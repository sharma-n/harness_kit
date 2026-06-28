"""Episodic memory (SPEC §7.2, §6.4): retrieval over the per-user vector store.

Read (retrieve) is on the hot path every turn. Write is off the hot path and, by
design, **per conversation rather than per turn**: the whole conversation (rolling
summary + remaining buffer) is embedded as a single point when the conversation
ends. This keeps the vector store compact and embedding cost low, trading per-turn
recall precision for coarse-grained, conversation-level memory.

When ``flagged_moments_enabled`` is set, the LLM additionally identifies 1–N
notable discussion threads within the conversation and embeds each as a sibling
point (``kind="moment"``). This improves recall precision for specific topics
without per-turn embedding noise: the conversation point handles broad "what was
this conversation about?" queries, while moment points answer "what specific topics
did we work through?". Both kinds compete naturally in ``top_k`` search, with the
budgeter handling density via score-based eviction.
"""

from __future__ import annotations

import logging
import math
import time

from pydantic import BaseModel

from agent_kit.config import EpisodicMemoryConfig
from agent_kit.llm import LLM, Embedder
from agent_kit.retry import RetryPolicy, store_write
from agent_kit.stores.base import VectorStore
from agent_kit.stores.types import MemoryHit, MemoryPoint, Turn
from agent_kit import telemetry, metrics as _metrics

_log = logging.getLogger(__name__)


class StandaloneQuery(BaseModel):
    """Schema for the optional query-rewrite step (SPEC §6.4.3)."""

    query: str


class FlaggedMoment(BaseModel):
    """One notable discussion thread within a conversation."""

    text: str


class FlaggedMoments(BaseModel):
    """LLM response model for flagged-moments extraction."""

    moments: list[FlaggedMoment]


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
        # When decay is active, fetch extra candidates so re-ranking can surface
        # fresher points that fell just below an unweighted top-k cutoff.
        fetch_k = self._cfg.top_k * 2 if self._cfg.decay_rate > 0.0 else self._cfg.top_k
        results = await self._store.search(
            user_id, vector, k=fetch_k, min_score=self._cfg.min_score
        )
        if self._cfg.decay_rate > 0.0:
            results = self._apply_decay(results)
        _metrics.record_retrieval(len(results))
        return results

    def _apply_decay(self, hits: list[MemoryHit]) -> list[MemoryHit]:
        """Multiply each hit's score by exp(-decay_rate * age_days), then re-rank."""
        now = time.time()
        decayed: list[MemoryHit] = []
        for h in hits:
            ts = h.point.payload.get("ts")
            if ts is not None:
                age_days = max(0.0, (now - float(ts)) / 86400.0)
                factor = math.exp(-self._cfg.decay_rate * age_days)
                decayed.append(MemoryHit(point=h.point, score=h.score * factor))
            else:
                decayed.append(h)
        decayed.sort(key=lambda h: h.score, reverse=True)
        return decayed[: self._cfg.top_k]

    async def forget_conversation(self, user_id: str, conversation_id: str) -> int:
        """Delete the conv: point and all moment: siblings for this conversation.

        Returns the count of points deleted (0 means nothing was found).
        User isolation is enforced via ``list_points`` (which is always
        user-scoped), so a guessed ``conversation_id`` belonging to another
        user returns 0 rather than deleting anything.
        """
        conv_points = await self._store.list_points(user_id, kind="conversation")
        moment_points = await self._store.list_points(user_id, kind="moment")
        ids = [
            p.id for p in conv_points if p.id == f"conv:{conversation_id}"
        ] + [
            p.id for p in moment_points
            if p.payload.get("conversation_id") == conversation_id
        ]
        if not ids:
            return 0
        await store_write(
            lambda: self._store.delete(ids, user_id=user_id),
            policy=self._store_retry,
            operation="episodic.forget_conversation",
        )
        return len(ids)

    async def write_conversation(
        self,
        user_id: str,
        conversation_id: str,
        summary: str,
        turns: list[Turn],
    ) -> None:
        """Embed a whole conversation as one episodic point (at conversation end).

        Composes the rolling summary + remaining buffer into a single transcript,
        embeds it, and upserts one ``kind="conversation"`` point. When
        ``flagged_moments_enabled`` is set, also extracts 1–N notable discussion
        threads and upserts each as a sibling ``kind="moment"`` point. No-op for
        an empty conversation.
        """
        text = self._compose(summary, turns)
        if not text:
            return
        with telemetry.span("memory.episodic.write_conversation", turns=len(turns)):
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

            # Optional: embed notable discussion threads as sibling moment points.
            moment_texts = await self._extract_moments(text)
            if moment_texts:
                ts = time.time()
                moment_points: list[MemoryPoint] = []
                for i, moment_text in enumerate(moment_texts):
                    m_vector = (await self._embedder.embed_one(moment_text)).vector
                    moment_points.append(
                        MemoryPoint(
                            # Deterministic index: re-finalize upserts rather than duplicates.
                            id=f"moment:{conversation_id}:{i}",
                            vector=m_vector,
                            payload={
                                "user_id": user_id,
                                "conversation_id": conversation_id,
                                "text": moment_text,
                                "kind": "moment",
                                "parent_id": f"conv:{conversation_id}",
                                "ts": ts,
                            },
                        )
                    )
                await store_write(
                    lambda: self._store.add(moment_points),
                    policy=self._store_retry,
                    operation="episodic.write_moments",
                )

    async def _extract_moments(self, text: str) -> list[str]:
        """Return up to max_flagged_moments discussion-thread summaries, or [] on no-op."""
        if not self._cfg.flagged_moments_enabled or self._llm is None:
            return []
        try:
            from llm_kit import Message

            prompt = self._cfg.flagged_moments_system_prompt.format(
                max_moments=self._cfg.max_flagged_moments
            )
            resp = await self._llm.invoke(
                [Message.system(prompt), Message.user(text)],
                response_model=FlaggedMoments,
            )
            if resp.parsed is None:
                return []
            return [m.text for m in resp.parsed.moments][: self._cfg.max_flagged_moments]
        except Exception:
            _log.warning("episodic._extract_moments failed", exc_info=True)
            return []

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
                Message.system(self._cfg.query_rewrite_system_prompt),
                Message.user(text),
            ],
            response_model=StandaloneQuery,
        )
        if resp.parsed is not None:
            return resp.parsed.query  # type: ignore[attr-defined]
        return text
