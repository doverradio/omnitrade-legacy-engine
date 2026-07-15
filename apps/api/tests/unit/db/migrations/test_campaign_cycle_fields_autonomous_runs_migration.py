from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260715_0037_add_campaign_cycle_fields_to_autonomous_runs.py"
)

_IDENTIFIER_LIMIT = 63


class _FakeOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        sa.Table(
            "autonomous_cycle_runs",
            self.metadata,
            sa.Column("cycle_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        )
        self.added_columns: list[tuple[str, str]] = []
        self.created_indexes: list[str] = []
        self.created_checks: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def add_column(self, table_name: str, column: sa.Column) -> None:
        table = self.metadata.tables[table_name]
        table.append_column(column)
        self.added_columns.append((table_name, column.name))

    def create_index(self, index_name: str, table_name: str, columns: list[str], unique: bool = False) -> sa.Index:
        table = self.metadata.tables[table_name]
        index = sa.Index(index_name, *(table.c[column_name] for column_name in columns), unique=unique)
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.created_indexes.append(index_name)
        return index

    def create_check_constraint(self, name: str, table_name: str, condition: str) -> None:
        self.created_checks.append((name, table_name, condition))

    def drop_index(self, index_name: str, table_name: str | None = None) -> None:
        self.dropped_indexes.append((index_name, table_name))

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, table_name, type_))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260715_0037", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260715_0037"
    assert module.down_revision == "20260714_0036"


def test_upgrade_and_downgrade_compile() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    assert fake_op.added_columns == [
        ("autonomous_cycle_runs", "cycle_kind"),
        ("autonomous_cycle_runs", "capital_campaign_id"),
        ("autonomous_cycle_runs", "capital_campaign_version"),
    ]
    assert fake_op.created_indexes == [
        "ix_autonomous_cycle_runs_campaign_created",
        "ix_autonomous_cycle_runs_kind_created",
    ]
    assert fake_op.created_checks == [
        ("ck_autonomous_cycle_runs_cycle_kind", "autonomous_cycle_runs", "cycle_kind IN ('autonomous','campaign')"),
    ]

    module.downgrade()
    assert fake_op.dropped_indexes == [
        ("ix_autonomous_cycle_runs_kind_created", "autonomous_cycle_runs"),
        ("ix_autonomous_cycle_runs_campaign_created", "autonomous_cycle_runs"),
    ]
    assert fake_op.dropped_constraints == [
        ("ck_autonomous_cycle_runs_cycle_kind", "autonomous_cycle_runs", "check"),
    ]
    assert fake_op.dropped_columns == [
        ("autonomous_cycle_runs", "capital_campaign_version"),
        ("autonomous_cycle_runs", "capital_campaign_id"),
        ("autonomous_cycle_runs", "cycle_kind"),
    ]


def test_identifier_lengths_fit_postgresql() -> None:
    module = _load_module()
    identifiers = [
        "ix_autonomous_cycle_runs_campaign_created",
        "ix_autonomous_cycle_runs_kind_created",
    ]
    too_long = [(name, len(name)) for name in identifiers if len(name) > _IDENTIFIER_LIMIT]
    assert not too_long, f"Identifiers exceed {_IDENTIFIER_LIMIT} chars: {too_long}"