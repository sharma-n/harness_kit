---
title: Fake-Driven Testing Posture
category: concept
tags: [testing, FakeLLM, unit-tests, conftest, determinism, mirroring]
created: 2026-07-11
updated: 2026-07-11
sources: [tests/conftest.py, CLAUDE.md#Testing posture]
status: current
---

# Fake-Driven Testing Posture

## Philosophy

harness_kit mirrors `llm_kit`'s own testing approach: a **fake-driven suite** using scripted LLM responses, not live API calls. This keeps tests fast, deterministic, and offline.

## FakeLLM

`FakeLLM` (in `tests/conftest.py`) replays pre-scripted streaming turns:

- **Input:** a list of turn specifications (text chunks + `StreamEnd` with tool calls)
- **Output:** yields chunks and tool definitions in order, matching the streaming signature of a real `LLMClient`
- **Usage:** `make_service(cfg, turns=[...])` wires it into the real stores so the rest of the system (context assembly, tool registry, serving layer) runs full-stack against realistic but deterministic data

No mocking of stores or memory — all the infrastructure is real, allowing tests to verify interactions end-to-end.

## Mirroring llm_kit

The fake approach reuses the same pattern as the underlying `llm_kit` library: **one fake implementation that stands in for the real client**, letting both projects test in isolation without external API calls.

## Testing Discipline

- **No live-key tests in-repo by default.** Tests must be network-free and runnable locally.
- **Golden test is deterministic.** `tests/test_context.py` asserts the exact assembled message list, relying on FakeLLM reproducibility. Any change to context assembly (order, formatting, block changes) must update the golden test deliberately.
- **Integration testing is opt-in.** Live LLM tests live separately (see [[pages/entities/integration-testing|integration testing]]), gated by `LIVE_TESTS_ENABLED=1` environment variable.

## When Tests Are Useful

- Verifying [[pages/entities/context-builder|context assembly]] order and formatting
- Testing tool registry behavior (permissions, rate limiting, approval gates)
- Validating [[pages/concepts/permission-model|permission model]] enforcement
- Checking [[pages/entities/working-memory|rollover logic]] under token budget pressure
- Memory write paths (factual extraction, episodic finalization)
- Error handling and edge cases (that don't require a real LLM)

## When Tests Are Limited

- Model-specific behavior (e.g., does Claude actually follow this tool instruction?) — requires live tests
- Tool calling accuracy and argument parsing nuance — requires real LLM
- Streaming latency and resource usage — requires production-like conditions
