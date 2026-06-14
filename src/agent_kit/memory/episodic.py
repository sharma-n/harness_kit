"""Episodic memory (SPEC §7.2, §6.4): retrieval over the per-user vector store.

Read (retrieve) is on the hot path every turn. Write is off the hot path and, by
design, **per conversation rather than per turn**: the whole conversation (rolling
summary + remaining buffer) is embedded as a single point when the conversation
ends. This keeps the vector store compact and embedding cost low, trading per-turn
recall precision for coarse-grained, conversation-level memory.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from agent_kit.config import EpisodicMemoryConfig
from agent_kit.llm import LLM, Embedder
from agent_kit.retry import RetryPolicy, store_write
from agent_kit.stores.base import VectorStore
from agent_kit.stores.types import MemoryHit, MemoryPoint, Turn


class StandaloneQuery(BaseModel):
    """Schema for the optional query-rewrite step (SPEC §6.4.3)."""

    query: str


class EpisodicMemory:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        cfg: EpisodicMemoryConfig,
        *,
        llm: LLM | None = None,
        store_retry: RetryPolicy | None = None,
    ) -> None:
        self._store = vector_store
        self._embedder = embedder
        self._cfg = cfg
        self._llm = llm  # only needed when query_rewrite is enabled
        self._store_retry = store_retry or RetryPolicy()

    async def retrieve(
        self, user_id: str, user_message: str, recent_turns: list[Turn]
    ) -> list[MemoryHit]:
        """Top-k hits above ``min_score`` for this user, or ``[]`` (inject nothing)."""
        query_text = await self._build_query(user_message, recent_turns)
        vector = (await self._embedder.embed_one(query_text)).vector
        return await self._store.search(
            user_id, vector, k=self._cfg.top_k, min_score=self._cfg.min_score
        )

    async def write_conversation(
        self,
        user_id: str,
        conversation_id: str,
        summary: str,
        turns: list[Turn],
    ) -> None:
        """Embed a whole conversation as ONE episodic point (at conversation end).

        Composes the rolling summary + remaining buffer into a single transcript,
        embeds it, and upserts one point. No-op for an empty conversation.
        """
        text = self._compose(summary, turns)
        if not text:
            return
        vector = (await self._embedder.embed_one(text)).vector
        point = MemoryPoint(
            # Deterministic per conversation: if a resumed conversation is finalized
            # again later, the new (fuller) point upserts the old one rather than
            # accumulating duplicates. conversation_id is globally unique to one user.
            id=f"conv:{conversation_id}",
            vector=vector,
            payload={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "text": text,
                "kind": "conversation",
                "ts": time.time(),
            },
        )
        # Retry only the store write; the embed_one above is already retried by
        # llm_kit, and re-running it on a store fault would waste an embedding call.
        await store_write(
            lambda: self._store.add([point]),
            policy=self._store_retry,
            operation="episodic.write_conversation",
        )

    @staticmethod
    def _compose(summary: str, turns: list[Turn]) -> str:
        parts: list[str] = []
        if summary:
            parts.append(summary)
        parts.extend(f"{t.role}: {t.text}" for t in turns if t.text)
        return "\n".join(parts).strip()

    async def _build_query(self, user_message: str, recent_turns: list[Turn]) -> str:
        # Default: the current message. Context-augment with recent turns so
        # pronouns/ellipsis in follow-ups resolve.
        augment_n = self._cfg.query_augment_turns
        if augment_n > 0 and recent_turns:
            tail = recent_turns[-augment_n:]
            prefix = " ".join(t.text for t in tail if t.text)
            base = f"{prefix} {user_message}".strip() if prefix else user_message
        else:
            base = user_message

        if self._cfg.query_rewrite and self._llm is not None:
            return await self._rewrite(base)
        return base

    async def _rewrite(self, text: str) -> str:
        from llm_kit import Message

        resp = await self._llm.invoke(
            [
                Message.system(
                    "Rewrite the user's text into a single standalone search query "
                    "that resolves pronouns and ellipsis. Return only the query."
                ),
                Message.user(text),
            ],
            response_model=StandaloneQuery,
        )
        if resp.parsed is not None:
            return resp.parsed.query  # type: ignore[attr-defined]
        return text
