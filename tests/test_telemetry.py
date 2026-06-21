"""Telemetry seam tests — all offline (no real Langfuse client / network).

Two postures:
  * disabled (the default): the seam is a no-op and ``TracingLLM`` passes the stream
    through byte-for-byte, so the rest of the suite is unaffected.
  * enabled with a recording double installed via ``telemetry._set_client_for_test``:
    one scripted turn produces the expected span tree (names / types / nesting /
    trace identity), driven through the real Agent + stores.
"""

from __future__ import annotations

import pytest
from llm_kit import Message

from agent_kit import telemetry
from agent_kit.config import AgentKitConfig
from agent_kit.llm import TracingEmbedder, TracingLLM

from tests.conftest import FakeEmbedder, FakeLLM, ScriptedTurn, make_service, tc


# --------------------------------------------------------------------------- #
# Recording double: models start_as_current_observation (current, stack-based),
# start_observation (child of current, not made current), and propagate_attributes.
# --------------------------------------------------------------------------- #
class RecordingSpan:
    def __init__(self, name: str, as_type: str, parent: "RecordingSpan | None") -> None:
        self.name = name
        self.as_type = as_type
        self.parent = parent
        self.metadata: dict = {}
        self.fields: dict = {}
        self.ended = False

    def update(self, *, metadata=None, **fields):
        if metadata:
            self.metadata.update(metadata)
        self.fields.update(fields)
        return self

    def end(self, **_kw):
        self.ended = True
        return self


class _CurrentCM:
    def __init__(self, recorder: "Recorder", span: RecordingSpan) -> None:
        self._recorder = recorder
        self._span = span

    def __enter__(self) -> RecordingSpan:
        self._recorder.stack.append(self._span)
        return self._span

    def __exit__(self, *exc) -> bool:
        # Remove by identity so concurrent background tasks can't pop each other's
        # span (parenting under concurrency isn't asserted; balance is what matters).
        try:
            self._recorder.stack.remove(self._span)
        except ValueError:
            pass
        self._span.ended = True
        return False


class _NullCM:
    def __enter__(self):
        return None

    def __exit__(self, *exc) -> bool:
        return False


class Recorder:
    def __init__(self) -> None:
        self.spans: list[RecordingSpan] = []
        self.stack: list[RecordingSpan] = []
        self.trace_user_id: str | None = None
        self.trace_session_id: str | None = None
        self.flushed = 0

    def _new(self, name: str, as_type: str, input, metadata) -> RecordingSpan:
        parent = self.stack[-1] if self.stack else None
        span = RecordingSpan(name, as_type, parent)
        if input is not None:
            span.fields["input"] = input
        if metadata:
            span.metadata.update(metadata)
        self.spans.append(span)
        return span

    def start_as_current_observation(self, *, name, as_type="span", input=None, metadata=None, **_kw):
        return _CurrentCM(self, self._new(name, as_type, input, metadata))

    def start_observation(self, *, name, as_type="span", input=None, metadata=None, **_kw):
        return self._new(name, as_type, input, metadata)  # child of current, not pushed

    def propagate_attributes(self, *, user_id=None, session_id=None, **_kw):
        self.trace_user_id = user_id
        self.trace_session_id = session_id
        return _NullCM()

    def flush(self) -> None:
        self.flushed += 1

    def shutdown(self) -> None:
        pass

    def by_name(self, name: str) -> list[RecordingSpan]:
        return [s for s in self.spans if s.name == name]


@pytest.fixture
def recorder():
    rec = Recorder()
    telemetry._set_client_for_test(rec)
    try:
        yield rec
    finally:
        telemetry._set_client_for_test(None)


# --------------------------------------------------------------------------- #
# Disabled posture
# --------------------------------------------------------------------------- #
def test_disabled_seam_is_noop():
    telemetry._set_client_for_test(None)
    assert telemetry.is_enabled() is False
    with telemetry.span("x", kind="tool", foo=1) as h:
        h.set_attributes(a=1)
        h.set_output("o")
        h.set_error(ValueError("boom"))
        h.record_generation(model="m")
        h.end()
    with telemetry.turn_span("turn", user_id="u", conversation_id="c") as h:
        h.set_attributes(b=2)
    telemetry.flush()
    telemetry.shutdown()  # all no-ops, no raise


