from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import sqlalchemy as sa


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260713_0033_backfill_strategy_catalog_for_phase1_roster.py"
)


class _FakeOp:
    def __init__(self) -> None:
        self.sql_calls: list[str] = []

    def execute(self, statement):
        self.sql_calls.append(str(statement))
        return None


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260713_0033", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260713_0033"
    assert module.down_revision == "20260713_0032"


def test_upgrade_executes_idempotent_slug_backfill_sql() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    assert len(fake_op.sql_calls) == 5
    assert all("INSERT INTO strategies" in call for call in fake_op.sql_calls)
    assert all("ON CONFLICT (slug) DO NOTHING" in call for call in fake_op.sql_calls)


def test_downgrade_is_non_destructive_noop() -> None:
    module = _load_module()
    module.downgrade()
