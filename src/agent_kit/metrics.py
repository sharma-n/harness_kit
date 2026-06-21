"""Prometheus metrics seam — the one module that imports ``prometheus_client``.

A leaf utility (like ``tokens.py`` / ``retry.py`` / ``telemetry.py``): any layer
may import it without violating the bottom-up layering rule. Every call site uses
the thin ``record_*`` functions here, never ``prometheus_client`` types directly —
so swapping the metrics backend later means reimplementing this one file.

No-op until ``configure`` enables it. With metrics disabled (the default), every
``record_*`` is a fast branch-on-None and returns immediately, so the default test
suite stays offline and deterministic and TTFT is untouched.

Instruments (all prefixed ``agent_kit_``):
  ttft_seconds          Histogram  time from run_turn entry to first TextDelta
  turn_latency_seconds  Histogram  full turn wall time to TurnComplete
  turn_iterations       Histogram  outer LLM-call loop count per turn
  tool_calls_total      Counter    per tool × outcome label
  retrieval_hits        Histogram  episodic hits returned per retrieve() call
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_kit.config import MetricsConfig

logger = logging.getLogger(__name__)

# Instrument bundle — None means disabled → every record_* is a no-op.
_instruments: dict[str, Any] | None = None


def configure(cfg: MetricsConfig) -> None:
    """Enable metrics from config. Idempotent; a no-op when ``cfg.enabled`` is false.

    The ``prometheus_client`` registry is global-process, so calling this more than
    once would re-register the same metric names and raise. The ``if _instruments``
    guard makes it safe to call from tests or multiple build() calls.
    """
    global _instruments
    if _instruments is not None or not cfg.enabled:
        return
    try:
        from prometheus_client import Counter, Histogram
    except ImportError:
        logger.warning(
            "metrics enabled but 'prometheus-client' is not installed; install the "
            "'metrics' extra (uv sync --extra metrics). Metrics disabled."
        )
        return

    _instruments = {
        "ttft": Histogram(
            "agent_kit_ttft_seconds",
            "Time from run_turn entry to first TextDelta",
            buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
        ),
        "turn_latency": Histogram(
            "agent_kit_turn_latency_seconds",
            "Full turn wall time to TurnComplete",
            buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
        ),
        "turn_iterations": Histogram(
            "agent_kit_turn_iterations",
            "Outer LLM-call loop count per turn",
            buckets=(1, 2, 3, 4, 5, 6),
        ),
        "tool_calls": Counter(
            "agent_kit_tool_calls_total",
            "Tool executions by tool name and outcome",
            labelnames=["tool", "outcome"],
        ),
        "retrieval_hits": Histogram(
            "agent_kit_retrieval_hits",
            "Episodic hits returned per retrieve() call",
            buckets=(0, 1, 2, 3, 4, 5),
        ),
    }
    logger.info("metrics configured (prometheus_client)")


def is_enabled() -> bool:
    return _instruments is not None


def metrics_output() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the ``/metrics`` route.

    Returns ``(b"", "")`` when metrics are disabled — the route returns 501 in
    that case so operators know they need to enable the ``metrics`` extra.
    """
    if _instruments is None:
        return b"", ""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(), CONTENT_TYPE_LATEST


# --------------------------------------------------------------------------- #
# Record functions — called by loop.py, registry.py, episodic.py
# --------------------------------------------------------------------------- #

def record_ttft(seconds: float) -> None:
    if _instruments is None:
        return
    _instruments["ttft"].observe(seconds)


def record_turn(seconds: float, iterations: int) -> None:
    if _instruments is None:
        return
    _instruments["turn_latency"].observe(seconds)
    _instruments["turn_iterations"].observe(iterations)


def record_tool_call(tool_name: str, outcome: str) -> None:
    if _instruments is None:
        return
    _instruments["tool_calls"].labels(tool=tool_name, outcome=outcome).inc()


def record_retrieval(hit_count: int) -> None:
    if _instruments is None:
        return
    _instruments["retrieval_hits"].observe(hit_count)


def _set_instruments_for_test(instruments: dict[str, Any] | None) -> None:
    """Test seam: install fake instruments (or ``None`` to disable)."""
    global _instruments
    _instruments = instruments
