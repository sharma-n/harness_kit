"""SQLite-backed PermissionStore via SQLAlchemy Core + aiosqlite.

Swapping to Postgres is a connection-string change only.

Table schema:
    permissions(user_id TEXT PK, allowed_json TEXT)

The global default fallback is stored as a sentinel row with
user_id='__default__'. A user with no explicit row in the table falls back to
this row, mirroring the in-memory two-tier (default + per-user grants) model.

Lazy schema creation on first access via an asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

try:
    from sqlalchemy import Column, MetaData, Table, Text, select
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
    _SQLALCHEMY_AVAILABLE = True
except ImportError:
    _SQLALCHEMY_AVAILABLE = False

_DEFAULT_SENTINEL = "__default__"


class SqlitePermissionStore:
    """Per-user tool allowlist persisted in SQLite, default-fallback aware."""

    def __init__(self, url: str, default_allowed: Iterable[str] = ()) -> None:
        if not _SQLALCHEMY_AVAILABLE:
            raise ImportError(
                "sqlite backend requires the 'sqlite' extra: uv sync --extra sqlite"
            )
        self._engine: AsyncEngine = create_async_engine(url, future=True)
        self._default_allowed = set(default_allowed)
        self._metadata = MetaData()
        self._table = Table(
            "permissions",
            self._metadata,
            Column("user_id", Text, primary_key=True),
            Column("allowed_json", Text, nullable=False),
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
                # Seed the default row if it doesn't exist.
                existing = (await conn.execute(
                    select(self._table).where(
                        self._table.c.user_id == _DEFAULT_SENTINEL
                    )
                )).first()
                if existing is None:
                    await conn.execute(
                        self._table.insert().values(
                            user_id=_DEFAULT_SENTINEL,
                            allowed_json=json.dumps(list(self._default_allowed)),
                        )
                    )
            self._initialized = True

    async def _get_default(self, conn) -> set[str]:
        row = (await conn.execute(
            select(self._table).where(self._table.c.user_id == _DEFAULT_SENTINEL)
        )).first()
        if row is None:
            return set(self._default_allowed)
        return set(json.loads(row.allowed_json))

    async def _get_user(self, conn, user_id: str) -> set[str] | None:
        row = (await conn.execute(
            select(self._table).where(self._table.c.user_id == user_id)
        )).first()
        if row is None:
            return None
        return set(json.loads(row.allowed_json))

    async def _upsert(self, conn, user_id: str, allowed: set[str]) -> None:
        existing = await self._get_user(conn, user_id)
        if existing is None:
            await conn.execute(
                self._table.insert().values(
                    user_id=user_id,
                    allowed_json=json.dumps(list(allowed)),
                )
            )
        else:
            await conn.execute(
                self._table.update()
                .where(self._table.c.user_id == user_id)
                .values(allowed_json=json.dumps(list(allowed)))
            )

    async def allowed_tools(self, user_id: str) -> set[str]:
        await self._ensure_init()
        async with self._engine.connect() as conn:
            user_set = await self._get_user(conn, user_id)
            if user_set is not None:
                return user_set
            return await self._get_default(conn)

    async def grant(self, user_id: str, tools: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            current = await self._get_user(conn, user_id)
            if current is None:
                current = await self._get_default(conn)
            current.update(tools)
            await self._upsert(conn, user_id, current)

    async def revoke(self, user_id: str, tools: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            current = await self._get_user(conn, user_id)
            if current is None:
                current = await self._get_default(conn)
            current.difference_update(tools)
            await self._upsert(conn, user_id, current)

    async def extend_default_allowed(self, names: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            current = await self._get_default(conn)
            current.update(names)
            await conn.execute(
                self._table.update()
                .where(self._table.c.user_id == _DEFAULT_SENTINEL)
                .values(allowed_json=json.dumps(list(current)))
            )