async def test_tracing_llm_passthrough_when_disabled():
    telemetry._set_client_for_test(None)
    script = [ScriptedTurn(text_chunks=["hel", "lo"])]
    wrapped = TracingLLM(FakeLLM(turns=script), model="m")
    got = [type(e).__name__ async for e in wrapped.invoke_stream([Message.user("hi")])]
    baseline = [type(e).__name__ async for e in FakeLLM(turns=script).invoke_stream([Message.user("hi")])]
    assert got == baseline
    assert got[:2] == ["TextChunk", "TextChunk"]
    assert got[-1] == "StreamEnd"


async def test_tracing_embedder_passthrough_when_disabled():
    telemetry._set_client_for_test(None)
    wrapped = TracingEmbedder(FakeEmbedder(), model="e")
    resp = await wrapped.embed_one("hello")
    assert resp.vector == (await FakeEmbedder().embed_one("hello")).vector


# --------------------------------------------------------------------------- #
# Enabled posture — span tree for one scripted turn + conversation end
# --------------------------------------------------------------------------- #
async def test_turn_span_tree(recorder: Recorder):
    cfg = AgentKitConfig()
    cfg.tools.default_allowed = ["list_facts"]
    # tool-call turn (list_facts now allowed), then the answer turn.
    service, _ = make_service(
        cfg,
        turns=[
            ScriptedTurn(tool_calls=[tc("c1", "list_facts")]),
            ScriptedTurn(text_chunks=["done"]),
        ],
    )
    assert telemetry.is_enabled() is True  # recorder installed before build → wrapped

    events = [e async for e in service.agent.run_turn("u1", "conv1", "hello there")]
    assert [type(e).__name__ for e in events][-1] == "TurnComplete"
    await service.agent.drain()  # let background extract/rollover spans finish
    await service.agent.end_conversation("u1", "conv1")

    names = {s.name for s in recorder.spans}

    # Root + identity propagated to the trace.
    turn = recorder.by_name("turn")
    assert len(turn) == 1 and turn[0].as_type == "span"
    assert recorder.trace_user_id == "u1"
    assert recorder.trace_session_id == "conv1"
    assert turn[0].fields.get("input") == "hello there"
    assert turn[0].metadata.get("stop_reason") == "completed"

    # context.build under the turn; the four source reads under context.build.
    ctx = recorder.by_name("context.build")[0]
    assert ctx.parent is turn[0]
    for child in ("memory.working.load", "memory.factual.get", "memory.episodic.retrieve", "tools.definitions"):
        assert recorder.by_name(child)[0].parent is ctx

    # The retrieve embedding nests under episodic.retrieve.
    retrieve = recorder.by_name("memory.episodic.retrieve")[0]
    assert any(s.name == "embed_one" and s.parent is retrieve for s in recorder.spans)

    # Two LLM generations (tool-call turn + answer turn), parented to the turn.
    gens = recorder.by_name("llm.invoke_stream")
    assert len(gens) == 2
    assert all(g.as_type == "generation" and g.parent is turn[0] for g in gens)

    # The tool execution span, with outcome attributes.
    tool = recorder.by_name("tool.execute:list_facts")[0]
    assert tool.as_type == "tool" and tool.parent is turn[0]
    assert tool.metadata.get("ok") is True
    assert tool.metadata.get("outcome") == "ok"

    # Background writes (after drain) + conversation-end subtree are present.
    assert "working.rollover" in names
    assert "factual.extract" in names
    assert "conversation_end" in names
    assert "memory.episodic.write_conversation" in names
    assert "memory.working.mark_finalized" in names


def test_loader_coerces_env_bool():
    # ``${VAR:-false}`` interpolates to the string "false"; without coercion it would
    # be truthy. Confirm the loader turns it into a real bool for the enabled flag.
    from agent_kit.config import TelemetryConfig
    from agent_kit.config.loader import _coerce

    assert _coerce(bool, "false") is False
    assert _coerce(bool, "true") is True
    cfg = AgentKitConfig.from_dict({"telemetry": {"enabled": "false"}})
    assert cfg.telemetry.enabled is False
    assert isinstance(cfg.telemetry, TelemetryConfig)
