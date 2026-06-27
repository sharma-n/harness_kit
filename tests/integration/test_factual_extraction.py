"""Live tests: post-turn factual extraction.

Verifies that the LLM extracts durable facts from conversation turns and that
ephemeral context is not stored. Assertions are lenient — we check non-empty /
empty rather than exact keys, since the model chooses the key names.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import requires_live, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-factual"


async def test_extraction_produces_at_least_one_fact(live_service, conv_id):
    await run_turn(
        live_service,
        USER_ID,
        conv_id,
        "My name is Jordan and I'm a software engineer in Austin, Texas.",
    )
    await live_service.agent.drain()

    profile = await live_service.stores.profile.get(USER_ID)
    assert profile.facts, (
        "expected at least one fact extracted from a message with name + occupation"
    )


async def test_ephemeral_context_not_stored(live_service, conv_id):
    await run_turn(
        live_service,
        USER_ID,
        conv_id,
        "I am currently waiting at the airport for my flight.",
    )
    await live_service.agent.drain()

    profile = await live_service.stores.profile.get(USER_ID)
    # The extraction system prompt says "omit anything ephemeral."
    # A transient location ("airport", "flight") should not be stored as a durable fact.
    assert not profile.facts, (
        f"expected no facts for ephemeral context, got {profile.facts}"
    )
