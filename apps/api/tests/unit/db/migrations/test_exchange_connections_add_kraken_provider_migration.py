from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.schemas.exchange_connections import SaveExchangeConnectionRequest


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260710_0028_exchange_connections_add_kraken_provider.py"
)


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeBind:
    def __init__(self, *, kraken_rows: int = 0) -> None:
        self.kraken_rows = kraken_rows
        self.executed_sql: list[str] = []

    def execute(self, statement):
        self.executed_sql.append(str(statement))
        return _FakeResult(self.kraken_rows)


class _FakeOp:
    def __init__(self, *, kraken_rows: int = 0) -> None:
        self.bind = _FakeBind(kraken_rows=kraken_rows)
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.created_constraints: list[tuple[str, str, str]] = []

    def get_bind(self):
        return self.bind

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, table_name, type_))

    def create_check_constraint(self, name: str, table_name: str, sqltext: str) -> None:
        self.created_constraints.append((name, table_name, sqltext))


def _load_migration_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260710_0028", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_is_correct() -> None:
    module = _load_migration_module()
    assert module.revision == "20260710_0028"
    assert module.down_revision == "20260710_0027"


def test_upgrade_expands_provider_constraint_to_include_kraken() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    assert fake_op.dropped_constraints == [
        ("ck_exchange_connections_provider", "exchange_connections", "check")
    ]
    assert fake_op.created_constraints == [
        (
            "ck_exchange_connections_provider",
            "exchange_connections",
            "provider IN ('coinbase_advanced','kraken_spot')",
        )
    ]


def test_downgrade_restores_coinbase_only_constraint_when_no_kraken_rows() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(kraken_rows=0)
    module.op = fake_op

    module.downgrade()

    assert fake_op.created_constraints[-1] == (
        "ck_exchange_connections_provider",
        "exchange_connections",
        "provider IN ('coinbase_advanced')",
    )


def test_downgrade_fails_when_kraken_rows_exist() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(kraken_rows=1)
    module.op = fake_op

    with pytest.raises(RuntimeError, match="contains kraken_spot rows"):
        module.downgrade()


def test_model_constraint_compiles_with_kraken_provider() -> None:
    from app.models import exchange_connection  # noqa: F401
    from app.db.base import Base

    table = Base.metadata.tables["exchange_connections"]
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "coinbase_advanced" in ddl
    assert "kraken_spot" in ddl


def test_schema_accepts_coinbase_and_kraken_and_rejects_unknown_provider() -> None:
    coinbase = SaveExchangeConnectionRequest(
        provider="coinbase_advanced",
        connection_name="Coinbase Prod",
        environment="production",
        api_key_name="k",
        private_key="s",
    )
    kraken = SaveExchangeConnectionRequest(
        provider="kraken_spot",
        connection_name="Kraken Prod",
        environment="production",
        api_key_name="k",
        private_key="s",
    )

    assert coinbase.provider == "coinbase_advanced"
    assert kraken.provider == "kraken_spot"

    with pytest.raises(ValidationError):
        SaveExchangeConnectionRequest(
            provider="unknown_provider",
            connection_name="Unknown",
            environment="production",
            api_key_name="k",
            private_key="s",
        )
