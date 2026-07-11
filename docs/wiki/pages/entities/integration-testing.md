---
title: Live Integration Testing (Opt-In)
category: entity
tags: [testing, integration, live-tests, real-llm, coverage, setup]
created: 2026-07-11
updated: 2026-07-11
sources: [tests/integration/, CLAUDE.md#Live integration tests]
status: current
---

# Live Integration Testing (Opt-In)

## Overview

`tests/integration/` is a separate test suite that exercises real LLM calls. It is **opt-in and skipped by default** — enabled only when `LIVE_TESTS_ENABLED=1` is set. This allows the main suite to stay fast and offline while providing a way to verify behavior against actual models.

## Setup

1. **Create a live config** — Copy `config.yaml` to `config_live.yaml` (in the project root) and set your LLM provider and model:
   ```yaml
   llm_kit:
     provider: anthropic  # or openai, etc.
     model: claude-opus-4-8
     api_key_env: ANTHROPIC_API_KEY
   ```

2. **Export your API key** under the name specified in `api_key_env`:
   ```bash
   export ANTHROPIC_API_KEY=sk-...
   ```

3. **Run the suite:**
   ```bash
   LIVE_TESTS_ENABLED=1 uv run pytest tests/integration/ -v
   ```

## What Stays Fake

- **Embedder is always `FakeEmbedder`.** No embedding endpoint or API call is needed.
- **Stores are in-memory.** No external infrastructure (Redis, SQLite, Qdrant) is required; all tests run against in-memory adapters.

Only the LLM is live.

## Coverage Areas

The live suite covers critical flows that require real model behavior:

- **`test_streaming.py`** — Verifies event sequence and token usage invariants (ensures [[pages/entities/agent-loop|agent loop]] streaming is correct and token counts are accurate)
- **`test_tool_roundtrip.py`** — Real LLM calls `remember_fact`, exercises ≥2 iterations (validates tool-calling accuracy and multi-turn reasoning)
- **`test_native_memory_tools.py`** — Validates `list_facts`, `forget_fact`, `recall` invoked by real LLM (ensures [[pages/entities/native-tools|native tools]] and [[pages/entities/factual-memory|factual memory]] work as designed)
- **`test_working_memory.py`** — Fires [[pages/decisions/rolling-summary-rollover|rollover]] when `buffer_token_budget` is exceeded (tests token-driven eviction with real token counting)
- **`test_episodic_memory.py`** — Verifies `end_conversation` writes one vector point per conversation (checks [[pages/entities/episodic-memory|episodic memory]] finalization)
- **`test_factual_extraction.py`** — Durable facts are extracted; ephemeral context is omitted (validates memory tier semantics)
- **`test_skills.py`** — Skills are discovered at startup, `read_skill` is callable, instructions are followed (validates [[pages/entities/skills-system|skills]] integration)

## Cost and Duration

Each test makes real API calls to the LLM, so:
- **Cost:** Each test burns a small amount of API quota (typically <$1 per suite run)
- **Duration:** Tests take longer than the offline suite (network latency + model inference)

Use `LIVE_TESTS_ENABLED=1` selectively — e.g., before major releases or after significant changes to [[pages/entities/agent-loop|loop logic]], [[pages/entities/context-builder|context assembly]], or [[pages/synthesis/memory-system-overview|memory]] systems.

## Design Notes

Live tests are designed to be **minimal and focused**:
- They test real behavior, not infrastructure (in-memory stores are sufficient)
- They don't require Docker or external services
- They're fast enough to run before pushing to main (within a minute or two)

This is a planned feature noted in ROADMAP.md — the suite exists and is stable, but live testing in CI/CD remains a future consideration due to API costs and latency.
