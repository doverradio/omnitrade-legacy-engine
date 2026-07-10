from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from typing import Any


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260709_0022_live_crypto_order_dry_run_status.py"
)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeBind:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.execute_calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        self.execute_calls.append((str(statement), params))
        return _FakeResult(self._rows)


class _FakeOp:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.bind = _FakeBind(rows)
        self.drop_calls: list[tuple[str, str, str]] = []
        self.create_calls: list[tuple[str, str, str]] = []

    def get_bind(self) -> _FakeBind:
        return self.bind

    def drop_constraint(self, name: str, table_name: str, *, type_: str) -> None:
        self.drop_calls.append((name, table_name, type_))

    def create_check_constraint(self, name: str, table_name: str, sqltext: str) -> None:
        self.create_calls.append((name, table_name, sqltext))


def _load_migration_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260709_0022", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_drops_expected_named_status_constraint_and_recreates_once() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(
        rows=[
            {
                "constraint_name": "ck_live_crypto_orders_status",
                "constraint_def": "CHECK ((status = ANY (ARRAY['PENDING_CONFIRMATION'::text])))",
                "column_names": ["status"],
            }
        ]
    )
    module.op = fake_op

    module.upgrade()

    assert fake_op.drop_calls == [("ck_live_crypto_orders_status", "live_crypto_orders", "check")]
    assert len(fake_op.create_calls) == 1
    name, table_name, sqltext = fake_op.create_calls[0]
    assert name == "ck_live_crypto_orders_status"
    assert table_name == "live_crypto_orders"
    assert "DRY_RUN_READY" in sqltext
    assert "DRY_RUN_BLOCKED" in sqltext


def test_upgrade_drops_alternate_named_status_constraint_only() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(
        rows=[
            {
                "constraint_name": "live_crypto_orders_status_check",
                "constraint_def": "CHECK ((status IN ('PENDING_CONFIRMATION','SUBMITTED')))",
                "column_names": [],
            },
            {
                "constraint_name": "ck_live_crypto_orders_provider",
                "constraint_def": "CHECK ((provider = 'coinbase_advanced'::text))",
                "column_names": ["provider"],
            },
        ]
    )
    module.op = fake_op

    module.upgrade()

    assert fake_op.drop_calls == [("live_crypto_orders_status_check", "live_crypto_orders", "check")]
    assert len(fake_op.create_calls) == 1


def test_upgrade_handles_no_existing_status_constraint() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(
        rows=[
            {
                "constraint_name": "ck_live_crypto_orders_provider",
                "constraint_def": "CHECK ((provider = 'coinbase_advanced'::text))",
                "column_names": ["provider"],
            }
        ]
    )
    module.op = fake_op

    module.upgrade()

    assert fake_op.drop_calls == []
    assert len(fake_op.create_calls) == 1


def test_constraint_inspection_is_read_only_and_deterministic() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(rows=[])
    module.op = fake_op

    first = module._list_table_check_constraints()
    second = module._list_table_check_constraints()

    assert first == second == []
    assert len(fake_op.bind.execute_calls) == 2
    assert fake_op.drop_calls == []
    assert fake_op.create_calls == []


def test_downgrade_drops_status_constraint_and_restores_pre_dry_run_values() -> None:
    module = _load_migration_module()
    fake_op = _FakeOp(
        rows=[
            {
                "constraint_name": "live_crypto_orders_status_check",
                "constraint_def": "CHECK ((status IN ('DRY_RUN_READY','DRY_RUN_BLOCKED','PENDING_CONFIRMATION')))",
                "column_names": ["status"],
            }
        ]
    )
    module.op = fake_op

    module.downgrade()

    assert fake_op.drop_calls == [("live_crypto_orders_status_check", "live_crypto_orders", "check")]
    assert len(fake_op.create_calls) == 1
    name, table_name, sqltext = fake_op.create_calls[0]
    assert name == "ck_live_crypto_orders_status"
    assert table_name == "live_crypto_orders"
    assert "DRY_RUN_READY" not in sqltext
    assert "DRY_RUN_BLOCKED" not in sqltext
    assert "PENDING_CONFIRMATION" in sqltext
