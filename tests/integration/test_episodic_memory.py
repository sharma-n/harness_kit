"""Live tests: episodic memory write plumbing.

Verifies that end_conversation embeds the conversation as a single vector
point in the store. Uses FakeEmbedder so the embed endpoint is not called;
the point write plumbing and store state are what matter here.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import requires_live, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-episodic"


async def test_one_point_written_after_end_conversation(live_service, conv_id):
    await run_turn(live_service, USER_ID, conv_id, "I work as a nurse in Denver.")
    await live_service.agent.end_conversation(USER_ID, conv_id)

    points = [
        p
        for p in live_service.stores.vectors._points.values()
        if p.payload.get("user_id") == USER_ID
    ]
    assert len(points) == 1, f"expected exactly one episodic point, got {len(points)}"


async def test_episodic_point_has_correct_metadata(live_service, conv_id):
    await run_turn(live_service, USER_ID, conv_id, "I work as a nurse in Denver.")
    await live_service.agent.end_conversation(USER_ID, conv_id)

    points = [
        p
        for p in live_service.stores.vectors._points.values()
        if p.payload.get("user_id") == USER_ID
    ]
    assert points, "no episodic points found"
    point = points[0]
    assert point.payload.get("user_id") == USER_ID
    assert point.payload.get("text"), "expected non-empty text in episodic point payload"


async def test_episodic_point_id_is_deterministic(live_service, conv_id):
    """Re-finalizing the same conversation must upsert, not duplicate."""
    await run_turn(live_service, USER_ID, conv_id, "I work as a nurse in Denver.")
    await live_service.agent.end_conversation(USER_ID, conv_id)
    first_count = sum(
        1
        for p in live_service.stores.vectors._points.values()
        if p.payload.get("user_id") == USER_ID
    )

    # Simulate resumed conversation: run a new turn (clears finalized_at), then re-finalize.
    await run_turn(live_service, USER_ID, conv_id, "Actually I just moved to Seattle.")
    await live_service.agent.end_conversation(USER_ID, conv_id)
    second_count = sum(
        1
        for p in live_service.stores.vectors._points.values()
        if p.payload.get("user_id") == USER_ID
    )

    assert second_count == first_count, (
        f"re-finalizing should upsert (same count), got {first_count} then {second_count}"
    )
