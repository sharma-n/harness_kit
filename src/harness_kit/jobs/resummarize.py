"""Episodic re-summarization batch job (M8).

Conversation points older than ``min_age_days`` have their text condensed by
the LLM and their embedding refreshed.  The point ID is preserved so the
operation is an upsert, not a duplication.  Moment points are short by design
and are excluded.

Layering: imports only from stores/, config/, and llm_kit.  Does NOT import
from agent/ or serving/.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from llm_kit import Message
from llm_kit.batch.processor import run_batch_stream
from llm_kit.config.batch import BatchConfig
from llm_kit.embed.response import EmbedItem
from llm_kit.llm.response import BatchItem

from harness_kit.config.schema import ResummarizationConfig
from harness_kit.jobs._base import load_all_user_points
from harness_kit.llm import LLM
from harness_kit.retry import RetryPolicy, store_write
from harness_kit.stores.base import VectorStore
from harness_kit.stores.types import MemoryPoint

_log = logging.getLogger(__name__)

_RESUMMARIZE_SYSTEM_PROMPT = (
    "You are given a summary of a past conversation. "
    "Produce a condensed version that preserves the key topics, decisions, and "
    "discussion threads. Remove redundant context and keep it under 150 words. "
    "Write in the same neutral third-person style."
)


@dataclass(slots=True)
class ResummarizationResult:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0


class EpisodicResummarizer:
    """Refresh the text and embeddings of stale episodic conversation points."""

    def __init__(
        self,
        store: VectorStore,
        embedder: object,  # OpenAICompatibleEmbedder (concrete, has embed_batch)
        llm: LLM,
        cfg: ResummarizationConfig,
        store_retry: RetryPolicy | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._cfg = cfg
        self._store_retry = store_retry or RetryPolicy()

    async def run_for_user(self, user_id: str) -> ResummarizationResult:
        result = ResummarizationResult()
        now = time.time()
        cutoff = now - self._cfg.min_age_days * 86400.0

        conv_points = await load_all_user_points(self._store, user_id, kind="conversation")
        result.scanned = len(conv_points)

        eligible = [
            p for p in conv_points
            if p.payload.get("ts", now) <= cutoff
        ]

        if len(eligible) > self._cfg.max_points_per_user:
            _log.warning(
                "resummarize: user %s has %d eligible points, truncating to %d",
                user_id, len(eligible), self._cfg.max_points_per_user,
            )
            eligible = eligible[: self._cfg.max_points_per_user]

        result.skipped = result.scanned - len(eligible)
        if not eligible:
            return result

        # --- Build LLM batch items ---
        batch_items = [
            BatchItem(
                id=p.id,
                messages=[
                    Message.system(_RESUMMARIZE_SYSTEM_PROMPT),
                    Message.user(p.payload.get("text", "")),
                ],
                metadata={"original_ts": p.payload.get("ts", now)},
            )
            for p in eligible
        ]

        batch_cfg = BatchConfig(worker_concurrency=self._cfg.worker_concurrency)
        fresh_texts: dict[str, tuple[str, float]] = {}  # point_id -> (new_text, original_ts)
        async for br in run_batch_stream(llm=self._llm, items=batch_items, config=batch_cfg):
            if br.ok and br.response is not None:
                orig_ts = br.metadata.get("original_ts", now)
                fresh_texts[br.id] = (br.response.text, orig_ts)
            else:
                _log.warning(
                    "resummarize: LLM failed for point=%s: %s", br.id, br.error
                )

        if not fresh_texts:
            return result

        # --- Embed fresh summaries ---
        embed_items = [
            EmbedItem(id=pid, text=text)
            for pid, (text, _) in fresh_texts.items()
        ]
        # Build a lookup from original point id -> original point for payload merging
        point_by_id = {p.id: p for p in eligible}

        vectors_by_id: dict[str, list[float]] = {}
        async for er in self._embedder.embed_batch(embed_items):
            if er.ok and er.response is not None:
                vectors_by_id[er.id] = er.response.vector
            else:
                _log.warning(
                    "resummarize: embed failed for point=%s: %s", er.id, er.error
                )

        # --- Upsert refreshed points (same ID = upsert, not duplicate) ---
        updated_points: list[MemoryPoint] = []
        for pid, (text, original_ts) in fresh_texts.items():
            if pid not in vectors_by_id:
                continue
            orig = point_by_id[pid]
            updated_payload = {
                **orig.payload,
                "text": text,
                "ts": original_ts,       # preserve original timestamp
                "resummarized_at": now,  # observability: when was this refreshed
            }
            updated_points.append(
                MemoryPoint(id=pid, vector=vectors_by_id[pid], payload=updated_payload)
            )

        if updated_points:
            await store_write(
                lambda: self._store.add(updated_points),
                policy=self._store_retry,
                operation="jobs.resummarize.add",
            )
            result.updated = len(updated_points)

        return result

    async def run_for_all_users(self, user_ids: list[str]) -> list[ResummarizationResult]:
        results = []
        for uid in user_ids:
            try:
                r = await self.run_for_user(uid)
                results.append(r)
                _log.info(
                    "resummarize: user=%s scanned=%d updated=%d skipped=%d",
                    uid, r.scanned, r.updated, r.skipped,
                )
            except Exception:
                _log.exception("resummarize: failed for user=%s", uid)
                results.append(ResummarizationResult())
        return results
