"""Qdrant-backed VectorStore (SPEC §9.3).

Always filters searches by ``user_id`` (stored in point payload) so there is
zero cross-user leakage, matching the in-memory adapter's invariant.

Client modes (selected by ``mode`` config field):
    "memory"  — QdrantClient(":memory:")   — in-process, no persistence
    "file"    — QdrantClient(path=path)    — file-backed, persists locally
    "host"    — QdrantClient(url=url)      — remote Qdrant instance

Point IDs in agent_kit are arbitrary strings (e.g. "conv:abc123"), but Qdrant
requires IDs to be unsigned integers or UUID-format strings. We derive a
deterministic UUID5 from each string ID so the original is preserved in the
payload under ``_ak_id`` and can be recovered on retrieval.

Collection is created lazily on first use if it does not already exist.
"""

from __future__ import annotations

import asyncio
import uuid

try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False

from agent_kit.stores.types import MemoryHit, MemoryPoint

_AK_ID_KEY = "_ak_id"
_NS = uuid.NAMESPACE_URL


def _to_qdrant_id(point_id: str) -> str:
    return str(uuid.uuid5(_NS, point_id))


def _build_client(mode: str, path: str, url: str) -> AsyncQdrantClient:
    if mode == "memory":
        return AsyncQdrantClient(":memory:")
    if mode == "file":
        return AsyncQdrantClient(path=path)
    return AsyncQdrantClient(url=url)


class QdrantVectorStore:
    """SPEC §9.3 — Qdrant-backed episodic memory, always user-scoped."""

    def __init__(
        self,
        *,
        mode: str = "host",
        path: str = "qdrant_data",
        url: str = "http://localhost:6333",
        collection: str = "episodic_memory",
        vector_size: int = 1536,
    ) -> None:
        if not _QDRANT_AVAILABLE:
            raise ImportError(
                "qdrant backend requires the 'qdrant' extra: uv sync --extra qdrant"
            )
        self._client = _build_client(mode, path, url)
        self._collection = collection
        self._vector_size = vector_size
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_collection(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if not await self._client.collection_exists(self._collection):
                await self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(
                        size=self._vector_size,
                        distance=Distance.COSINE,
                    ),
                )
            self._initialized = True

    async def add(self, points: list[MemoryPoint]) -> None:
        if not points:
            return
        await self._ensure_collection()
        structs = [
            PointStruct(
                id=_to_qdrant_id(p.id),
                vector=p.vector,
                payload={**p.payload, _AK_ID_KEY: p.id},
            )
            for p in points
        ]
        await self._client.upsert(collection_name=self._collection, points=structs)

    async def search(
        self,
        user_id: str,
        query_vector: list[float],
        k: int,
        min_score: float,
    ) -> list[MemoryHit]:
        if k <= 0 or not query_vector:
            return []
        await self._ensure_collection()

        response = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=k,
            score_threshold=min_score,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            with_vectors=True,
            with_payload=True,
        )

        hits: list[MemoryHit] = []
        for scored in response.points:
            payload = dict(scored.payload or {})
            original_id = payload.pop(_AK_ID_KEY, str(scored.id))
            vector = scored.vector or []
            if isinstance(vector, dict):
                # Named vectors — not expected but guard defensively.
                vector = list(next(iter(vector.values()), []))
            hits.append(MemoryHit(
                point=MemoryPoint(id=original_id, vector=list(vector), payload=payload),
                score=scored.score,
            ))
        return hits
