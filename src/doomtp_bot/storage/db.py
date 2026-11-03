"""Postgres connections, and the schema check at startup (ADR-0014, ADR-0022).

One database, two schemas: `bot` holds state and configuration, `chatlog` holds the message log. Each
gets its own connection whose `search_path` points at it, so queries name tables unqualified exactly as
they did when these were two SQLite files, and nothing can join across the two by accident.

Migrations are Alembic revisions under `migrations/<schema>/` (`storage.schema`). The bot doesn't run
them: the `migrate` step does, before it starts, and `Databases.open` only checks that each schema is at
this build's head.

Connections are `autocommit=True`: a read should not leave a transaction open. Writes take an explicit
`transaction()` block, which serializes on a per-connection lock because every writer in the process
shares one connection. A pool belongs with the second process (ADR-0014), not here.

A statement sent on that shared connection while another task holds its lock runs inside that task's
open transaction: it sees the uncommitted rows, and is rolled back with them. Each schema therefore
has a second autocommit connection, `outside(conn)`, that no transaction ever holds (C99). The shared
connection sends every statement there itself unless the task sending it is inside its own
`transaction()` (`SharedConnection`), so a read sees only committed rows and a write outside a
transaction commits on its own, whoever else is mid-transaction.
"""

from __future__ import annotations

import asyncio
import re
import sys
import weakref
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import psycopg
import structlog
from psycopg.rows import dict_row

from doomtp_bot.storage import schema as schema_module

log = structlog.get_logger(__name__)

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

Connection = psycopg.AsyncConnection[dict[str, Any]]
Row = dict[str, Any]
Params = Sequence[Any]


def configure_event_loop() -> None:
    """Pick an event loop psycopg can use. Call before `asyncio.run`, on Windows only in practice.

    Python defaults to the Proactor loop on Windows and psycopg's async mode refuses to run on it. The
    Selector loop it wants cannot spawn asyncio subprocesses — production is Linux, where none of this
    applies, but a dev box that needs both would have to run the database work in its own loop.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def connect(dsn: str, schema: str) -> Connection:
    """Open a connection pinned to `schema`, creating the schema if this is a fresh database."""
    if not _SCHEMA_RE.match(schema):
        # Interpolated into SQL below: it comes from our own package layout, never from input, and this
        # keeps it that way.
        raise ValueError(f"not a usable schema name: {schema!r}")
    conn: Connection = await SharedConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}", public')
    return conn


_WRITE_LOCKS: weakref.WeakKeyDictionary[Connection, asyncio.Lock] = weakref.WeakKeyDictionary()


def write_lock(conn: Connection) -> asyncio.Lock:
    """One lock per connection: every coroutine shares it, so transactions must not interleave."""
    lock = _WRITE_LOCKS.get(conn)
    if lock is None:
        lock = _WRITE_LOCKS[conn] = asyncio.Lock()
    return lock


_OUTSIDE: weakref.WeakKeyDictionary[Connection, Connection] = weakref.WeakKeyDictionary()


#: The connections whose `transaction()` the current task (or one it started inside it) is in.
_HOLDING: ContextVar[tuple[Connection, ...]] = ContextVar("holding", default=())


def outside(conn: Connection) -> Connection:
    """The connection paired with `conn` by `Databases.open` that no transaction() ever holds, or `conn`
    itself when it has none (a test's bare connection)."""
    return _OUTSIDE.get(conn, conn)


class SharedConnection(psycopg.AsyncConnection[dict[str, Any]]):
    """The connection every writer shares. A statement from a task that isn't inside this connection's
    `transaction()` goes to `outside(self)` instead, so it can't land in another task's open one.

    `execute()` and the `fetch_*` helpers all open their cursor here, so this one method routes them.
    """

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        other = _OUTSIDE.get(self)
        if other is not None and not any(held is self for held in _HOLDING.get()):
            return other.cursor(*args, **kwargs)
        return super().cursor(*args, **kwargs)


@asynccontextmanager
async def transaction(conn: Connection) -> AsyncIterator[Connection]:
    """Serialized write transaction: commit on success, roll back on any exception.

    Don't nest these on the same connection — the lock is not reentrant.
    """
    async with write_lock(conn):
        token = _HOLDING.set((*_HOLDING.get(), conn))
        try:
            async with conn.transaction():
                yield conn
        finally:
            _HOLDING.reset(token)


async def fetch_all(conn: Connection, sql: str, params: Params = ()) -> list[Row]:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def fetch_one(conn: Connection, sql: str, params: Params = ()) -> Row | None:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def fetch_value(conn: Connection, sql: str, params: Params = ()) -> Any:
    """First column of the first row, or None. For `SELECT count(*)`-shaped queries."""
    row = await fetch_one(conn, sql, params)
    return None if row is None else next(iter(row.values()))


async def execute(conn: Connection, sql: str, params: Params = ()) -> int:
    """Run a statement and return the number of rows it touched."""
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return cur.rowcount


async def schema_revision(conn: Connection) -> str | None:
    """The Alembic revision this connection's schema is at, or None if it has none."""
    if await fetch_value(conn, "SELECT to_regclass('alembic_version') AS t") is None:
        return None
    value = await fetch_value(conn, "SELECT version_num FROM alembic_version")
    return None if value is None else str(value)


async def check_schema(conn: Connection, schema: str) -> str:
    """Refuse a schema that isn't at this build's head, saying which way to fix it. Returns the revision."""
    expected = schema_module.head(schema)
    revision = await schema_revision(conn)
    if revision == expected:
        return revision
    if revision is None or schema_module.known(schema, revision):
        raise schema_module.SchemaMismatch(
            f"{schema} schema is at {revision or 'nothing'}, this build needs {expected}: "
            "run `doomtp-bot db upgrade` (the migrate step) first"
        )
    raise schema_module.SchemaMismatch(
        f"{schema} schema is at {revision}, which a newer build wrote; this build needs {expected}: "
        "downgrade with the newer image first (scripts/rollback.sh)"
    )


@dataclass
class Databases:
    bot: Connection
    chatlog: Connection

    @classmethod
    async def open(cls, dsn: str, *, migrate: bool = False, pair: bool = True) -> Databases:
        """Connect to both schemas and check them. `migrate` upgrades them first: tests and dev tools
        only, since a deployed bot leaves that to the migrate step (ADR-0022). `pair=False` skips the
        `outside` connections, for tests whose data lives in one transaction that only `bot` can see."""
        if migrate:
            await asyncio.to_thread(schema_module.upgrade, dsn)
        opened: list[Connection] = []
        try:
            for schema in ("bot", "chatlog"):
                opened.append(await connect(dsn, schema))
                await check_schema(opened[-1], schema)
                if pair:
                    _OUTSIDE[opened[-1]] = await connect(dsn, schema)
        except BaseException:
            for conn in opened:
                await outside(conn).close()
                await conn.close()
            raise
        return cls(bot=opened[0], chatlog=opened[1])

    async def close(self) -> None:
        for conn in (self.bot, self.chatlog):
            if (other := outside(conn)) is not conn:
                await other.close()
            await conn.close()
