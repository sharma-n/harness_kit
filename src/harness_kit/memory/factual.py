"""Factual memory (SPEC §7.3): per-user structured profile over SQLite.

Read is hot (every turn) → a compact profile block. Writes are off the hot path:
post-turn extraction (``invoke`` + ``response_model``) and the model-driven
``remember_fact`` tool both land in ``ProfileStore.upsert_facts``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from harness_kit.config import FactualMemoryConfig
from harness_kit.llm import LLM
from harness_kit.retry import RetryPolicy, store_write
from harness_kit.stores.base import ProfileStore
from harness_kit.stores.types import UserProfile


class ExtractedFacts(BaseModel):
    """Durable facts pulled from a turn (SPEC §7.3 extraction)."""

    facts: dict[str, str] = Field(default_factory=dict)


class FactualMemory:
    def __init__(
        self,
        profile_store: ProfileStore,
        cfg: FactualMemoryConfig,
        *,
        llm: LLM | None = None,
        store_retry: RetryPolicy | None = None,
    ) -> None:
        self._store = profile_store
        self._cfg = cfg
        self._llm = llm  # only needed for extraction
        self._store_retry = store_retry or RetryPolicy()

    async def get(self, user_id: str) -> UserProfile:
        return await self._store.get(user_id)

    async def remember(self, user_id: str, key: str, value: str) -> None:
        """Tool-driven write: the most reliable capture path (SPEC §8)."""
        await self._store.upsert_facts(user_id, {key: value})

    async def forget(self, user_id: str, key: str) -> bool:
        """Tool-driven delete. Returns whether the fact existed before removal."""
        profile = await self._store.get(user_id)
        existed = key in profile.facts
        if existed:
            await self._store.forget_facts(user_id, {key})
        return existed

    async def extract(self, user_id: str, user_text: str, assistant_text: str) -> None:
        """Post-turn extraction of durable facts (off the hot path)."""
        if not self._cfg.extraction_enabled or self._llm is None:
            return
        from llm_kit import Message

        resp = await self._llm.invoke(
            [
                Message.system(self._cfg.extraction_system_prompt),
                Message.user(f"User said: {user_text}\nAssistant said: {assistant_text}"),
            ],
            response_model=ExtractedFacts,
        )
        if resp.parsed is not None and resp.parsed.facts:  # type: ignore[attr-defined]
            facts = dict(resp.parsed.facts)  # type: ignore[attr-defined]
            # Retry only the store write; the LLM invoke above is already retried by
            # llm_kit, and re-running it on a store fault would waste a model call.
            await store_write(
                lambda: self._store.upsert_facts(user_id, facts),
                policy=self._store_retry,
                operation="factual.extract",
            )
