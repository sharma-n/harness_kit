"""SQLite-backed PermissionStore via SQLAlchemy Core + aiosqlite.

Swapping to Postgres is a connection-string change only.

Table schema:
    permissions(user_id TEXT PK, granted_json TEXT, revoked_json TEXT)

Per-user allowed tools are computed as:
  allowed = (default ∪ granted) − revoked

The global default fallback is stored as a sentinel row with user_id='__default__'.
A user with no explicit row in the table falls back to this row, then union with
any explicit grants and subtract explicit revokes, mirroring the in-memory model.

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
            Column("granted_json", Text, nullable=False),
            Column("revoked_json", Text, nullable=False),
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
                            granted_json=json.dumps(list(self._default_allowed)),
                            revoked_json=json.dumps([]),
                        )
                    )
            self._initialized = True

    async def _get_default(self, conn) -> set[str]:
        row = (await conn.execute(
            select(self._table).where(self._table.c.user_id == _DEFAULT_SENTINEL)
        )).first()
        if row is None:
            return set(self._default_allowed)
        return set(json.loads(row.granted_json))

    async def _get_user_grants_revokes(self, conn, user_id: str) -> tuple[set[str], set[str]] | None:
        """Return (granted, revoked) for a user, or None if no explicit row."""
        row = (await conn.execute(
            select(self._table).where(self._table.c.user_id == user_id)
        )).first()
        if row is None:
            return None
        granted = set(json.loads(row.granted_json))
        revoked = set(json.loads(row.revoked_json))
        return granted, revoked

    async def _upsert(self, conn, user_id: str, granted: set[str], revoked: set[str]) -> None:
        existing = await self._get_user_grants_revokes(conn, user_id)
        if existing is None:
            await conn.execute(
                self._table.insert().values(
                    user_id=user_id,
                    granted_json=json.dumps(list(granted)),
                    revoked_json=json.dumps(list(revoked)),
                )
            )
        else:
            await conn.execute(
                self._table.update()
                .where(self._table.c.user_id == user_id)
                .values(
                    granted_json=json.dumps(list(granted)),
                    revoked_json=json.dumps(list(revoked)),
                )
            )

    async def allowed_tools(self, user_id: str) -> set[str]:
        await self._ensure_init()
        async with self._engine.connect() as conn:
            default = await self._get_default(conn)
            user_deltas = await self._get_user_grants_revokes(conn, user_id)
            if user_deltas is None:
                return default
            granted, revoked = user_deltas
            allowed = (default | granted) - revoked
            return allowed

    async def grant(self, user_id: str, tools: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            user_deltas = await self._get_user_grants_revokes(conn, user_id)
            if user_deltas is None:
                granted, revoked = set(), set()
            else:
                granted, revoked = user_deltas
            granted.update(tools)
            revoked.difference_update(tools)
            await self._upsert(conn, user_id, granted, revoked)

    async def revoke(self, user_id: str, tools: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            user_deltas = await self._get_user_grants_revokes(conn, user_id)
            if user_deltas is None:
                granted, revoked = set(), set()
            else:
                granted, revoked = user_deltas
            revoked.update(tools)
            granted.difference_update(tools)
            await self._upsert(conn, user_id, granted, revoked)

    async def extend_default_allowed(self, names: set[str]) -> None:
        await self._ensure_init()
        async with self._engine.begin() as conn:
            current = await self._get_default(conn)
            current.update(names)
            await conn.execute(
                self._table.update()
                .where(self._table.c.user_id == _DEFAULT_SENTINEL)
                .values(granted_json=json.dumps(list(current)))
            )
