"""Redis-backed SessionStore (SPEC §9.1).

Key layout (all strings; TTL resets on every activity to implement idle eviction):

    session:{conv_id}               STRING  JSON-serialised SessionState
    user:{user_id}:convs            ZSET    member=conv_id, score=updated_at
    sessions:pending_finalize       ZSET    member=conv_id, score=updated_at

The two ZSETs are side-indices that avoid full-key SCAN:
  - ``user:{uid}:convs`` powers ``list(user_id)`` in O(log n) + N GETs.
  - ``sessions:pending_finalize`` powers ``due_for_finalize`` via ZRANGEBYSCORE
    with score <= (now - idle_s); entries are removed on ``mark_finalized`` or
    when new activity resets finalized_at to None (and the entry is re-added on
    the next save/append_turn).

Session keys carry an EXPIRE equal to ttl_s (reset on every activity) so Redis
handles eviction automatically. ZSET members for evicted sessions are stale but
harmless — a missing GET is treated as "not found" and the entry is skipped.
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis
from llm_kit import ToolCall

from agent_kit.errors import UnauthorizedError
from agent_kit.stores.types import ConversationMeta, SessionState, Turn

_PREVIEW_LEN = 200
_KEY_SESSION = "session:{}"
_KEY_USER_CONVS = "user:{}:convs"
_KEY_PENDING = "sessions:pending_finalize"


def _session_key(conv_id: str) -> str:
    return _KEY_SESSION.format(conv_id)


def _user_key(user_id: str) -> str:
    return _KEY_USER_CONVS.format(user_id)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _to_json(state: SessionState) -> str:
    return json.dumps({
        "user_id": state.user_id,
        "working_buffer": [
            {
                "role": t.role,
                "text": t.text,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in t.tool_calls
                ],
                "tool_call_id": t.tool_call_id,
                "ts": t.ts,
            }
            for t in state.working_buffer
        ],
        "rolling_summary": state.rolling_summary,
        "scratch": state.scratch,
        "updated_at": state.updated_at,
        "finalized_at": state.finalized_at,
        "created_at": state.created_at,
    })


def _from_dict(d: dict[str, Any]) -> SessionState:
    buffer = [
        Turn(
            role=t["role"],
            text=t["text"],
            tool_calls=[ToolCall(**tc) for tc in t.get("tool_calls", [])],
            tool_call_id=t.get("tool_call_id"),
            ts=t["ts"],
        )
        for t in d.get("working_buffer", [])
    ]
    return SessionState(
        user_id=d["user_id"],
        working_buffer=buffer,
        rolling_summary=d.get("rolling_summary", ""),
        scratch=d.get("scratch", {}),
        updated_at=d["updated_at"],
        finalized_at=d.get("finalized_at"),
        created_at=d.get("created_at", d["updated_at"]),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class RedisSessionStore:
    """SPEC §9.1 — Redis-backed session store with idle-TTL eviction."""

    def __init__(self, url: str, ttl_s: int | None = None) -> None:
        self._client: aioredis.Redis = aioredis.from_url(url, decode_responses=True)
        self._ttl_s = ttl_s

    async def load(self, conversation_id: str, user_id: str) -> SessionState | None:
        raw = await self._client.get(_session_key(conversation_id))
        if raw is None:
            return None
        state = _from_dict(json.loads(raw))
        if state.user_id != user_id:
            raise UnauthorizedError(
                f"conversation {conversation_id!r} is not owned by user {user_id!r}"
            )
        return state

    async def save(self, conversation_id: str, state: SessionState) -> None:
        now = time.time()
        state.updated_at = now
        state.finalized_at = None  # new activity → re-finalize on next idle sweep

        raw = _to_json(state)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(_session_key(conversation_id), raw, ex=self._ttl_s)
            pipe.zadd(_user_key(state.user_id), {conversation_id: now})
            pipe.zadd(_KEY_PENDING, {conversation_id: now})
            await pipe.execute()

    async def append_turn(self, conversation_id: str, turn: Turn) -> None:
        raw = await self._client.get(_session_key(conversation_id))
        if raw is None:
            raise KeyError(f"no session {conversation_id!r}; call save() first")
        state = _from_dict(json.loads(raw))
        state.working_buffer.append(turn)
        now = time.time()
        state.updated_at = now
        state.finalized_at = None

        new_raw = _to_json(state)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(_session_key(conversation_id), new_raw, ex=self._ttl_s)
            pipe.zadd(_user_key(state.user_id), {conversation_id: now})
            pipe.zadd(_KEY_PENDING, {conversation_id: now})
            await pipe.execute()

    async def due_for_finalize(self, idle_s: float) -> list[tuple[str, str]]:
        cutoff = time.time() - idle_s
        conv_ids: list[str] = await self._client.zrangebyscore(
            _KEY_PENDING, "-inf", cutoff
        )
        if not conv_ids:
            return []

        # Batch-fetch session data to get user_ids.
        async with self._client.pipeline() as pipe:
            for cid in conv_ids:
                pipe.get(_session_key(cid))
            raws = await pipe.execute()

        result: list[tuple[str, str]] = []
        for cid, raw in zip(conv_ids, raws):
            if raw is None:
                # Session expired; clean up stale ZSET entry.
                await self._client.zrem(_KEY_PENDING, cid)
                continue
            d = json.loads(raw)
            if d.get("finalized_at") is not None:
                # Already finalised in the meantime — should not normally be in ZSET,
                # but be defensive.
                continue
            result.append((cid, d["user_id"]))
        return result

    async def mark_finalized(self, conversation_id: str) -> None:
        raw = await self._client.get(_session_key(conversation_id))
        if raw is None:
            return  # already evicted — no-op
        state = _from_dict(json.loads(raw))
        state.finalized_at = time.time()
        new_raw = _to_json(state)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(_session_key(conversation_id), new_raw, ex=self._ttl_s)
            pipe.zrem(_KEY_PENDING, conversation_id)
            await pipe.execute()

    async def list(self, user_id: str) -> list[ConversationMeta]:
        # ZREVRANGE → highest score (most-recently-updated) first.
        conv_ids: list[str] = await self._client.zrevrange(
            _user_key(user_id), 0, -1
        )
        if not conv_ids:
            return []

        async with self._client.pipeline() as pipe:
            for cid in conv_ids:
                pipe.get(_session_key(cid))
            raws = await pipe.execute()

        metas: list[ConversationMeta] = []
        for cid, raw in zip(conv_ids, raws):
            if raw is None:
                continue  # TTL-evicted; skip (stale ZSET entry)
            d = json.loads(raw)
            if d["user_id"] != user_id:
                continue  # defensive: should not happen but guard anyway
            metas.append(ConversationMeta(
                conversation_id=cid,
                user_id=d["user_id"],
                created_at=d.get("created_at", d["updated_at"]),
                updated_at=d["updated_at"],
                finalized_at=d.get("finalized_at"),
                turn_count=len(d.get("working_buffer", [])),
                summary_preview=d.get("rolling_summary", "")[:_PREVIEW_LEN],
            ))
        return metas
