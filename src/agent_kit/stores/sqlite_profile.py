"""SQLite-backed ProfileStore via SQLAlchemy Core + aiosqlite (SPEC §9.2).

Swapping to Postgres is a connection-string change only:
    sqlite+aiosqlite:///agent_kit.db  →  postgresql+asyncpg://user:pw@host/db

Table schema:
    profiles(user_id TEXT PK, facts_json TEXT, updated_at REAL)

Lazy schema creation on first access via an asyncio.Lock so __init__ stays
synchronous (the stores/ layer has no astart() hook).
"""

from __future__ import annotations

import asyncio
import json
import time

from sqlalchemy import Column, Float, MetaData, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_kit.stores.types import UserProfile


class SqliteProfileStore:
    """SPEC §9.2 — user profiles persisted in SQLite (or Postgres via URL swap)."""

    def __init__(self, url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(url, future=True)
        self._metadata = MetaData()
        self._table = Table(
            "profiles",
            self._metadata,
            Column("user_id", Text, primary_key=True),
            Column("facts_json", Text, nullable=False),
            Column("updated_at", Float, nullable=False),
        )
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with self._engine.begin() as conn:
                await conn.run_sync(self._metadata.create_all)
            self._initialized = True

    async def get(self, user_id: str) -> UserProfile:
        await self._ensure_init()
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(self._table).where(self._table.c.user_id == user_id)
            )).first()

        if row is None:
            now = time.time()
            async with self._engine.begin() as conn:
                await conn.execute(
                    self._table.insert().prefix_with("OR IGNORE").values(
                        user_id=user_id,
                        facts_json="{}",
                        updated_at=now,
                    )
                )
            return UserProfile(user_id=user_id, facts={}, updated_at=now)

        return UserProfile(
            user_id=row.user_id,
            facts=json.loads(row.facts_json),
            updated_at=row.updated_at,
        )

    async def upsert_facts(self, user_id: str, facts: dict) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                select(self._table).where(self._table.c.user_id == user_id)
            )).first()

            if row is None:
                merged = dict(facts)
                await conn.execute(
                    self._table.insert().values(
                        user_id=user_id,
                        facts_json=json.dumps(merged),
                        updated_at=time.time(),
                    )
                )
            else:
                existing = json.loads(row.facts_json)
                existing.update(facts)
                await conn.execute(
                    self._table.update()
                    .where(self._table.c.user_id == user_id)
                    .values(facts_json=json.dumps(existing), updated_at=time.time())
                )

    async def forget_facts(self, user_id: str, keys: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                select(self._table).where(self._table.c.user_id == user_id)
            )).first()

            if row is None:
                return  # unknown user — silent no-op

            existing = json.loads(row.facts_json)
            changed = False
            for k in keys:
                if k in existing:
                    del existing[k]
                    changed = True

            if changed:
                await conn.execute(
                    self._table.update()
                    .where(self._table.c.user_id == user_id)
                    .values(facts_json=json.dumps(existing), updated_at=time.time())
                )
