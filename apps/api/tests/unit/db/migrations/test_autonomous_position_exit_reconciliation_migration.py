from pathlib import Path

PATH = Path(__file__).parents[4] / "app/db/migrations/versions/20260801_0063_reconcile_autonomous_exit.py"


def test_exit_reconciliation_migration_is_reversible_and_fail_closed():
    source = PATH.read_text()
    assert 'down_revision: str | None = "20260801_0062"' in source
    assert "exit_reconciliation_event_id" in source and "uq_apc_exit_reconciliation" in source
    assert "ck_apc_realized_exit_economics" in source and "realized_net_sell_proceeds" in source
    assert "ck_apc_proof_sell_verified" in source and "realized_net_profit > 0" in source
    assert "residual_dust_quantity = 0" in source and "def downgrade()" in source
