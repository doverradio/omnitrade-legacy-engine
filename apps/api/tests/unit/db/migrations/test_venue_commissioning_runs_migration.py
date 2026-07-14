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
    / "20260714_0035_add_venue_commissioning_runs.py"
)

_IDENTIFIER_LIMIT = 63


class _FakeOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.created_tables: list[sa.Table] = []
        self.created_indexes: list[str] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.dropped_tables: list[str] = []

    def create_table(self, table_name: str, *elements: sa.Column | sa.Constraint) -> sa.Table:
        table = sa.Table(table_name, self.metadata, *elements)
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        self.created_tables.append(table)
        return table

    def create_index(
        self,
        index_name: str,
        table_name: str,
        columns: list[str],
        unique: bool = False,
        **kwargs,
    ) -> sa.Index:
        table = self.metadata.tables[table_name]
        index = sa.Index(index_name, *(table.c[column_name] for column_name in columns), unique=unique, **kwargs)
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.created_indexes.append(index_name)
        return index

    def drop_index(self, index_name: str, table_name: str | None = None) -> None:
        self.dropped_indexes.append((index_name, table_name))

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260714_0035", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _named_constraints(table: sa.Table) -> list[str]:
    names: list[str] = []
    for constraint in table.constraints:
        if constraint.name:
            names.append(constraint.name)
    return names


def _assert_identifier_lengths(identifier_names: list[str]) -> None:
    too_long = [(name, len(name)) for name in identifier_names if len(name) > _IDENTIFIER_LIMIT]
    assert not too_long, f"Identifiers exceed {_IDENTIFIER_LIMIT} chars: {too_long}"


def test_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260714_0035"
    assert module.down_revision == "20260713_0034"


def test_upgrade_and_downgrade_compile() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    assert {table.name for table in fake_op.created_tables} == {"venue_commissioning_runs"}
    assert fake_op.created_indexes == [
        "uq_vcr_active_scope",
        "ix_vcr_status_created",
        "ix_vcr_buy_client_order_id",
        "ix_vcr_sell_client_order_id",
    ]

    module.downgrade()
    assert fake_op.dropped_indexes == [
        ("ix_vcr_sell_client_order_id", "venue_commissioning_runs"),
        ("ix_vcr_buy_client_order_id", "venue_commissioning_runs"),
        ("ix_vcr_status_created", "venue_commissioning_runs"),
        ("uq_vcr_active_scope", "venue_commissioning_runs"),
    ]
    assert fake_op.dropped_tables == ["venue_commissioning_runs"]


def test_model_metadata_compiles_postgresql_partial_index_and_constraints() -> None:
    from app.models.venue_commissioning_run import VenueCommissioningRun  # noqa: F401
    from app.db.base import Base

    table = Base.metadata.tables["venue_commissioning_runs"]
    str(CreateTable(table).compile(dialect=postgresql.dialect()))

    explicit_identifiers = _named_constraints(table)
    partial_index_found = False
    for index in table.indexes:
        compiled = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        if index.name:
            explicit_identifiers.append(index.name)
        if index.name == "uq_vcr_active_scope":
            partial_index_found = True
            assert "WHERE status IN" in compiled
            assert "BUY_SUBMISSION_PENDING" in compiled
            assert "SELL_RECONCILIATION_REQUIRED" in compiled

    assert partial_index_found
    _assert_identifier_lengths(explicit_identifiers)
