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
    / "20260712_0030_add_mandate_lifecycle_evidence.py"
)

_IDENTIFIER_LIMIT = 63


class _FakeOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        sa.Table(
            "autonomous_capital_mandates",
            self.metadata,
            sa.Column("mandate_id", postgresql.UUID(as_uuid=True), primary_key=True),
        )
        sa.Table(
            "autonomous_capital_mandate_versions",
            self.metadata,
            sa.Column("mandate_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        )
        sa.Table(
            "decision_records",
            self.metadata,
            sa.Column("decision_id", postgresql.UUID(as_uuid=True), primary_key=True),
        )
        sa.Table(
            "autonomous_capital_mandate_authorizations",
            self.metadata,
            sa.Column("mandate_authorization_id", postgresql.UUID(as_uuid=True), primary_key=True),
        )

        self.added_columns: list[tuple[str, str]] = []
        self.created_tables: list[sa.Table] = []
        self.created_indexes: list[str] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.dropped_tables: list[str] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def add_column(self, table_name: str, column: sa.Column) -> None:
        self.added_columns.append((table_name, column.name))

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

    def drop_index(self, index_name: str, table_name: str | None = None) -> None:
        self.dropped_indexes.append((index_name, table_name))

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))


def _load_migration_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260712_0030", _MIGRATION_PATH)
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


def test_migration_imports_and_revision_chain_are_correct() -> None:
    module = _load_migration_module()

    assert module.revision == "20260712_0030"
    assert module.down_revision == "20260712_0029"


def test_upgrade_compiles_postgresql_ddl_and_identifiers_fit_limit() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    assert (
        "autonomous_capital_mandate_authorizations",
        "audit_correlation_id",
    ) in fake_op.added_columns
    assert {table.name for table in fake_op.created_tables} == {"autonomous_capital_mandate_evaluations"}

    explicit_identifiers: list[str] = []
    for table in fake_op.created_tables:
        explicit_identifiers.extend(_named_constraints(table))
        explicit_identifiers.extend(index.name for index in table.indexes if index.name)

    _assert_identifier_lengths(explicit_identifiers)


def test_model_metadata_compiles_with_postgresql_and_identifiers_fit_limit() -> None:
    from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation  # noqa: F401
    from app.db.base import Base

    table = Base.metadata.tables["autonomous_capital_mandate_evaluations"]
    str(CreateTable(table).compile(dialect=postgresql.dialect()))

    explicit_identifiers = _named_constraints(table)
    for index in table.indexes:
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        if index.name:
            explicit_identifiers.append(index.name)

    _assert_identifier_lengths(explicit_identifiers)


def test_downgrade_drops_indexes_table_and_column() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.downgrade()

    assert fake_op.dropped_indexes == [
        ("ix_ac_mandate_evaluations_created_at", "autonomous_capital_mandate_evaluations"),
        ("ix_ac_mandate_evaluations_decision", "autonomous_capital_mandate_evaluations"),
        ("ix_ac_mandate_evaluations_mandate", "autonomous_capital_mandate_evaluations"),
    ]
    assert fake_op.dropped_tables == ["autonomous_capital_mandate_evaluations"]
    assert fake_op.dropped_columns == [("autonomous_capital_mandate_authorizations", "audit_correlation_id")]
