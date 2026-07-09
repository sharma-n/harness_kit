"""Shared helpers for M8 offline batch jobs."""

from __future__ import annotations

from harness_kit.stores.base import VectorStore
from harness_kit.stores.types import MemoryPoint


async def load_all_user_points(
    store: VectorStore,
    user_id: str,
    kind: str | None = None,
    page_size: int = 256,
) -> list[MemoryPoint]:
    """Collect all points for a user via paginated ``list_points`` calls."""
    all_points: list[MemoryPoint] = []
    cursor = None
    while True:
        page, next_cursor = await store.list_points(user_id, kind=kind, cursor=cursor, limit=page_size)
        all_points.extend(page)
        if next_cursor is None:
            break
        cursor = next_cursor
    return all_points
