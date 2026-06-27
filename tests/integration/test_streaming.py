"""Live tests: basic streaming contract.

Verifies that a real LLM produces valid AgentEvent sequences and reports
non-zero token usage. These are protocol invariants — not content-dependent.
"""

from __future__ import annotations

import pytest

from agent_kit.agent.events import TextDelta, TurnComplete

from tests.integration.conftest import requires_live, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-streaming"


async def test_text_deltas_precede_turn_complete(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, "What is 2 + 2?")

    types = [type(e).__name__ for e in events]
    assert "TextDelta" in types, "expected at least one TextDelta"
    assert types[-1] == "TurnComplete", "TurnComplete must be the last event"

    last_delta_idx = max(i for i, t in enumerate(types) if t == "TextDelta")
    complete_idx = types.index("TurnComplete")
    assert last_delta_idx < complete_idx


async def test_turn_complete_has_positive_token_usage(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, "What is 2 + 2?")

    turn_complete = next(e for e in events if isinstance(e, TurnComplete))
    assert turn_complete.usage.prompt_tokens > 0
    assert turn_complete.usage.completion_tokens > 0


async def test_concatenated_text_is_nonempty(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, "What is 2 + 2?")

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert len(text) > 0


async def test_stop_reason_is_completed_for_plain_answer(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, "Say hello.")

    turn_complete = next(e for e in events if isinstance(e, TurnComplete))
    assert turn_complete.stop_reason == "completed"
