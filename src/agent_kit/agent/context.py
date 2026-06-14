"""Context construction (SPEC §6) — the heart of the system.

Assembles the five sources into the provider message list, in the order that
matters for model attention (SPEC §6.2):

    [ system ]   identity + rules
                 ↳ FACTUAL block   (what we know about this user)
                 ↳ EPISODIC block  (relevant memories; only if hits clear threshold)
                 ↳ SUMMARY block   (earlier in this conversation)
    [ user/assistant … ]  working buffer, oldest→newest, verbatim
    [ user ]     the current message

All five sources are fetched for the calling ``user_id`` (profile, episodic
filtered by user, user-owned session); tools are the user's allowlist. The
builder is deterministic given its inputs, so the §6.6 worked example is a golden
test.
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_kit import Message, ToolDefinition

from agent_kit.agent.budgeter import BudgetInputs, ContextBudgeter
from agent_kit.config import AgentConfig
from agent_kit.memory.episodic import EpisodicMemory
from agent_kit.memory.factual import FactualMemory
from agent_kit.memory.working import WorkingMemory
from agent_kit.stores.types import MemoryHit, Turn, UserProfile
from agent_kit.tools.registry import ToolRegistry


@dataclass(slots=True)
class AssembledContext:
    messages: list[Message]
    tools: list[ToolDefinition]
    # diagnostics for observability / tests
    episodic_hits: int = 0
    buffer_turns: int = 0
    used_tokens: int = 0
    dropped_turns: int = 0
    rolled_summary: str = ""


@dataclass(slots=True)
class ContextBuilder:
    agent_cfg: AgentConfig
    working: WorkingMemory
    episodic: EpisodicMemory
    factual: FactualMemory
    registry: ToolRegistry
    budgeter: ContextBudgeter

    async def build(
        self, user_id: str, conversation_id: str, user_message: str
    ) -> AssembledContext:
        # --- gather the five sources, all scoped to this user ---
        snapshot = await self.working.load(conversation_id, user_id)
        profile = await self.factual.get(user_id)
        hits = await self.episodic.retrieve(user_id, user_message, snapshot.buffer)
        tools = await self.registry.definitions(user_id)

        # --- budget before assembly ---
        tool_text = " ".join(f"{t.name} {t.description}" for t in tools)
        budget = self.budgeter.allocate(
            BudgetInputs(
                system_fixed=self.agent_cfg.system_prompt,
                current_message=user_message,
                tool_text=tool_text,
                factual_block=_format_factual(profile),
                buffer=snapshot.buffer,
                summary=snapshot.summary,
                episodic=hits,
            )
        )

        # --- assemble in §6.2 order ---
        system_text = self._compose_system(
            _format_factual(profile),
            _format_episodic(budget.episodic),
            budget.summary,
        )
        messages: list[Message] = [Message.system(system_text)]
        messages.extend(_turn_to_message(t) for t in budget.buffer)
        messages.append(Message.user(user_message))

        dropped = len(snapshot.buffer) - len(budget.buffer)
        return AssembledContext(
            messages=messages,
            tools=tools,
            episodic_hits=len(budget.episodic),
            buffer_turns=len(budget.buffer),
            used_tokens=budget.used_tokens,
            dropped_turns=dropped,
            rolled_summary=budget.summary,
        )

    def _compose_system(self, factual: str, episodic: str, summary: str) -> str:
        parts = [self.agent_cfg.system_prompt]
        if factual:
            parts.append(factual)
        if episodic:
            parts.append(episodic)
        if summary:
            parts.append(f"Summary of earlier in this conversation:\n{summary}")
        return "\n\n".join(parts)


def _format_factual(profile: UserProfile) -> str:
    if not profile.facts:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in profile.facts.items())
    return f"What you know about this user:\n{lines}"


def _format_episodic(hits: list[MemoryHit]) -> str:
    if not hits:
        return ""
    lines = "\n".join(f"- {h.point.payload.get('text', '')}" for h in hits)
    return f"Relevant memories from past conversations:\n{lines}"


def _turn_to_message(turn: Turn) -> Message:
    if turn.role == "assistant":
        if turn.tool_calls:
            return Message.assistant_tool_calls(turn.tool_calls, text=turn.text or None)
        return Message.assistant(turn.text)
    if turn.role == "tool":
        return Message.tool_result(turn.tool_call_id or "", turn.text)
    return Message.user(turn.text)
