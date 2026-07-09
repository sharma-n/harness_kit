"""Utility helpers (leaves, like tokens.py and retry.py).

These are simple, dependency-light utilities shared across multiple layers.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedLRUDict(Generic[K, V]):
    """A dict that evicts the oldest entry when size exceeds a cap.

    Accesses (get/set) move the touched key to the end (most recent) so
    the next eviction removes the least-recently-used entry.
    """

    __slots__ = ("_dict", "_max_size")

    def __init__(self, max_size: int) -> None:
        self._dict: OrderedDict[K, V] = OrderedDict()
        self._max_size = max_size

    def get(self, key: K, default: V | None = None) -> V | None:
        """Get a value by key, moving it to end (most recent) if present."""
        if key in self._dict:
            self._dict.move_to_end(key)
            return self._dict[key]
        return default

    def __setitem__(self, key: K, value: V) -> None:
        """Set a value by key, evicting the oldest entry if capacity exceeded."""
        if key in self._dict:
            self._dict.move_to_end(key)
        self._dict[key] = value
        if len(self._dict) > self._max_size:
            self._dict.popitem(last=False)  # Remove the oldest (first) entry

    def __getitem__(self, key: K) -> V:
        """Get a value by key, moving it to end (most recent) if present."""
        self._dict.move_to_end(key)
        return self._dict[key]

    def __contains__(self, key: K) -> bool:
        return key in self._dict
