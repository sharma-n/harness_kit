---
title: Per-Conversation Model Switching
category: decision
tags: [models, llm, switching, sessions, configuration]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/service.py, src/harness_kit/agent/loop.py, src/harness_kit/serving/app.py, CLAUDE.md#Per-conversation model switching]
status: current
---

# Per-Conversation Model Switching

## Decision

Callers can change the LLM model mid-conversation without rebuilding the service. The override is stored in [[pages/entities/stores-overview|the session]] and takes effect on the next turn.

## API

```python
# Set override:
await service.set_conversation_model(conversation_id, user_id, "claude-opus-4-8")

# Clear override (revert to service default):
await service.set_conversation_model(conversation_id, user_id, None)
```

## Storage

`SessionState.model_name: str | None`

- `None` — use the service-level default (from config)
- `"claude-opus-4-8"` — use this specific model for this conversation

The in-memory adapter stores this as a Python attribute. `RedisSessionStore` serializes it as a JSON field and uses `.get("model_name")` for forward-compatibility with sessions that predate the field.

`ConversationMeta.model_name` is populated by `SessionStore.list()` and exposed in the `/conversations` listing API, so callers can see which model each conversation is using.

## Resolution in Agent

After [[pages/entities/context-builder|context is built]], `Agent.run_turn()` does a two-gate check before the iteration loop:

1. **Factory available:** `self._llm_factory is not None` — can the service build per-model clients?
   - `True` if the service built its own `LLMClient` (normal path)
   - `False` if an LLM was externally injected (test path with `FakeLLM`)

2. **Override present:** `SessionState.model_name is not None` — does *this* conversation want a different model?
   - `None` for most conversations → use `self._llm` (service default)
   - `"claude-opus-4-8"` → build/cache a per-model LLM

Both must be true to swap. Lookup is O(1) via `WorkingMemory.get_model_name()` (single dict access).

## Factory Implementation

Constructed in `AgentService.build()` as a `_make_llm` closure when the service builds its own `LLMClient`:

- **Captures:** shared `httpx.AsyncClient`, `cfg.llm_kit`, and `_llm_cache` dict
- **Build:** Per-model LLM clients on first use (same connection pool/API keys, only `model` differs)
- **Cache:** By model name for reuse (subsequent calls are free)
- **None when injected:** `set_conversation_model` raises `ValueError` if `llm=` was injected

## Serving Integration

### WebSocket

Handler recognizes a new message type:
```json
{"type": "set_model", "user_id": "alice", "model": "claude-opus-4-8"}
```

Pass `"model": null` to clear. The next turn uses the new model.

### REST (SSE)

```
PUT /conversations/{conversation_id}/model?user_id=alice&model=claude-opus-4-8
```

Omit `model` param or pass `null` to clear. Returns:
```json
{"conversation_id": "conv123", "model": "claude-opus-4-8"}
```

## Isolation

- Changing the model in conversation A doesn't affect conversation B or the service default.
- The override is per-conversation, per-session; it's not a global or per-user setting (though callers can apply the API per-user).

## Layering

`Agent` never imports `llm_kit` directly — it receives a `Callable[[str], LLM] | None` factory from [[pages/entities/service-composition-root|service.py]], the only place `LLMClient` is constructed. The [[pages/entities/llm-protocols|Protocol boundary]] is preserved: the loop depends only on `LLM`, not the concrete client.

## Limitations

- No warmup of per-model caches at startup (they build on first use).
- Cache never evicts (all seen models are cached for the app's lifetime); very-high-cardinality model-switching could grow memory unbounded (mitigated by operator discipline or a future LRU cache layer).
