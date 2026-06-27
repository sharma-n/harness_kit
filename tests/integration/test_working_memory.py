"""Live tests: working memory rollover.

Uses a very small buffer_token_budget (50 tokens) so rollover fires after
1–2 turns regardless of message content. Asserts on store state directly —
no model text assertions.
"""

from __future__ import annotations

import pytest

from agent_kit.config import AgentKitConfig
from agent_kit.service import AgentService
from tests.conftest import FakeEmbedder
from tests.integration.conftest import requires_live, _load_live_cfg, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-working-memory"

_MSGS = [
    "Tell me about the history of the Roman Empire and its expansion across Europe.",
    "What were the main causes of the fall of the Western Roman Empire in 476 AD?",
    "How did Roman engineering influence modern infrastructure like roads and aqueducts?",
    "Describe the role of the Roman Senate during the height of the empire.",
]


@pytest.fixture
async def live_service_small_budget():
    cfg = _load_live_cfg(
        memory={
            "working": {
                "buffer_token_budget": 50,
                "idle_finalize_s": 30,
                "ttl_s": 60,
                "sweep_interval_s": 60,
            }
        }
    )
    service = AgentService.build(cfg, embedder=FakeEmbedder(dim=8))
    await service.astart()
    yield service
    await service.aclose()


async def test_rollover_triggered_by_small_budget(live_service_small_budget, conv_id):
    for msg in _MSGS:
        await run_turn(live_service_small_budget, USER_ID, conv_id, msg)

    await live_service_small_budget.agent.drain()

    state = await live_service_small_budget.stores.session.load(conv_id, USER_ID)
    assert state is not None
    assert state.rolling_summary != "", "expected rollover to produce a non-empty summary"


async def test_buffer_shrinks_after_rollover(live_service_small_budget, conv_id):
    for msg in _MSGS:
        await run_turn(live_service_small_budget, USER_ID, conv_id, msg)

    await live_service_small_budget.agent.drain()

    state = await live_service_small_budget.stores.session.load(conv_id, USER_ID)
    assert state is not None
    # Each turn appends user + assistant turns; 4 messages → 8 raw turns max.
    # After rollover at least one batch of old turns must have been evicted.
    assert len(state.working_buffer) < 8, (
        f"expected old turns evicted after rollover, buffer has {len(state.working_buffer)} turns"
    )
