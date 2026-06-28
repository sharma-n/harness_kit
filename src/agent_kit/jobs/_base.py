"""Shared helpers for M8 offline batch jobs."""

from __future__ import annotations

from agent_kit.stores.base import VectorStore
from agent_kit.stores.types import MemoryPoint


async def load_all_user_points(
    store: VectorStore,
    user_id: str,
    kind: str | None = None,
    page_size: int = 256,
) -> list[MemoryPoint]:
    """Collect all points for a user via paginated ``list_points`` calls."""
    all_points: list[MemoryPoint] = []
    offset = 0
    while True:
        page = await store.list_points(user_id, kind=kind, offset=offset, limit=page_size)
        all_points.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return all_points
