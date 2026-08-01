from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock, call

import pytest

_MIGRATION = Path(__file__).resolve().parents[4] / "app/db/migrations/versions/20260731_0057_add_autonomous_position_custody.py"


def _load(*, operation: Mock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=operation))
    spec = importlib.util.spec_from_file_location("autonomous_position_custody_0057", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_enforces_custody_lineage_and_nonterminal_uniqueness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = Mock()
    migration = _load(operation=operation, monkeypatch=monkeypatch)

    migration.upgrade()

    create_table = operation.mock_calls[0]
    assert create_table.args[0] == "autonomous_position_custodies"
    schema_items = create_table.args[1:]
    names = {item.name for item in schema_items if getattr(item, "name", None)}
    assert {
        "custody_id", "originating_autonomous_cycle_id", "originating_campaign_cycle_id",
        "mandate_id", "decision_record_id", "buy_package_id", "buy_activation_id",
        "buy_claim_id", "buy_live_order_id", "buy_reconciliation_event_id",
        "original_acquired_quantity", "observed_remaining_quantity", "proof_eligible",
        "ck_apc_proof_disqualification", "uq_apc_buy_claim", "uq_apc_buy_package",
        "uq_apc_buy_order",
    } <= names

    active_scope = next(
        item for item in operation.mock_calls
        if item.args and item.args[0] == "uq_apc_nonterminal_position_scope"
    )
    assert active_scope.kwargs["unique"] is True
    assert active_scope.args[2] == ["live_trading_profile_id", "product"]
    assert "HANDOFF_PENDING" in str(active_scope.kwargs["postgresql_where"])
    assert "BLOCKED" in str(active_scope.kwargs["postgresql_where"])


def test_downgrade_removes_only_the_custody_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    operation = Mock()
    migration = _load(operation=operation, monkeypatch=monkeypatch)

    migration.downgrade()

    assert operation.mock_calls == [
        call.drop_index("uq_apc_nonterminal_position_scope", table_name="autonomous_position_custodies"),
        call.drop_index("ix_apc_scope", table_name="autonomous_position_custodies"),
        call.drop_index("ix_apc_state_next_evaluation", table_name="autonomous_position_custodies"),
        call.drop_table("autonomous_position_custodies"),
    ]
