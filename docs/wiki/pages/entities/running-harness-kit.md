---
title: Running harness_kit — Setup, Examples, and Serving
category: entity
tags: [operations, setup, examples, FastAPI, single_turn, running, uv, environment]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Running things, examples/, src/harness_kit/serving/app.py]
status: current
---

# Running harness_kit — Setup, Examples, and Serving

## Setup

Install dependencies via `uv`:

```bash
uv sync --extra dev --extra mcp --extra telemetry
```

**Note on Windows:** Use `--native-tls` flag on Windows machines to avoid certificate errors:
```bash
uv sync --native-tls --extra dev --extra mcp --extra telemetry
```

## Unit Tests

Run the offline test suite (no network required):

```bash
uv run pytest
```

Tests use [[pages/concepts/fake-driven-testing|FakeLLM]] and are deterministic. The golden context test (`tests/test_context.py`) verifies the exact assembled message list — a critical invariant of [[pages/entities/context-builder|context assembly]].

## Single-Turn Example

Run a one-shot conversation:

```bash
ANTHROPIC_API_KEY=sk-... uv run python examples/single_turn.py
```

This example:
1. Builds the service from `config.yaml`
2. Creates a conversation
3. Sends a single turn
4. Prints the response and tool calls

## FastAPI Server

Start the production-ready [[pages/entities/serving-layer|serving layer]]:

```bash
ANTHROPIC_API_KEY=sk-... uv run uvicorn "harness_kit.serving.app:create_app_from_yaml" --factory
```

The server provides:
- **WebSocket** at `/ws` — bidirectional; supports approval and model-switch commands
- **SSE** at `/sse` — unidirectional streaming (auto-deny approvals)
- **REST** at `/conversations` — list, get, put (metadata updates)

See `src/harness_kit/serving/app.py` and examples/ws_client.py for usage patterns.

## Configuration

The default config is `config.yaml` in the project root. It nests an `llm_kit` block with provider credentials. To use a different config:

```bash
HARNESS_CONFIG=my_config.yaml ANTHROPIC_API_KEY=... uv run uvicorn ...
```

The [[pages/entities/service-composition-root|service composition root]] (`service.py`) loads config at startup.

## Key Environment Variables

- `ANTHROPIC_API_KEY` — API key for the LLM provider (required for live examples)
- `HARNESS_CONFIG` — Path to config.yaml (default: `config.yaml` in cwd)
- `LIVE_TESTS_ENABLED` — Set to `1` to enable [[pages/entities/integration-testing|live integration tests]]
- `LOG_LEVEL` — Python logging level (default: INFO)

## Offline Jobs (M8)

Batch operations for episodic memory maintenance:

```bash
python -m harness_kit.jobs dedup      # Cluster and merge duplicate memories
python -m harness_kit.jobs resummarize  # Re-summarize old conversations
```

See [[pages/entities/jobs-offline|offline jobs]] for details.
