"""Metrics seam tests — all offline (no real prometheus_client / network).

Two postures:
  * disabled (the default): every record_* call is a no-op; metrics_output() returns
    empty bytes.
  * enabled with fake instruments installed via ``metrics._set_instruments_for_test``:
    a scripted turn through the real Agent asserts that each instrument was called with
    the expected values.
"""

from __future__ import annotations

import pytest

from harness_kit import metrics
from harness_kit.config import HarnessKitConfig

from tests.conftest import ScriptedTurn, make_service, tc


# --------------------------------------------------------------------------- #
# Fake instruments — lightweight doubles that record observe/inc calls.
# --------------------------------------------------------------------------- #
class FakeHistogram:
    def __init__(self) -> None:
        self.observations: list[float] = []

    def observe(self, value: float) -> None:
        self.observations.append(value)


class FakeCounter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._labels: dict = {}

    def labels(self, **kwargs) -> "_FakeCounterChild":
        return _FakeCounterChild(self, kwargs)


class _FakeCounterChild:
    def __init__(self, parent: FakeCounter, labels: dict) -> None:
        self._parent = parent
        self._labels = labels

    def inc(self) -> None:
        self._parent.calls.append(self._labels)


def _make_fake_instruments() -> dict:
    return {
        "ttft": FakeHistogram(),
        "turn_latency": FakeHistogram(),
        "turn_iterations": FakeHistogram(),
        "tool_calls": FakeCounter(),
        "retrieval_hits": FakeHistogram(),
    }


@pytest.fixture
def fake_instruments():
    inst = _make_fake_instruments()
    metrics._set_instruments_for_test(inst)
    try:
        yield inst
    finally:
        metrics._set_instruments_for_test(None)


# --------------------------------------------------------------------------- #
# Disabled posture
# --------------------------------------------------------------------------- #
def test_disabled_seam_is_noop():
    metrics._set_instruments_for_test(None)
    assert metrics.is_enabled() is False
    # None of these should raise.
    metrics.record_ttft(0.1)
    metrics.record_turn(1.0, 2)
    metrics.record_tool_call("some_tool", "ok")
    metrics.record_retrieval(3)
    body, ct = metrics.metrics_output()
    assert body == b""
    assert ct == ""


# --------------------------------------------------------------------------- #
# Enabled posture — individual record functions
# --------------------------------------------------------------------------- #
def test_record_ttft(fake_instruments):
    metrics.record_ttft(0.25)
    assert fake_instruments["ttft"].observations == [0.25]


def test_record_turn(fake_instruments):
    metrics.record_turn(3.5, 2)
    assert fake_instruments["turn_latency"].observations == [3.5]
    assert fake_instruments["turn_iterations"].observations == [2]


def test_record_tool_call(fake_instruments):
    metrics.record_tool_call("remember_fact", "ok")
    metrics.record_tool_call("bad_tool", "not_permitted")
    assert {"tool": "remember_fact", "outcome": "ok"} in fake_instruments["tool_calls"].calls
    assert {"tool": "bad_tool", "outcome": "not_permitted"} in fake_instruments["tool_calls"].calls


def test_record_retrieval(fake_instruments):
    metrics.record_retrieval(2)
    assert fake_instruments["retrieval_hits"].observations == [2]


# --------------------------------------------------------------------------- #
# metrics_output() when enabled — fake instruments don't produce Prometheus text,
# but we can verify is_enabled() and that the seam is wired correctly.
# --------------------------------------------------------------------------- #
def test_is_enabled_with_fake(fake_instruments):
    assert metrics.is_enabled() is True


# --------------------------------------------------------------------------- #
# Integration: one scripted turn drives the instrumentation call sites
# --------------------------------------------------------------------------- #
async def test_turn_instruments_called(fake_instruments):
    """A plain text turn: TTFT + turn latency + iterations + retrieval are recorded."""
    cfg = HarnessKitConfig()
    service, _ = make_service(cfg, turns=[ScriptedTurn(text_chunks=["hello", " world"])])

    events = [e async for e in service.agent.run_turn("u1", "conv1", "hi")]
    assert [type(e).__name__ for e in events][-1] == "TurnComplete"

    # TTFT recorded once (first text chunk of the first iteration).
    assert len(fake_instruments["ttft"].observations) == 1
    assert fake_instruments["ttft"].observations[0] > 0

    # Turn latency and iterations recorded.
    assert len(fake_instruments["turn_latency"].observations) == 1
    assert fake_instruments["turn_latency"].observations[0] > 0
    assert fake_instruments["turn_iterations"].observations == [1]

    # Episodic retrieve was called → retrieval_hits recorded.
    assert len(fake_instruments["retrieval_hits"].observations) == 1


async def test_tool_call_outcome_recorded(fake_instruments):
    """A tool-call turn: tool_calls counter captures the tool name and outcome."""
    cfg = HarnessKitConfig()
    cfg.tools.default_allowed = ["list_facts"]
    service, _ = make_service(
        cfg,
        turns=[
            ScriptedTurn(tool_calls=[tc("c1", "list_facts")]),
            ScriptedTurn(text_chunks=["done"]),
        ],
    )

    events = [e async for e in service.agent.run_turn("u1", "conv1", "what do you know?")]
    assert [type(e).__name__ for e in events][-1] == "TurnComplete"

    assert {"tool": "list_facts", "outcome": "ok"} in fake_instruments["tool_calls"].calls


async def test_denied_tool_outcome_recorded(fake_instruments):
    """A denied tool call: tool_calls counter captures 'not_permitted' outcome."""
    cfg = HarnessKitConfig()
    # list_facts is NOT in the allowlist — will be denied.
    service, _ = make_service(
        cfg,
        turns=[
            ScriptedTurn(tool_calls=[tc("c1", "list_facts")]),
            ScriptedTurn(text_chunks=["ok"]),
        ],
    )

    events = [e async for e in service.agent.run_turn("u1", "conv1", "hi")]
    assert [type(e).__name__ for e in events][-1] == "TurnComplete"

    assert {"tool": "list_facts", "outcome": "not_permitted"} in fake_instruments["tool_calls"].calls
