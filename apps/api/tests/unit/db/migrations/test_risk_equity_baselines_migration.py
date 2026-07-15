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
    / "20260715_0039_add_risk_equity_baselines.py"
)

_IDENTIFIER_LIMIT = 63


class _FakeOp:
    def __init__(self) -> None:
        self.created_tables: list[str] = []
        self.dropped_tables: list[str] = []
        self.create_args: list[tuple] = []

    def create_table(self, table_name: str, *args, **kwargs) -> None:
        _ = kwargs
        self.created_tables.append(table_name)
        self.create_args = list(args)

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260715_0039", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260715_0039"
    assert module.down_revision == "20260715_0038"


def test_upgrade_and_downgrade_compile() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    assert fake_op.created_tables == ["risk_equity_baselines"]

    module.downgrade()
    assert fake_op.dropped_tables == ["risk_equity_baselines"]


def test_identifier_lengths_fit_postgresql() -> None:
    identifiers = [
        "uq_risk_equity_baselines_account",
        "ck_risk_eq_base_sod_non_negative",
        "ck_risk_eq_base_hwm_non_negative",
        "ck_risk_eq_base_last_equity_non_negative",
        "ck_risk_eq_base_cash_non_negative",
        "ck_risk_eq_base_pos_non_negative",
        "ck_risk_eq_base_valuation_state",
    ]
    too_long = [(name, len(name)) for name in identifiers if len(name) > _IDENTIFIER_LIMIT]
    assert not too_long, f"Identifiers exceed {_IDENTIFIER_LIMIT} chars: {too_long}"
