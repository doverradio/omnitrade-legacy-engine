from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock, call

import pytest

_MIGRATION = Path(__file__).resolve().parents[4] / "app/db/migrations/versions/20260728_0055_add_controlled_proof_exit_recovery.py"


def _load(*, operation: Mock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=operation))
    spec = importlib.util.spec_from_file_location("controlled_proof_exit_recovery_0055", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_upgrade_declares_proof_scoped_active_index_and_downgrade_reverses(monkeypatch: pytest.MonkeyPatch) -> None:
    operation = Mock()
    migration = _load(operation=operation, monkeypatch=monkeypatch)
    migration.upgrade()
    create_table = operation.mock_calls[0]
    assert create_table.args[0] == "controlled_proof_exit_recoveries"
    assert any(getattr(item, "name", None) == "ck_controlled_proof_exit_recoveries_status" for item in create_table.args[1:])
    active_index_call = next(item for item in operation.mock_calls if item.args and item.args[0] == "uq_controlled_proof_exit_recoveries_active_proof")
    assert active_index_call.args[1:] == ("controlled_proof_exit_recoveries", ["proof_id"])
    assert active_index_call.kwargs["unique"] is True
    assert str(active_index_call.kwargs["postgresql_where"]) == "status IN ('AUTHORIZED','IN_PROGRESS')"
    assert call.create_index(
        "ix_controlled_proof_exit_recoveries_proof", "controlled_proof_exit_recoveries", ["proof_id"],
    ) in operation.mock_calls

    operation.reset_mock(); migration.downgrade()
    assert operation.mock_calls == [
        call.drop_index("ix_controlled_proof_exit_recoveries_proof", table_name="controlled_proof_exit_recoveries"),
        call.drop_index("uq_controlled_proof_exit_recoveries_active_proof", table_name="controlled_proof_exit_recoveries"),
        call.drop_table("controlled_proof_exit_recoveries"),
    ]
