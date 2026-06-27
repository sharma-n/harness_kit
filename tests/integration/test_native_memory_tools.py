"""Live tests: native memory tool suite.

Verifies that a real LLM calls list_facts, forget_fact, and recall in response
to natural-language requests. Facts are seeded directly into the store where
possible to remove flakiness from the remember step.
"""

from __future__ import annotations

import pytest

from agent_kit.agent.events import ToolCallStarted

from tests.integration.conftest import requires_live, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-memory-tools"


async def test_list_facts_called_when_asked(live_service, conv_id):
    await live_service.stores.profile.upsert_facts(USER_ID, {"color": "blue"})

    events = await run_turn(
        live_service, USER_ID, conv_id, "What facts do you have about me?"
    )

    names = [e.name for e in events if isinstance(e, ToolCallStarted)]
    assert "list_facts" in names, f"expected list_facts in tool calls, got {names}"


async def test_forget_fact_removes_from_store(live_service, conv_id):
    await live_service.stores.profile.upsert_facts(USER_ID, {"test_key": "test_value"})

    await run_turn(
        live_service, USER_ID, conv_id, "Please forget the fact called test_key."
    )
    await live_service.agent.drain()

    profile = await live_service.stores.profile.get(USER_ID)
    assert "test_key" not in profile.facts, (
        f"expected test_key to be removed, got facts={profile.facts}"
    )


async def test_recall_called_on_memory_search_request(live_service, conv_id):
    events = await run_turn(
        live_service,
        USER_ID,
        conv_id,
        "Search your memory for anything about my past travels.",
    )

    names = [e.name for e in events if isinstance(e, ToolCallStarted)]
    assert "recall" in names, f"expected recall in tool calls, got {names}"
