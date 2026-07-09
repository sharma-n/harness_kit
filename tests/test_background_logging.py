"""Background-write failures must never be silent.

Covers the two logging surfaces reworked alongside store-write retry:
  - the ``_guard`` choke point logs exactly one ERROR (with context) when an enqueued
    background write exhausts its retries;
  - ``sweep_idle`` logs a per-conversation WARNING on failure and keeps sweeping the
    rest (isolation preserved, no longer suppressed).
"""

from __future__ import annotations

import logging

import pytest

from harness_kit.config import HarnessKitConfig
from harness_kit.memory.factual import ExtractedFacts
from harness_kit.stores.types import Turn

from tests.conftest import ScriptedTurn, make_service
from tests.test_memory_retry import FlakyProfileStore


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr("harness_kit.retry.asyncio.sleep", _instant)


async def _run(agent, user="alice", convo="c1", msg="hi"):
    async for _ in agent.run_turn(user, convo, msg):
        pass
    await agent.drain()


async def test_choke_point_logs_one_error_on_exhausted_background_write(caplog):
    base = HarnessKitConfig()
    service, _ = make_service(
        base,
        turns=[ScriptedTurn(text_chunks=["sure"])],
        invoke_parsed=ExtractedFacts(facts={"diet": "vegetarian"}),
    )
    # Make the factual store write fail permanently → extract exhausts its retries.
    service.agent._factual._store = FlakyProfileStore(fail_times=99)

    with caplog.at_level(logging.ERROR, logger="harness_kit.agent.loop"):
        await _run(service.agent, msg="I'm vegetarian")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "factual.extract" in msg
    assert "alice" in msg and "c1" in msg


async def test_sweep_idle_logs_failure_and_continues(caplog):
    base = HarnessKitConfig()
    service, _ = make_service(
        base,
        turns=[ScriptedTurn(text_chunks=["one"]), ScriptedTurn(text_chunks=["two"])],
    )
    await _run(service.agent, convo="c1", msg="first")
    await _run(service.agent, convo="c2", msg="second")

    # Age both so they are due for finalize.
    for convo in ("c1", "c2"):
        state = await service.stores.session.load(convo, "alice")
        state.updated_at -= 1000

    # c1 finalize blows up; c2 must still be swept.
    orig = service.agent.end_conversation

    async def flaky_end(user_id, conversation_id):
        if conversation_id == "c1":
            raise ConnectionError("finalize boom")
        await orig(user_id, conversation_id)

    service.agent.end_conversation = flaky_end

    with caplog.at_level(logging.WARNING, logger="harness_kit.agent.loop"):
        await service.agent.sweep_idle(idle_finalize_s=900)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("c1" in r.getMessage() and "idle finalize failed" in r.getMessage()
               for r in warnings)
    # c2 was still finalized despite c1 failing.
    c2_points = [
        p for p in service.stores.vectors._points.values()
        if p.payload["conversation_id"] == "c2"
    ]
    assert len(c2_points) == 1
