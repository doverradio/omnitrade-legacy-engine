from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock, call

import pytest

_MIGRATION = Path(__file__).resolve().parents[4] / "app/db/migrations/versions/20260729_0056_add_mandate_purpose.py"


def _load(*, operation: Mock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=operation))
    spec = importlib.util.spec_from_file_location("mandate_purpose_0056", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_backward_compatible_purpose_column_and_active_scope_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = Mock()
    migration = _load(operation=operation, monkeypatch=monkeypatch)

    migration.upgrade()

    add_column_call = operation.mock_calls[0]
    assert add_column_call[0] == "add_column"
    assert add_column_call.args[0] == "autonomous_capital_mandates"
    column = add_column_call.args[1]
    assert column.name == "purpose"
    assert column.nullable is False
    # Every pre-existing mandate row backfills to PRODUCTION -- ordinary
    # autonomous trading is never reclassified by this migration.
    assert str(column.server_default.arg) == "'PRODUCTION'"

    assert call.create_check_constraint(
        "ck_ac_mandates_purpose",
        "autonomous_capital_mandates",
        "purpose IN ('PRODUCTION','CONTROLLED_PROOF')",
    ) in operation.mock_calls

    active_scope_call = next(
        item for item in operation.mock_calls
        if item.args and item.args[0] == "uq_ac_mandates_active_scope_purpose"
    )
    assert active_scope_call.args[1:] == (
        "autonomous_capital_mandates",
        ["provider", "exchange_environment", "exchange_connection_id", "live_trading_profile_id", "autonomy_level", "purpose"],
    )
    assert active_scope_call.kwargs["unique"] is True
    assert str(active_scope_call.kwargs["postgresql_where"]) == "status = 'ACTIVE'"

    assert call.create_index("ix_ac_mandates_purpose", "autonomous_capital_mandates", ["purpose"]) in operation.mock_calls


def test_downgrade_reverses_every_upgrade_step(monkeypatch: pytest.MonkeyPatch) -> None:
    operation = Mock()
    migration = _load(operation=operation, monkeypatch=monkeypatch)

    migration.downgrade()

    assert operation.mock_calls == [
        call.drop_index("ix_ac_mandates_purpose", table_name="autonomous_capital_mandates"),
        call.drop_index("uq_ac_mandates_active_scope_purpose", table_name="autonomous_capital_mandates"),
        call.drop_constraint("ck_ac_mandates_purpose", "autonomous_capital_mandates", type_="check"),
        call.drop_column("autonomous_capital_mandates", "purpose"),
    ]
