from pathlib import Path


PATH = Path(__file__).parents[4] / "app/db/migrations/versions/20260801_0060_activate_autonomous_position_exit_claim.py"


def test_phase5_migration_is_narrow_reversible_and_side_aware() -> None:
    source = PATH.read_text()
    assert 'down_revision: str | None = "20260801_0059"' in source
    assert "CONTINUING_EXIT" in source
    assert 'alter_column("canonical_proving_activations", "dry_run_live_crypto_order_id", nullable=True)' in source
    assert "ck_aec_reduce_only_custody_claim" in source
    assert "capital_deployment_amount = 0" in source
    assert "claimed_base_quantity <= maximum_authorized_base_quantity" in source
    assert "uq_aec_active_sell_custody_scope" in source
    assert "reserved_activation_id" in source and "reserved_claim_id" in source
    assert "def downgrade()" in source
