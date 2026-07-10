from __future__ import annotations

from app.services.exchange_connections.readiness import compute_verdict, readiness_check


def _check(code: str, status: str):
    return readiness_check(
        code=code,
        label=code,
        status=status,
        explanation=code,
        remediation=code,
    )


def test_withdrawal_permission_blocks_readiness() -> None:
    checks = [
        _check("credentials_stored", "pass"),
        _check("authentication_valid", "pass"),
        _check("permissions_retrieved", "pass"),
        _check("dangerous_permissions_detected", "fail"),
        _check("trade_permission_present", "pass"),
        _check("usd_balance_retrieved", "pass"),
        _check("btc_balance_retrieved", "pass"),
        _check("product_btc_usd_available", "pass"),
    ]
    assert compute_verdict(checks) == "PERMISSION_BLOCKED"


def test_unknown_permission_state_fails_closed() -> None:
    checks = [
        _check("credentials_stored", "pass"),
        _check("authentication_valid", "pass"),
        _check("permissions_retrieved", "fail"),
        _check("dangerous_permissions_detected", "pass"),
        _check("trade_permission_present", "pass"),
        _check("usd_balance_retrieved", "pass"),
        _check("btc_balance_retrieved", "pass"),
        _check("product_btc_usd_available", "pass"),
    ]
    assert compute_verdict(checks) == "PERMISSION_BLOCKED"


def test_missing_trade_permission_blocks_preview_and_dry_run() -> None:
    checks = [
        _check("credentials_stored", "pass"),
        _check("authentication_valid", "pass"),
        _check("permissions_retrieved", "pass"),
        _check("dangerous_permissions_detected", "pass"),
        _check("trade_permission_present", "fail"),
        _check("usd_balance_retrieved", "pass"),
        _check("btc_balance_retrieved", "pass"),
        _check("product_btc_usd_available", "pass"),
    ]
    assert compute_verdict(checks) == "PERMISSION_BLOCKED"


def test_minimum_permission_and_product_evidence_ready_for_operator_review() -> None:
    checks = [
        _check("credentials_stored", "pass"),
        _check("api_reachable", "pass"),
        _check("authentication_valid", "pass"),
        _check("permissions_retrieved", "pass"),
        _check("dangerous_permissions_detected", "pass"),
        _check("trade_permission_present", "pass"),
        _check("usd_balance_retrieved", "pass"),
        _check("btc_balance_retrieved", "pass"),
        _check("product_btc_usd_available", "pass"),
    ]
    assert compute_verdict(checks) == "READY_FOR_OPERATOR_REVIEW"
