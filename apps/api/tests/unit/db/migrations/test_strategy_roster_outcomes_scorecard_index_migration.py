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
    / "20260721_0044_add_strategy_roster_outcomes_scorecard_index.py"
)

_SCORECARD_LOOKUP_COLUMNS = [
    "provider", "product_id", "interval", "evaluation_state", "strategy_slug", "evaluated_at", "outcome_id",
]


class _FakeOp:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        sa.Table(
            "strategy_roster_proposal_outcomes",
            self.metadata,
            sa.Column("outcome_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("provider", sa.Text()),
            sa.Column("product_id", sa.Text()),
            sa.Column("interval", sa.Text()),
            sa.Column("evaluation_state", sa.Text()),
            sa.Column("strategy_slug", sa.Text()),
            sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        )
        self.created_indexes: list[tuple[str, str, list[str]]] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []

    def create_index(self, index_name: str, table_name: str, columns: list[str], unique: bool = False) -> sa.Index:
        table = self.metadata.tables[table_name]
        index = sa.Index(index_name, *(table.c[column_name] for column_name in columns), unique=unique)
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.created_indexes.append((index_name, table_name, list(columns)))
        return index

    def drop_index(self, index_name: str, table_name: str | None = None) -> None:
        self.dropped_indexes.append((index_name, table_name))


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260721_0044", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260721_0044"
    assert module.down_revision == "20260717_0043"


def test_upgrade_creates_scorecard_lookup_index_covering_filter_and_sort() -> None:
    """The index must cover exactly the WHERE-clause columns (provider,
    product_id, interval, evaluation_state) followed by the ORDER BY columns
    (strategy_slug, evaluated_at, outcome_id) of
    fetch_strategy_scorecards -- in that order, so Postgres can satisfy the
    query with a single ordered index scan instead of a full table scan
    followed by a separate sort."""
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    assert len(fake_op.created_indexes) == 1
    index_name, table_name, columns = fake_op.created_indexes[0]
    assert index_name == "ix_roster_outcomes_scorecard_lookup"
    assert table_name == "strategy_roster_proposal_outcomes"
    assert columns == _SCORECARD_LOOKUP_COLUMNS


def test_downgrade_drops_scorecard_lookup_index() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.downgrade()

    assert fake_op.dropped_indexes == [("ix_roster_outcomes_scorecard_lookup", "strategy_roster_proposal_outcomes")]
