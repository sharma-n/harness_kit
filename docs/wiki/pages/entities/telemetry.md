---
title: Telemetry & Tracing
category: entity
tags: [observability, tracing, langfuse, spans, otel]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/telemetry.py, CLAUDE.md#Telemetry / tracing (Langfuse), ROADMAP.md#Observability]
status: current
---

# Telemetry & Tracing

`telemetry.py` is a vendor-neutral seam over Langfuse (which is built on OpenTelemetry). It's the only module in harness_kit that imports `langfuse`; all other layers call this module's API, making it possible to swap backends by reimplementing one file.

## No-Op by Default

Controlled by `TelemetryConfig.enabled` (default false). When disabled:

- Every `span()`, `turn_span()`, `start_observation()`, `end()` call is a null context manager.
- No network traffic to Langfuse.
- No performance overhead; the default test suite stays offline and deterministic.

## Enabling Telemetry

```python
from harness_kit import telemetry
from harness_kit.config import TelemetryConfig

cfg = TelemetryConfig(enabled=True, environment="prod", release="v1.2.3", sample_rate=0.1)
telemetry.configure(cfg)
```

Credentials are read from the environment by the Langfuse SDK:
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST` (optional, for self-hosted)

Optional `telemetry` extra; if not installed and enabled, logs a warning and stays disabled.

## Span Tree

Root-level `turn` spans group by `conversation_id` (Langfuse "session") and tagged with `user_id` (Langfuse "user"):

```
turn (root)
  ├─ context.build
  │   ├─ source: profile
  │   ├─ source: episodic
  │   ├─ source: working
  │   └─ source: current
  ├─ llm.invoke_stream (per iteration)
  │   └─ TextChunk yields, token usage recorded
  └─ tool.execute:{name} (per tool call)
      └─ outcome tag (ok/denied/rate_limited/…)
```

Background memory writes (episodic embeddings, working-memory rollover) are spanned separately in `_guard()` but stay in the same trace because `asyncio.create_task` copies the OTel context at enqueue time (Langfuse v4 on OTel).

Conversation finalization is its own `conversation_end` root span under the same session.

## Identity Mapping

- `conversation_id` → Langfuse **session** (all turns in a conversation group together)
- `user_id` → Langfuse **user** (via `propagate_attributes` in `turn_span`)
- `model` → tag on the `llm.invoke_stream` span (enables cost attribution in Langfuse's UI)

## Streaming Rule

The `invoke_stream` wrapper must never buffer the response — each `TextChunk` is yielded immediately for [[pages/entities/serving-layer|time-to-first-token]]. Uses `start_observation()` and `end()` (not a context manager held across yield statements, which would shuffle the OTel current-span variable and cause span nesting issues).

## Integration

- `service.py` wraps the LLM and embedder in `TracingLLM` and `TracingEmbedder` when telemetry is enabled.
- `Agent.run_turn()` opens a `turn_span()` at entry.
- `ContextBuilder.build()` is wrapped.
- `ToolRegistry.execute()` opens per-tool spans.
- Background write guards in `Agent._guard()` are spanned.

## Future: OTel Swap

Langfuse v4 is built on OpenTelemetry natively. Swapping to a pure OTel backend (Grafana Loki, Jaeger, etc.) means reimplementing `telemetry.py` to use `opentelemetry` SDK instead of `langfuse` — no other modules need to change.
