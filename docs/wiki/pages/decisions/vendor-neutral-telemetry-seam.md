---
title: Vendor-Neutral Telemetry Seam
category: decision
tags: [observability, telemetry, decoupling, langfuse, otel, vendor-lock-in]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/telemetry.py, src/harness_kit/metrics.py, CLAUDE.md#Telemetry / tracing (Langfuse)]
status: current
---

# Vendor-Neutral Telemetry Seam

## Decision

Create two leaf modules (`telemetry.py` and `metrics.py`) that are the *only* places importing Langfuse and Prometheus respectively. All other layers call thin seam APIs, enabling backend swaps by reimplementing these two files.

## Pattern

Leaf modules (like `tokens.py`, `retry.py`, `errors.py`) can be imported from any layer without violating [[pages/concepts/bottom-up-layering|bottom-up layering]]. This allows any layer to use telemetry/metrics without a dependency cycle.

**telemetry.py:**
- Imports: `langfuse` (lazy, inside `configure()`)
- Exports: `configure()`, `span()`, `turn_span()`, `start_observation()`, `end()`, `SpanHandle`
- Call sites never touch `langfuse` directly

**metrics.py:**
- Imports: `prometheus_client` (lazy, inside `configure()`)
- Exports: `configure()`, `record_ttft_seconds()`, `record_tool_call()`, etc.
- Call sites never touch `prometheus_client` directly

## Off by Default

Both modules are no-op when disabled (default):

- `TelemetryConfig.enabled=false` → all telemetry calls are null context managers
- `MetricsConfig.enabled=false` → all metric calls are fast branches returning None

This keeps the default test suite offline, deterministic, and free of Langfuse/Prometheus overhead. The golden context test still passes unchanged.

## Future Backend Swaps

To swap from Langfuse → pure OpenTelemetry (Grafana, Jaeger, etc.):

1. Implement `telemetry.py` using `opentelemetry` SDK instead of `langfuse`
2. Update `configure()` to accept OTel exporter config
3. No changes to `agent/`, `tools/`, `memory/`, `serving/` — they depend only on the seam API

To swap from Prometheus → StatsD, Datadog, or CloudWatch:

1. Implement `metrics.py` using the new client
2. No other changes needed

## Langfuse v4 & OTel Native

Langfuse v4 is built on OpenTelemetry natively. If harness_kit later adopts pure OTel, the swap is straightforward because:

- Spans, baggage, and trace context already propagate correctly (OTel is underlying Langfuse v4)
- `asyncio.create_task` automatically copies context variables (OTel contextvars)
- The seam pattern isolates the swap to two files

## Identity in Traces

Within the Langfuse/OTel backend:

- `conversation_id` maps to Langfuse "session"
- `user_id` maps to Langfuse "user"
- Background writes stay in the same trace because context is copied at `asyncio.create_task` time

Backends that don't have "session" or "user" concepts can still work — just skip those tags or map them differently.

## Optional Extras

- `telemetry` extra: installs `langfuse` (or placeholder if v4/pure-OTel era)
- `metrics` extra: installs `prometheus_client`

Both are optional; the service runs without them (disabled mode is the fallback).
