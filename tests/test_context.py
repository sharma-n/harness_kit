"""Context assembly (golden) + budgeter tier-eviction tests (SPEC §6, §15)."""

from __future__ import annotations

import pytest
from llm_kit import Message, ToolDefinition
from llm_kit.messages.types import Role

from agent_kit.agent.budgeter import BudgetInputs, ContextBudgeter
from agent_kit.agent.context import ContextBuilder
from agent_kit.config import AgentConfig, ContextConfig
from agent_kit.errors import ContextOverflowError
from agent_kit.memory.working import WorkingSnapshot
from agent_kit.stores.types import MemoryHit, MemoryPoint, Turn, UserProfile


# --------------------------------------------------------------- assembly golden


class _StubWorking:
    def __init__(self, snapshot: WorkingSnapshot) -> None:
        self._snapshot = snapshot

    async def load(self, conversation_id: str, user_id: str) -> WorkingSnapshot:
        return self._snapshot


class _StubFactual:
    def __init__(self, profile: UserProfile) -> None:
        self._profile = profile

    async def get(self, user_id: str) -> UserProfile:
        return self._profile


class _StubEpisodic:
    def __init__(self, hits: list[MemoryHit]) -> None:
        self._hits = hits

    async def retrieve(self, user_id, message, recent_turns) -> list[MemoryHit]:
        return self._hits


class _StubRegistry:
    def __init__(self, defs: list[ToolDefinition]) -> None:
        self._defs = defs

    async def definitions(self, user_id) -> list[ToolDefinition]:
        return self._defs


def _hit(text: str, score: float) -> MemoryHit:
    return MemoryHit(
        point=MemoryPoint(id=text, vector=[], payload={"user_id": "u", "text": text}),
        score=score,
    )


async def test_assembly_matches_spec_6_6_order():
    """The §6.6 worked example: system(+factual+episodic+summary), buffer, current."""
    profile = UserProfile(user_id="u", facts={"name": "Sam", "seat": "aisle"})
    hits = [_hit("(2026-06-06) booked SFO->JFK, UA 4567, aisle.", 0.9)]
    snapshot = WorkingSnapshot(
        buffer=[
            Turn(role="user", text="I'm planning weekly NYC trips."),
            Turn(role="assistant", text="Got it, weekly trips to NYC."),
        ],
        summary="User is planning a recurring weekly NYC trip.",
    )
    builder = ContextBuilder(
        agent_cfg=AgentConfig(system_prompt="You are a travel agent."),
        working=_StubWorking(snapshot),
        episodic=_StubEpisodic(hits),
        factual=_StubFactual(profile),
        registry=_StubRegistry(
            [ToolDefinition(name="book_flight", description="book", parameters={})]
        ),
        budgeter=ContextBudgeter(ContextConfig()),
    )

    ctx = await builder.build("u", "c1", "Can you book the same flight as last week?")

    roles = [m.role for m in ctx.messages]
    assert roles == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER]

    system = ctx.messages[0].text
    assert system == (
        "You are a travel agent.\n\n"
        "What you know about this user:\n- name: Sam\n- seat: aisle\n\n"
        "Relevant memories from past conversations:\n"
        "- (2026-06-06) booked SFO->JFK, UA 4567, aisle.\n\n"
        "Summary of earlier in this conversation:\n"
        "User is planning a recurring weekly NYC trip."
    )
    assert ctx.messages[-1] == Message.user("Can you book the same flight as last week?")
    assert [d.name for d in ctx.tools] == ["book_flight"]
    assert ctx.episodic_hits == 1
    assert ctx.buffer_turns == 2


async def test_assembly_omits_empty_blocks():
    builder = ContextBuilder(
        agent_cfg=AgentConfig(system_prompt="Base."),
        working=_StubWorking(WorkingSnapshot(buffer=[], summary="")),
        episodic=_StubEpisodic([]),
        factual=_StubFactual(UserProfile(user_id="u")),
        registry=_StubRegistry([]),
        budgeter=ContextBudgeter(ContextConfig()),
    )
    ctx = await builder.build("u", "c1", "hello")
    assert ctx.messages[0].text == "Base."  # no factual/episodic/summary blocks
    assert [m.role for m in ctx.messages] == [Role.SYSTEM, Role.USER]


# --------------------------------------------------------------- budgeter tiers


def _small_budgeter(max_input: int) -> ContextBudgeter:
    # output_reserve + safety = 0 so the math is legible; estimator = len//4.
    cfg = ContextConfig(
        max_input_tokens=max_input, output_reserve_tokens=0, safety_margin=0
    )
    return ContextBudgeter(cfg)


def test_tier0_overflow_raises():
    budgeter = _small_budgeter(max_input=1)
    with pytest.raises(ContextOverflowError):
        budgeter.allocate(
            BudgetInputs(
                system_fixed="x" * 100,
                current_message="y" * 100,
                tool_text="",
                factual_block="",
                buffer=[],
                summary="",
                episodic=[],
            )
        )


def test_buffer_evicts_oldest_first():
    # budget tokens = 20 → 80 chars. tier0 uses 0. Each turn 'text'=8 chars → 2 tok.
    budgeter = _small_budgeter(max_input=6)
    buffer = [Turn(role="user", text=f"msg{i:05d}") for i in range(10)]  # 8 chars each
    result = budgeter.allocate(
        BudgetInputs(
            system_fixed="",
            current_message="",
            tool_text="",
            factual_block="",
            buffer=buffer,
            summary="",
            episodic=[],
        )
    )
    # 6 tokens / 2 per turn = 3 newest turns kept, oldest evicted.
    assert [t.text for t in result.buffer] == ["msg00007", "msg00008", "msg00009"]


def test_episodic_drops_lowest_score_first():
    budgeter = _small_budgeter(max_input=4)  # 4 tokens; each hit text = 8 chars = 2 tok
    hits = [_hit("aaaaaaaa", 0.2), _hit("bbbbbbbb", 0.9), _hit("cccccccc", 0.5)]
    result = budgeter.allocate(
        BudgetInputs(
            system_fixed="",
            current_message="",
            tool_text="",
            factual_block="",
            buffer=[],
            summary="",
            episodic=hits,
        )
    )
    kept = [h.point.payload["text"] for h in result.episodic]
    assert kept == ["bbbbbbbb", "cccccccc"]  # highest scores kept, 0.2 dropped
