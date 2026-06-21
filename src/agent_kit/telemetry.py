"""Vendor-neutral tracing seam — the one module that imports ``langfuse``.

A leaf utility (like ``tokens.py`` / ``retry.py`` / ``errors.py``): any layer may
import it without violating the bottom-up layering rule, because it imports nothing
from the package above ``config``. Every other layer calls *this* API, never
``langfuse`` directly — so swapping to pure OpenTelemetry (or any OTLP backend)
later means reimplementing this one file, not re-instrumenting the call sites.

No-op until ``configure`` enables it. With telemetry disabled (the default), every
helper is a trivial null context manager / no-op, so the default test suite stays
offline and deterministic and time-to-first-token is untouched.

Langfuse v4 is built on OpenTelemetry, so trace context propagates across ``await``
boundaries and ``asyncio.create_task`` (which copies contextvars) without manual
trace-id threading — that is what keeps the fire-and-forget background memory writes
attached to the turn's trace.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_kit.llm.response import TokenUsage

    from agent_kit.config import TelemetryConfig

logger = logging.getLogger(__name__)

# The Langfuse client (or a test double) when enabled; ``None`` means disabled → no-op.
_client: Any = None


def configure(cfg: TelemetryConfig) -> None:
    """Enable tracing from config. Idempotent; a no-op when ``cfg.enabled`` is false.

    Credentials are read from the environment by the SDK (``LANGFUSE_PUBLIC_KEY`` /
    ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``). If the ``telemetry`` extra is not
    installed, we log once and stay disabled rather than crash the service.
    """
    global _client
    if _client is not None or not cfg.enabled:
        return
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning(
            "telemetry enabled but 'langfuse' is not installed; install the "
            "'telemetry' extra (uv sync --extra telemetry). Tracing disabled."
        )
        return
    _client = Langfuse(
        environment=cfg.environment or None,
        release=cfg.release or None,
        sample_rate=cfg.sample_rate,
        tracing_enabled=True,
    )
    logger.info(
        "telemetry configured (langfuse): service=%s sample_rate=%s",
        cfg.service_name,
        cfg.sample_rate,
    )


def is_enabled() -> bool:
    return _client is not None


def flush() -> None:
    """Force-export buffered spans (call on request end so short-lived turns ship)."""
    if _client is not None:
        _client.flush()


def shutdown() -> None:
    """Flush and tear down the exporter; wired into the service cleanup closure."""
    global _client
    if _client is not None:
        try:
            _client.shutdown()
        finally:
            _client = None


# --------------------------------------------------------------------------- #
# Span handle — the only object call sites touch. No-ops when ``_obj is None``.
# --------------------------------------------------------------------------- #
class SpanHandle:
    """Thin wrapper over a Langfuse observation; every method no-ops when disabled."""

    __slots__ = ("_obj",)

    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def set_attributes(self, **attrs: Any) -> None:
        if self._obj is not None:
            cleaned = _clean(attrs)
            if cleaned:
                self._obj.update(metadata=cleaned)

    def set_output(self, output: Any) -> None:
        if self._obj is not None:
            self._obj.update(output=output)

    def set_error(self, exc: BaseException) -> None:
        if self._obj is not None:
            self._obj.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")

    def record_generation(
        self, *, model: str | None = None, usage: TokenUsage | None = None, output: Any = None
    ) -> None:
        """Stamp a generation/embedding observation with model + token usage so
        Langfuse prices it from its model tables (covers the M9 cost goal)."""
        if self._obj is not None:
            self._obj.update(
                model=model, usage_details=_usage_details(usage), output=output
            )

    def end(self) -> None:
        """End a manually-started observation (used by the streaming generation)."""
        if self._obj is not None:
            self._obj.end()


_NULL = SpanHandle(None)


@contextmanager
def span(name: str, *, kind: str = "span", input: Any = None, **attrs: Any) -> Iterator[SpanHandle]:
    """Open a child observation under the active trace. ``kind`` maps to the Langfuse
    observation type (``span`` / ``tool`` / ``generation`` / ``embedding`` / …)."""
    if _client is None:
        yield _NULL
        return
    with _client.start_as_current_observation(
        name=name, as_type=kind, input=input, metadata=_clean(attrs) or None
    ) as obj:
        handle = SpanHandle(obj)
        try:
            yield handle
        except BaseException as exc:
            handle.set_error(exc)
            raise


@contextmanager
def turn_span(
    name: str, *, user_id: str, conversation_id: str, input: Any = None, **attrs: Any
) -> Iterator[SpanHandle]:
    """Open a root span for a turn/conversation-end and stamp trace-level identity:
    ``user_id`` → Langfuse user, ``conversation_id`` → Langfuse session (so a whole
    conversation groups in the Sessions view). Propagated to every child span."""
    if _client is None:
        yield _NULL
        return
    with _client.start_as_current_observation(
        name=name, as_type="span", input=input, metadata=_clean(attrs) or None
    ) as obj, _propagate(user_id=user_id, session_id=conversation_id):
        handle = SpanHandle(obj)
        try:
            yield handle
        except BaseException as exc:
            handle.set_error(exc)
            raise


def start_generation(name: str, *, kind: str = "generation", input: Any = None) -> SpanHandle:
    """Manually start a generation/embedding observation (caller must ``end()`` it).

    Used by the streaming LLM wrapper, where holding a context manager open across
    ``yield`` boundaries would shuffle the OTel current-span contextvar; a non-current
    child observation parents under the active span without that hazard."""
    if _client is None:
        return _NULL
    return SpanHandle(_client.start_observation(name=name, as_type=kind, input=input))


def _propagate(*, user_id: str, conversation_id: str = "", session_id: str = "") -> Any:
    """Return the ``propagate_attributes`` context manager (real, or a test double's)."""
    sid = session_id or conversation_id
    prop = getattr(_client, "propagate_attributes", None)
    if prop is None:
        from langfuse import propagate_attributes as prop  # type: ignore[no-redef]
    return prop(user_id=user_id, session_id=sid)


def _usage_details(usage: TokenUsage | None) -> dict[str, int] | None:
    """Map llm_kit ``TokenUsage`` onto Langfuse usage keys (input/output/total)."""
    if usage is None:
        return None
    details: dict[str, int] = {}
    if getattr(usage, "prompt_tokens", 0):
        details["input"] = usage.prompt_tokens
    if getattr(usage, "completion_tokens", 0):
        details["output"] = usage.completion_tokens
    if getattr(usage, "total_tokens", 0):
        details["total"] = usage.total_tokens
    return details or None


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None}


def _set_client_for_test(client: Any) -> None:
    """Test seam: install a recording double (or ``None`` to disable)."""
    global _client
    _client = client
