"""Store-write retry at the memory-method boundary.

Each background op is compound (LLM/embed → store-write); retry must wrap ONLY the
store write so a store fault never re-runs the already-succeeded model call. These
tests use flaky store doubles (fail N times then succeed) and counting LLM/embedder
doubles to assert exactly that, plus that exhaustion surfaces as ``StoreWriteError``.
"""

from __future__ import annotations

import pytest

from harness_kit.config import EpisodicMemoryConfig, FactualMemoryConfig, WorkingMemoryConfig
from harness_kit.errors import StoreWriteError
from harness_kit.memory.episodic import EpisodicMemory
from harness_kit.memory.factual import ExtractedFacts, FactualMemory
from harness_kit.memory.working import RolledSummary, WorkingMemory
from harness_kit.retry import RetryPolicy
from harness_kit.stores.memory_profile import InMemoryProfileStore
from harness_kit.stores.memory_session import InMemorySessionStore
from harness_kit.stores.memory_vectors import InMemoryVectorStore
from harness_kit.stores.types import SessionState, Turn

from tests.conftest import FakeEmbedder, FakeLLM

FAST = RetryPolicy(max_retries=3, backoff_base_seconds=0.0, jitter_seconds=0.0)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr("harness_kit.retry.asyncio.sleep", _instant)


# ------------------------------------------------------------- counting doubles


class CountingLLM(FakeLLM):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.invoke_count = 0

    async def invoke(self, *args, **kwargs):
        self.invoke_count += 1
        return await super().invoke(*args, **kwargs)


class CountingEmbedder(FakeEmbedder):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.embed_count = 0

    async def embed_one(self, text: str):
        self.embed_count += 1
        return await super().embed_one(text)


# --------------------------------------------------------------- flaky stores


class FlakyProfileStore(InMemoryProfileStore):
    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self._fail_times = fail_times
        self.attempts = 0

    async def upsert_facts(self, user_id: str, facts: dict) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise ConnectionError("flaky upsert_facts")
        await super().upsert_facts(user_id, facts)


class FlakySessionStore(InMemorySessionStore):
    def __init__(self, fail_method: str, fail_times: int) -> None:
        super().__init__()
        self._fail_method = fail_method
        self._fail_times = fail_times
        self.attempts = 0

    async def save(self, conversation_id: str, state) -> None:
        if self._fail_method == "save":
            self.attempts += 1
            if self.attempts <= self._fail_times:
                raise ConnectionError("flaky save")
        await super().save(conversation_id, state)

    async def mark_finalized(self, conversation_id: str) -> None:
        if self._fail_method == "mark_finalized":
            self.attempts += 1
            if self.attempts <= self._fail_times:
                raise ConnectionError("flaky mark_finalized")
        await super().mark_finalized(conversation_id)


class FlakyVectorStore(InMemoryVectorStore):
    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self._fail_times = fail_times
        self.attempts = 0

    async def add(self, points) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise ConnectionError("flaky add")
        await super().add(points)


# ----------------------------------------------------------------- factual.extract


async def test_extract_retries_store_write_without_rerunning_llm():
    store = FlakyProfileStore(fail_times=2)
    llm = CountingLLM(invoke_parsed=ExtractedFacts(facts={"diet": "vegetarian"}))
    fm = FactualMemory(store, FactualMemoryConfig(), llm=llm, store_retry=FAST)

    await fm.extract("alice", "I'm vegetarian", "noted")

    assert store.attempts == 3  # 2 failures + 1 success
    assert llm.invoke_count == 1  # the LLM call is NOT re-run on store retry
    profile = await store.get("alice")
    assert profile.facts["diet"] == "vegetarian"


async def test_extract_exhaustion_raises_store_write_error():
    store = FlakyProfileStore(fail_times=99)
    llm = CountingLLM(invoke_parsed=ExtractedFacts(facts={"k": "v"}))
    fm = FactualMemory(store, FactualMemoryConfig(), llm=llm, store_retry=FAST)

    with pytest.raises(StoreWriteError) as exc_info:
        await fm.extract("alice", "x", "y")
    assert exc_info.value.operation == "factual.extract"
    assert llm.invoke_count == 1


# --------------------------------------------------------------- working.rollover


async def _seed(store, convo, user, texts):
    await store.save(convo, SessionState(user_id=user))
    for text in texts:
        await store.append_turn(convo, Turn(role="user", text=text))


async def test_rollover_retries_save_without_rerunning_summarizer():
    store = FlakySessionStore(fail_method="save", fail_times=2)
    llm = CountingLLM(invoke_parsed=RolledSummary(summary="rolled"))
    wm = WorkingMemory(
        store, WorkingMemoryConfig(buffer_token_budget=4), llm=llm, store_retry=FAST
    )
    # Seed via the parent's save so setup does not trip the flaky save override.
    await InMemorySessionStore.save(store, "c1", SessionState(user_id="alice"))
    for i in range(5):
        await store.append_turn("c1", Turn(role="user", text=f"message number {i}"))

    await wm.maybe_rollover("c1", "alice")

    assert store.attempts == 3
    assert llm.invoke_count == 1  # summarizer not re-run on store retry
    state = await store.load("c1", "alice")
    assert state.rolling_summary == "rolled"


async def test_mark_finalized_retries_then_succeeds():
    store = FlakySessionStore(fail_method="mark_finalized", fail_times=2)
    wm = WorkingMemory(store, WorkingMemoryConfig(), store_retry=FAST)
    await _seed(store, "c1", "alice", ["hello"])

    await wm.mark_finalized("c1")

    assert store.attempts == 3
    state = await store.load("c1", "alice")
    assert state.finalized_at is not None


async def test_mark_finalized_exhaustion_raises_store_write_error():
    store = FlakySessionStore(fail_method="mark_finalized", fail_times=99)
    wm = WorkingMemory(store, WorkingMemoryConfig(), store_retry=FAST)
    await _seed(store, "c1", "alice", ["hello"])

    with pytest.raises(StoreWriteError) as exc_info:
        await wm.mark_finalized("c1")
    assert exc_info.value.operation == "working.mark_finalized"


# ------------------------------------------------------- episodic.write_conversation


async def test_write_conversation_retries_add_without_rerunning_embedder():
    store = FlakyVectorStore(fail_times=2)
    embedder = CountingEmbedder()
    em = EpisodicMemory(store, embedder, EpisodicMemoryConfig(), store_retry=FAST)

    await em.write_conversation(
        "alice", "c1", "summary", [Turn(role="user", text="hi there")]
    )

    assert store.attempts == 3
    assert embedder.embed_count == 1  # embedding NOT re-run on store retry
    assert len(store._points) == 1


async def test_write_conversation_exhaustion_raises_store_write_error():
    store = FlakyVectorStore(fail_times=99)
    embedder = CountingEmbedder()
    em = EpisodicMemory(store, embedder, EpisodicMemoryConfig(), store_retry=FAST)

    with pytest.raises(StoreWriteError) as exc_info:
        await em.write_conversation(
            "alice", "c1", "summary", [Turn(role="user", text="hi")]
        )
    assert exc_info.value.operation == "episodic.write_conversation"
    assert embedder.embed_count == 1
