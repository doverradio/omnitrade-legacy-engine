from pathlib import Path

PATH = Path(__file__).parents[4] / "app/db/migrations/versions/20260801_0062_submit_autonomous_exit_order.py"


def test_submission_lifecycle_migration_is_reversible_and_fail_closed():
    source = PATH.read_text()
    assert 'down_revision: str | None = "20260801_0061"' in source
    assert "ck_lco_reduce_only_lifecycle" in source and "ck_lco_reduce_only_constructed" in source
    assert "normalized_base_quantity <= requested_base_quantity" in source
    assert "capital_deployment_amount = 0" in source
    assert "proof_eligible = true" in source and "disqualification_reason IS NOT NULL" in source
    assert "SUBMISSION_PENDING" in source and "RECONCILIATION_REQUIRED" in source
    assert "ACKNOWLEDGED" in source and "provider_order_id IS NOT NULL" in source
    assert "provider_submission_connected = true" in source
    assert "def downgrade()" in source
