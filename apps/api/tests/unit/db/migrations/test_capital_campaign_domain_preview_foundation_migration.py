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
    / "20260714_0036_add_capital_campaign_domain_preview_foundation.py"
)

_IDENTIFIER_LIMIT = 63


class _FakeOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        sa.Table(
            "capital_campaigns",
            self.metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        )
        self.created_tables: list[sa.Table] = []
        self.created_indexes: list[str] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.dropped_tables: list[str] = []
        self.created_checks: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.created_foreign_keys: list[str] = []
        self.added_columns: list[tuple[str, str]] = []
        self.dropped_columns: list[tuple[str, str]] = []

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

    def add_column(self, table_name: str, column: sa.Column) -> None:
        table = self.metadata.tables[table_name]
        table.append_column(column)
        self.added_columns.append((table_name, column.name))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))

    def create_check_constraint(self, name: str, table_name: str, condition: str) -> None:
        self.created_checks.append((name, table_name, condition))

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, table_name, type_))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        ondelete: str | None = None,
    ) -> None:
        _ = (source_table, referent_table, local_cols, remote_cols, ondelete)
        self.created_foreign_keys.append(name)


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260714_0036", _MIGRATION_PATH)
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


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260714_0036"
    assert module.down_revision == "20260714_0035"


def test_upgrade_and_downgrade_compile() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    assert {table.name for table in fake_op.created_tables} == {"capital_campaign_definitions"}
    assert fake_op.created_indexes == ["ix_ccd_campaign_id", "ix_ccd_status_created", "ix_capital_campaigns_definition_pin"]
    assert fake_op.added_columns == [
        ("capital_campaigns", "definition_campaign_id"),
        ("capital_campaigns", "definition_version"),
    ]
    assert fake_op.created_foreign_keys == ["fk_capital_campaigns_definition_pin"]
    assert [item[0] for item in fake_op.created_checks] == [
        "ck_capital_campaigns_definition_pin_pair",
        "ck_capital_campaigns_definition_pin_identity",
    ]

    module.downgrade()
    assert fake_op.dropped_indexes == [
        ("ix_capital_campaigns_definition_pin", "capital_campaigns"),
        ("ix_ccd_status_created", "capital_campaign_definitions"),
        ("ix_ccd_campaign_id", "capital_campaign_definitions"),
    ]
    assert fake_op.dropped_constraints == [
        ("fk_capital_campaigns_definition_pin", "capital_campaigns", "foreignkey"),
        ("ck_capital_campaigns_definition_pin_identity", "capital_campaigns", "check"),
        ("ck_capital_campaigns_definition_pin_pair", "capital_campaigns", "check"),
    ]
    assert fake_op.dropped_columns == [
        ("capital_campaigns", "definition_version"),
        ("capital_campaigns", "definition_campaign_id"),
    ]
    assert fake_op.dropped_tables == ["capital_campaign_definitions"]


def test_model_metadata_compiles_with_postgresql_and_identifier_limits() -> None:
    from app.models.capital_campaign_definition import CapitalCampaignDefinition  # noqa: F401
    from app.db.base import Base

    table = Base.metadata.tables["capital_campaign_definitions"]
    str(CreateTable(table).compile(dialect=postgresql.dialect()))

    explicit_identifiers = _named_constraints(table)
    for index in table.indexes:
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        if index.name:
            explicit_identifiers.append(index.name)

    _assert_identifier_lengths(explicit_identifiers)
