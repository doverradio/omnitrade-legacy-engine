from pathlib import Path

PATH = Path(__file__).parents[4] / "app/db/migrations/versions/20260801_0061_construct_autonomous_exit_order.py"


def test_phase5_order_migration_is_reversible_and_provider_disconnected():
    source = PATH.read_text()
    assert 'down_revision: str | None = "20260801_0060"' in source
    assert "ck_lco_reduce_only_constructed" in source
    assert "normalized_base_quantity <= requested_base_quantity" in source
    assert "provider_submission_connected = false" in source
    assert "provider_order_id IS NULL" in source and "submitted_at IS NULL" in source
    assert "uq_lco_execution_claim" in source and "uq_lco_active_sell_custody_scope" in source
    assert "reserved_order_id" in source and "def downgrade()" in source
