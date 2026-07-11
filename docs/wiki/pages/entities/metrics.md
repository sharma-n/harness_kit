---
title: Metrics
category: entity
tags: [observability, prometheus, metrics, monitoring, instrumentation]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/metrics.py, CLAUDE.md#Telemetry / tracing (Langfuse), ROADMAP.md#Observability]
status: current
---

# Metrics

`metrics.py` is a vendor-neutral seam over Prometheus. Like `telemetry.py`, it's a leaf module that imports `prometheus_client` only; all other layers call thin `record_*` functions here, so swapping metrics backends means reimplementing one file.

## No-Op by Default

Controlled by `MetricsConfig.enabled` (default false). When disabled:

- Every `record_*()` call is a fast branch-on-None and returns immediately.
- No Prometheus registry pollution.
- No performance overhead; time-to-first-token is untouched.

## Enabling Metrics

```python
from harness_kit import metrics
from harness_kit.config import MetricsConfig

cfg = MetricsConfig(enabled=True)
metrics.configure(cfg)
```

Optional `metrics` extra; if not installed and enabled, logs a warning and stays disabled.

## Instruments

All instrument names are prefixed `harness_kit_`:

**Histograms** (bucketed observations):

- **`ttft_seconds`** — Time from `run_turn()` entry to first `TextDelta` yielded. Buckets: 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0 seconds. Measures perceived latency.

- **`turn_latency_seconds`** — Full turn wall time from entry to `TurnComplete`. Includes all iterations, context assembly, tool calls. Buckets: 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0 seconds.

- **`turn_iterations`** — Number of LLM invocations per turn (iterations of the [[pages/entities/agent-loop|agent loop]]). Buckets: 1, 2, 3, 4, 5. Most turns are 1-2; high values indicate repeated tool calls.

- **`retrieval_hits`** — Hits returned per `EpisodicMemory.retrieve()` call. Buckets: 1, 2, 4, 8, 16. Monitors vector search efficiency.

**Counters**:

- **`tool_calls_total`** — Tool invocation count with labels:
  - `tool` — tool name
  - `outcome` — one of: `ok`, `denied` (permission), `rate_limited`, `timeout`, `error`
  
  Enables monitoring per-tool success rates and failure modes.

## Serving Endpoint

`GET /metrics` — Prometheus text-format output (when metrics enabled).

- **When enabled:** Returns 200 with Prometheus text-format metrics (compatible with Prometheus scrape).
- **When disabled:** Returns 501 JSON `{"status":"not_implemented"}`.

A Prometheus scraper polls this endpoint every 15-60 seconds (configurable in Prometheus). The metrics are point-in-time snapshots of the process state.

## Recording

Call sites use functions like:

```python
record_ttft_seconds(elapsed)
record_turn_latency_seconds(elapsed)
record_tool_call(tool_name, outcome)
record_retrieval_hits(len(results))
```

These are thin wrappers that check if `_instruments is not None` (enabled) before calling the underlying Histogram/Counter methods. In the disabled case, they return immediately.

## Integration

- `Agent.run_turn()` records TTFT and turn latency.
- `ToolRegistry.execute()` records per-tool call outcomes.
- `EpisodicMemory.retrieve()` records retrieval hit counts.
- Spans in [[pages/entities/telemetry]] may also record Langfuse-side token usage and cost.

## Process-Level Registry

Prometheus's `prometheus_client` uses a process-level global registry. Multiple calls to `configure()` from tests would try to re-register the same metric names and raise. The `if _instruments` guard makes subsequent calls safe (idempotent).
