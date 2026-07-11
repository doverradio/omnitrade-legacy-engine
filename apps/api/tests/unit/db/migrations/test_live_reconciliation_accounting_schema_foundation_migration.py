from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260710_0027_live_reconciliation_accounting_schema_foundation.py"
)


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeBind:
    def __init__(self, *, null_provider_order_count: int = 0) -> None:
        self.null_provider_order_count = null_provider_order_count
        self.executed_sql: list[str] = []

    def execute(self, statement):
        self.executed_sql.append(str(statement))
        return _FakeResult(self.null_provider_order_count)


class _FakeOp:
    def __init__(self, *, null_provider_order_count: int = 0) -> None:
        self.bind = _FakeBind(null_provider_order_count=null_provider_order_count)
        self.added_columns: list[tuple[str, str]] = []
        self.altered_columns: list[tuple[str, str, dict[str, object]]] = []
        self.created_foreign_keys: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...], str | None]] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.created_unique_constraints: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.created_check_constraints: list[tuple[str, str, str]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def get_bind(self):
        return self.bind

    def add_column(self, table_name: str, column: sa.Column) -> None:
        self.added_columns.append((table_name, column.name))

    def alter_column(self, table_name: str, column_name: str, **kwargs) -> None:
        self.altered_columns.append((table_name, column_name, kwargs))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        ondelete: str | None = None,
    ) -> None:
        self.created_foreign_keys.append((name, source_table, referent_table, tuple(local_cols), tuple(remote_cols), ondelete))

    def create_index(self, name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
        _ = unique
        self.created_indexes.append((name, table_name, tuple(columns)))

    def create_unique_constraint(self, name: str, table_name: str, columns: list[str]) -> None:
        self.created_unique_constraints.append((name, table_name, tuple(columns)))

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, table_name, type_))

    def create_check_constraint(self, name: str, table_name: str, sqltext: str) -> None:
        self.created_check_constraints.append((name, table_name, sqltext))

    def drop_index(self, name: str, table_name: str) -> None:
        self.dropped_indexes.append((name, table_name))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))


def _load_migration_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260710_0027", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_imports_and_revision_chain_are_correct() -> None:
    module = _load_migration_module()

    assert module.revision == "20260710_0027"
    assert module.down_revision == "20260710_0026"


def test_upgrade_adds_live_order_and_campaign_correlation_columns_and_constraints() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    assert ("live_reconciliation_events", "live_crypto_order_id") in fake_op.added_columns
    assert ("live_reconciliation_events", "capital_campaign_id") in fake_op.added_columns
    assert ("live_reconciliation_events", "provider_recorded_at") in fake_op.added_columns
    assert ("live_accounting_records", "live_crypto_order_id") in fake_op.added_columns
    assert ("live_accounting_records", "capital_campaign_id") in fake_op.added_columns
    assert ("live_accounting_records", "provider_fill_timestamp") in fake_op.added_columns

    assert (
        "uq_lar_provider_fill_record",
        "live_accounting_records",
        ("provider_order_id", "provider_fill_id", "record_type"),
    ) in fake_op.created_unique_constraints
    assert any(name == "ck_live_reconciliation_events_reconciliation_status" for name, _table, _sql in fake_op.created_check_constraints)


def test_downgrade_restores_old_status_constraint_and_drops_added_schema() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(null_provider_order_count=0)
    module.op = fake_op

    module.downgrade()

    assert ("uq_lar_provider_fill_record", "live_accounting_records", "unique") in fake_op.dropped_constraints
    assert any(name == "ck_live_reconciliation_events_reconciliation_status" and "open','partially_filled','filled','canceled','rejected" in sql for name, _table, sql in fake_op.created_check_constraints)
    assert ("live_reconciliation_events", "provider_recorded_at") in fake_op.dropped_columns
    assert ("live_accounting_records", "provider_fill_timestamp") in fake_op.dropped_columns


def test_downgrade_blocks_when_nullable_provider_order_rows_exist() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(null_provider_order_count=1)
    module.op = fake_op

    with pytest.raises(RuntimeError, match="provider_order_id contains NULL rows"):
        module.downgrade()


def test_models_compile_with_postgresql_and_include_new_constraints() -> None:
    from app.models import capital_campaign  # noqa: F401
    from app.models import live_accounting_record  # noqa: F401
    from app.models import live_crypto_order  # noqa: F401
    from app.models import live_execution_event  # noqa: F401
    from app.models import live_reconciliation_event  # noqa: F401
    from app.models import live_trading_profile  # noqa: F401
    from app.db.base import Base

    reconciliation_table = Base.metadata.tables["live_reconciliation_events"]
    accounting_table = Base.metadata.tables["live_accounting_records"]

    str(CreateTable(reconciliation_table).compile(dialect=postgresql.dialect()))
    str(CreateTable(accounting_table).compile(dialect=postgresql.dialect()))

    assert "live_crypto_order_id" in reconciliation_table.c
    assert "capital_campaign_id" in reconciliation_table.c
    assert "provider_recorded_at" in reconciliation_table.c
    assert reconciliation_table.c.provider_order_id.nullable is True

    assert "live_crypto_order_id" in accounting_table.c
    assert "capital_campaign_id" in accounting_table.c
    assert "provider_fill_timestamp" in accounting_table.c
    assert any(constraint.name == "uq_lar_provider_fill_record" for constraint in accounting_table.constraints)