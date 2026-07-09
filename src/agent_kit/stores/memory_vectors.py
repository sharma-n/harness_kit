"""In-memory VectorStore — brute-force cosine search, the default episodic backend.

Always filters by ``user_id`` so there is no cross-user leakage, matching the
Qdrant adapter's per-user filter. Fine for the prototype's dozens of users;
swap in Qdrant behind the same Protocol for scale.
"""

from __future__ import annotations

import numpy as np

from agent_kit.stores.types import MemoryHit, MemoryPoint


class InMemoryVectorStore:
    """Process-local episodic points with numpy cosine similarity."""

    def __init__(self) -> None:
        self._points: dict[str, MemoryPoint] = {}

    async def add(self, points: list[MemoryPoint]) -> None:
        for point in points:
            self._points[point.id] = point

    async def search(
        self,
        user_id: str,
        query_vector: list[float],
        k: int,
        min_score: float,
    ) -> list[MemoryHit]:
        # Filter to this user *first* — no cross-user candidates ever scored.
        candidates = [
            p for p in self._points.values() if p.payload.get("user_id") == user_id
        ]
        if not candidates or k <= 0:
            return []

        query = np.asarray(query_vector, dtype=np.float32)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0.0:
            return []

        hits: list[MemoryHit] = []
        for point in candidates:
            vec = np.asarray(point.vector, dtype=np.float32)
            vec_norm = float(np.linalg.norm(vec))
            if vec_norm == 0.0:
                continue
            score = float(np.dot(query, vec) / (query_norm * vec_norm))
            if score >= min_score:
                hits.append(MemoryHit(point=point, score=score))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    async def delete(self, point_ids: list[str], *, user_id: str) -> None:
        for pid in point_ids:
            p = self._points.get(pid)
            if p is not None and p.payload.get("user_id") == user_id:
                del self._points[pid]

    async def list_points(
        self,
        user_id: str,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 256,
    ) -> tuple[list[MemoryPoint], str | None]:
        candidates = [
            p for p in self._points.values()
            if p.payload.get("user_id") == user_id
            and (kind is None or p.payload.get("kind") == kind)
        ]
        candidates.sort(key=lambda p: p.payload.get("ts", 0.0))
        # Cursor is a string index; None means start from 0.
        offset = int(cursor) if cursor is not None else 0
        page = candidates[offset : offset + limit]
        # Next cursor is None if we've reached the end, else the next offset.
        next_cursor = str(offset + limit) if len(page) == limit else None
        return page, next_cursor
