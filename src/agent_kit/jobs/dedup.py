"""Episodic deduplication batch job (M8).

Near-identical conversation points are clustered using a cosine-similarity
graph + Union-Find (connected components).  Each cluster is merged into a
single point by the LLM; originals — including their moment siblings — are
deleted.

Layering: imports only from stores/, config/, and llm_kit.  Does NOT import
from agent/ or serving/.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from llm_kit import Message
from llm_kit.batch.processor import run_batch_stream
from llm_kit.config.batch import BatchConfig
from llm_kit.embed.response import EmbedItem
from llm_kit.llm.response import BatchItem

from agent_kit.config.schema import DeduplicationConfig
from agent_kit.jobs._base import load_all_user_points
from agent_kit.llm import Embedder, LLM
from agent_kit.retry import RetryPolicy, store_write
from agent_kit.stores.base import VectorStore
from agent_kit.stores.types import MemoryPoint

_log = logging.getLogger(__name__)

_MERGE_SYSTEM_PROMPT = (
    "You are given two or more summaries of past conversations from the same user. "
    "They cover very similar topics. Produce a single merged summary that preserves "
    "all distinct facts, decisions, and discussion threads from all of them. "
    "Remove exact repetitions. Keep it under 300 words. "
    "Write in the same neutral third-person style as the inputs."
)

_NS = uuid.NAMESPACE_URL


@dataclass(slots=True)
class DeduplicationResult:
    clusters_found: int = 0
    clusters_merged: int = 0
    points_deleted: int = 0


class EpisodicDeduplicator:
    """Cluster and merge near-duplicate episodic conversation points for one user."""

    def __init__(
        self,
        store: VectorStore,
        embedder: object,  # OpenAICompatibleEmbedder (concrete, has embed_batch)
        llm: LLM,
        cfg: DeduplicationConfig,
        store_retry: RetryPolicy | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._cfg = cfg
        self._store_retry = store_retry or RetryPolicy()

    async def run_for_user(self, user_id: str) -> DeduplicationResult:
        result = DeduplicationResult()

        conv_points = await load_all_user_points(self._store, user_id, kind="conversation")
        moment_points = await load_all_user_points(self._store, user_id, kind="moment")

        if len(conv_points) > self._cfg.max_points_per_user:
            _log.warning(
                "dedup: user %s has %d conversation points, truncating to %d",
                user_id, len(conv_points), self._cfg.max_points_per_user,
            )
            conv_points.sort(key=lambda p: p.payload.get("ts", 0.0), reverse=True)
            conv_points = conv_points[: self._cfg.max_points_per_user]

        if len(conv_points) < 2:
            return result

        # Build moment index: conversation_id -> list of moment point IDs
        moment_index: dict[str, list[str]] = defaultdict(list)
        for mp in moment_points:
            cid = mp.payload.get("conversation_id")
            if cid:
                moment_index[cid].append(mp.id)

        # --- Step 1: Cosine similarity matrix ---
        vectors = np.array([p.vector for p in conv_points], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-10, norms)
        normed = vectors / norms
        sim_matrix = normed @ normed.T

        upper = np.triu(np.ones_like(sim_matrix, dtype=bool), k=1)
        rows, cols = np.where((sim_matrix >= self._cfg.similarity_threshold) & upper)
        edges = list(zip(rows.tolist(), cols.tolist()))

        # --- Step 2: Union-Find to find connected components ---
        parent = list(range(len(conv_points)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for i, j in edges:
            union(i, j)

        clusters: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(conv_points)):
            clusters[find(idx)].append(idx)

        merge_groups = [idxs for idxs in clusters.values() if len(idxs) >= 2]
        result.clusters_found = len(merge_groups)

        if not merge_groups:
            return result

        # --- Step 3: Build LLM batch items for merging ---
        batch_items: list[BatchItem] = []
        for group_idxs in merge_groups:
            group = [conv_points[i] for i in group_idxs]
            conv_ids = [p.payload.get("conversation_id", p.id) for p in group]
            combined = "\n\n---\n\n".join(p.payload.get("text", "") for p in group)
            batch_id = _merge_id(conv_ids)
            batch_items.append(BatchItem(
                id=batch_id,
                messages=[
                    Message.system(_MERGE_SYSTEM_PROMPT),
                    Message.user(combined),
                ],
                metadata={"conv_ids": conv_ids, "source_points": [p.id for p in group]},
            ))

        batch_cfg = BatchConfig(worker_concurrency=self._cfg.worker_concurrency)
        merge_texts: dict[str, tuple[str, dict]] = {}  # batch_id -> (text, metadata)
        async for br in run_batch_stream(llm=self._llm, items=batch_items, config=batch_cfg):
            if br.ok and br.response is not None:
                merge_texts[br.id] = (br.response.text, br.metadata)
            else:
                _log.warning("dedup: LLM merge failed for batch_id=%s: %s", br.id, br.error)

        if not merge_texts:
            return result

        # --- Step 4: Embed merged summaries ---
        embed_items = [
            EmbedItem(id=bid, text=text)
            for bid, (text, _) in merge_texts.items()
        ]
        vectors_by_id: dict[str, list[float]] = {}
        async for er in self._embedder.embed_batch(embed_items):
            if er.ok and er.response is not None:
                vectors_by_id[er.id] = er.response.vector
            else:
                _log.warning("dedup: embed failed for batch_id=%s: %s", er.id, er.error)

        # --- Step 5: Upsert merged points + delete originals ---
        for bid, (text, meta) in merge_texts.items():
            if bid not in vectors_by_id:
                continue
            conv_ids: list[str] = meta.get("conv_ids", [])
            source_point_ids: list[str] = meta.get("source_points", [])

            # Find oldest ts among sources
            source_points = [p for p in conv_points if p.id in source_point_ids]
            ts_values = [p.payload.get("ts", time.time()) for p in source_points]
            oldest_ts = min(ts_values) if ts_values else time.time()

            merged = MemoryPoint(
                id=f"dedup:{bid}",
                vector=vectors_by_id[bid],
                payload={
                    "user_id": user_id,
                    "text": text,
                    "kind": "conversation",
                    "ts": oldest_ts,
                    "source_conversation_ids": conv_ids,
                },
            )
            await store_write(
                lambda: self._store.add([merged]),
                policy=self._store_retry,
                operation="jobs.dedup.add",
            )

            # Collect IDs to delete: source conv points + their moment siblings
            to_delete = list(source_point_ids)
            for cid in conv_ids:
                to_delete.extend(moment_index.get(cid, []))

            await store_write(
                lambda: self._store.delete(to_delete, user_id=user_id),
                policy=self._store_retry,
                operation="jobs.dedup.delete",
            )
            result.clusters_merged += 1
            result.points_deleted += len(to_delete)

        return result

    async def run_for_all_users(self, user_ids: list[str]) -> list[DeduplicationResult]:
        results = []
        for uid in user_ids:
            try:
                r = await self.run_for_user(uid)
                results.append(r)
                _log.info(
                    "dedup: user=%s clusters_found=%d merged=%d deleted=%d",
                    uid, r.clusters_found, r.clusters_merged, r.points_deleted,
                )
            except Exception:
                _log.exception("dedup: failed for user=%s", uid)
                results.append(DeduplicationResult())
        return results


def _merge_id(conv_ids: list[str]) -> str:
    """Deterministic merge batch ID from a list of conversation IDs."""
    key = ":".join(sorted(conv_ids))
    return str(uuid.uuid5(_NS, key))
