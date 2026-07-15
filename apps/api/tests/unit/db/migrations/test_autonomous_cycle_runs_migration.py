from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260712_0031_add_autonomous_cycle_runs.py"
)


class _FakeOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        sa.Table("autonomous_capital_mandates", self.metadata, sa.Column("mandate_id", postgresql.UUID(as_uuid=True), primary_key=True))
        sa.Table("autonomous_capital_mandate_versions", self.metadata, sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), primary_key=True))
        sa.Table("decision_records", self.metadata, sa.Column("decision_id", postgresql.UUID(as_uuid=True), primary_key=True))
        sa.Table("crypto_order_previews", self.metadata, sa.Column("crypto_order_preview_id", postgresql.UUID(as_uuid=True), primary_key=True))
        sa.Table("autonomous_capital_mandate_evaluations", self.metadata, sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), primary_key=True))
        sa.Table("risk_events", self.metadata, sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True))

        self.created_tables: list[sa.Table] = []
        self.created_indexes: list[str] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.dropped_tables: list[str] = []

    def create_table(self, table_name: str, *elements: sa.Column | sa.Constraint) -> sa.Table:
        table = sa.Table(table_name, self.metadata, *elements)
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        self.created_tables.append(table)
        return table

    def create_index(self, index_name: str, table_name: str, columns: list[str], unique: bool = False) -> sa.Index:
        table = self.metadata.tables[table_name]
        index = sa.Index(index_name, *(table.c[column_name] for column_name in columns), unique=unique)
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.created_indexes.append(index_name)
        return index

    def alter_column(self, table_name: str, column_name: str, **kwargs) -> None:
        _ = (table_name, column_name, kwargs)

    def drop_index(self, index_name: str, table_name: str | None = None) -> None:
        self.dropped_indexes.append((index_name, table_name))

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260712_0031", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260712_0031"
    assert module.down_revision == "20260712_0030"


def test_upgrade_and_downgrade_compile() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    assert {table.name for table in fake_op.created_tables} == {"autonomous_cycle_runs"}
    assert "ix_autonomous_cycle_runs_mandate_created" in fake_op.created_indexes
    assert "ix_autonomous_cycle_runs_state" in fake_op.created_indexes

    module.downgrade()
    assert fake_op.dropped_indexes == [
        ("ix_autonomous_cycle_runs_state", "autonomous_cycle_runs"),
        ("ix_autonomous_cycle_runs_mandate_created", "autonomous_cycle_runs"),
    ]
    assert fake_op.dropped_tables == ["autonomous_cycle_runs"]


def test_campaign_cycle_mandate_nullable_migration_compiles() -> None:
    module_path = (
        Path(__file__).resolve().parents[4]
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "20260715_0038_allow_null_mandate_id_on_autonomous_cycle_runs.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260715_0038", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    module.downgrade()
    assert True
