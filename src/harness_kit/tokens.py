"""Token estimation — a leaf utility shared across layers.

Lives at the package root (like ``errors`` / ``llm``) so both ``memory/`` and
``agent/`` can size text against a token budget without either importing the
other (the budgeter sits in ``agent/``, working-memory rollover in ``memory/``).

The default is a char/4 heuristic mirroring llm_kit's default estimator; inject a
real tokenizer where precision matters.
"""

from __future__ import annotations

from collections.abc import Callable

Estimator = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    return max(0, len(text) // 4)
