from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable

from sqlalchemy import BigInteger, Table, event
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.dialects.sqlite.base import SQLiteDDLCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# Production runs on Postgres-only JSONB/UUID column types and now()/gen_random_uuid()
# server defaults. These compiler overrides plus the sqlite user-defined functions below
# let REAL app models + REAL SQLAlchemy ORM event listeners (e.g. the before_update
# immutability guard on AutonomousCapitalMandateVersion) run against a REAL AsyncSession
# (sqlite+aiosqlite), instead of a hand-rolled fake session that would silently skip
# mapper-level events entirely. Shared by any test module that needs a real, in-memory,
# production-model-shaped session -- register once here rather than per test file.


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


# sqlite only auto-generates a rowid (autoincrement) primary key value for the exact
# type keyword "INTEGER"; a BIGINT-affinity column (what BigInteger compiles to by
# default) does not get that treatment, so an autoincrement BigInteger PK would insert
# as NULL and trip its NOT NULL constraint under sqlite even though it's fine on Postgres.
@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(element, compiler, **kw) -> str:
    return "INTEGER"


# sqlite's DDL grammar requires any non-literal column DEFAULT (e.g. a function call)
# to be parenthesized -- "DEFAULT gen_random_uuid()" is a syntax error, it must be
# "DEFAULT (gen_random_uuid())". Postgres has no such requirement, so the models'
# server_default=text("gen_random_uuid()")/text("now()") are correct there but need
# this compiler patch to emit valid CREATE TABLE DDL under sqlite for tests.
_original_get_column_default_string = SQLiteDDLCompiler.get_column_default_string


def _parenthesize_function_defaults(self, column):  # type: ignore[no-untyped-def]
    rendered = _original_get_column_default_string(self, column)
    if rendered is None:
        return rendered
    # Postgres-only `::type` casts (e.g. "'[]'::jsonb") are invalid inside a sqlite
    # DEFAULT clause; sqlite has no cast operator there, so just drop the cast --
    # the literal on its own is all sqlite needs (and all a DEFAULT can express).
    rendered = re.sub(r"::\w+", "", rendered)
    if "(" in rendered and not rendered.startswith("("):
        return f"({rendered})"
    return rendered


SQLiteDDLCompiler.get_column_default_string = _parenthesize_function_defaults


@asynccontextmanager
async def real_sqlite_session(tables: Iterable[Table]) -> AsyncIterator[AsyncSession]:
    """A real, in-memory SQLAlchemy AsyncSession (sqlite+aiosqlite) with the production
    ORM models' DDL, event listeners, and server defaults all genuinely exercised --
    not a hand-rolled fake. Pass the exact list of model __table__s this test needs."""
    tables = list(tables)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        # SQLAlchemy's postgresql.UUID(as_uuid=True) falls back to a character-based
        # impl on non-Postgres dialects whose bind_processor encodes Python UUID
        # objects as 32-char hex with NO dashes (value.hex). A server-side default
        # that returns str(uuid.uuid4()) (36 chars, dashed) would insert a value that
        # later WHERE-clause lookups (UPDATE/refresh/SELECT keyed on that same UUID)
        # can never match, since they bind the .hex form -- so this must match it.
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid.uuid4().hex)

    async with engine.begin() as conn:
        await conn.run_sync(tables[0].metadata.create_all, tables=tables)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
