from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

_MIGRATION = Path(__file__).resolve().parents[4] / "app/db/migrations/versions/20260801_0059_bind_exit_authority_package.py"


def _load(*, operation: Mock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=operation))
    spec = importlib.util.spec_from_file_location("autonomous_position_exit_0059", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_upgrade_adds_exact_decision_package_reservation_binding(monkeypatch):
    operation = Mock(); migration = _load(operation=operation, monkeypatch=monkeypatch)
    migration.upgrade()
    added = [call.args[1].name for call in operation.mock_calls if len(call.args) > 1 and call.args[0] == "autonomous_position_exit_authorities" and hasattr(call.args[1], "name")]
    assert added[:2] == ["reserved_decision_id", "reserved_package_id"]
    calls = repr(operation.mock_calls)
    assert "fk_apea_reserved_decision" in calls and "decision_records" in calls
    assert "fk_apea_reserved_package" in calls and "canonical_preview_packages" in calls
    assert "uq_apea_reserved_decision" in calls and "uq_apea_reserved_package" in calls
    assert "ck_apea_reservation_binding" in calls
    assert "capital_deployment_amount" in calls and "proposed_base_quantity" in calls
    assert "ck_cpp_side_aware_capital" in calls and "ck_cpp_side_aware_quantity" in calls
    assert "maximum_authorized_base_quantity" in calls and "ck_cpa_deployed_positive" in calls


def test_downgrade_removes_only_phase4_binding(monkeypatch):
    operation = Mock(); migration = _load(operation=operation, monkeypatch=monkeypatch)
    migration.downgrade(); calls = repr(operation.mock_calls)
    assert "drop_table" not in calls
    assert "reserved_package_id" in calls and "reserved_decision_id" in calls
    assert "ck_apea_reservation_binding" in calls
