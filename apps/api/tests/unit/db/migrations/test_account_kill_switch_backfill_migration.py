from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260708_0015_backfill_account_kill_switch_rows.py"
)


def _load_migration_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace(execute=lambda statement: None))

    spec = importlib.util.spec_from_file_location("migration_20260708_0015", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_backfill_uses_not_exists_for_idempotency_and_preserves_existing_rows() -> None:
    module = _load_migration_module()
    captured_sql: list[str] = []

    def _capture_execute(statement) -> None:
        captured_sql.append(str(statement))

    module.op.execute = _capture_execute

    module.upgrade()

    assert len(captured_sql) == 1
    sql = captured_sql[0]
    assert "INSERT INTO risk_kill_switches" in sql
    assert "FROM paper_accounts pa" in sql
    assert "WHERE NOT EXISTS" in sql
    assert "rks.scope = 'account'" in sql
    assert "rks.paper_account_id = pa.id" in sql
    assert "'account_bootstrap_default'" in sql
    assert "'system_bootstrap'" in sql


def test_downgrade_deletes_only_bootstrap_account_rows() -> None:
    module = _load_migration_module()
    captured_sql: list[str] = []

    def _capture_execute(statement) -> None:
        captured_sql.append(str(statement))

    module.op.execute = _capture_execute

    module.downgrade()

    assert len(captured_sql) == 1
    sql = captured_sql[0]
    assert "DELETE FROM risk_kill_switches" in sql
    assert "scope = 'account'" in sql
    assert "engaged = false" in sql
    assert "rearm_required = false" in sql
    assert "changed_by = 'system_bootstrap'" in sql
    assert "reason = 'account_bootstrap_default'" in sql
