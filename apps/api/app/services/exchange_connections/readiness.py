from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from app.schemas.exchange_connections import (
    ExchangeReadinessCheckResponse,
    ExchangeReadinessReportResponse,
    ExchangeReadinessVerdict,
)

CheckStatus = Literal["pass", "warn", "fail"]


# Centralized readiness contract for preview-only autonomous cycles.
# Keep legacy verdicts for compatibility while explicitly accepting
# READY_FOR_OPERATOR_REVIEW emitted by compute_verdict.
AUTONOMOUS_PREVIEW_READY_VERDICTS = frozenset(
    {
        "READY_FOR_PREVIEW",
        "READY_FOR_OPERATOR_REVIEW",
        "READY_FOR_ORDER_SUBMISSION",
        "NOT_READY_SUBMISSION_DISABLED",
    }
)


def supports_autonomous_preview(verdict: str | None) -> bool:
    if verdict is None:
        return False
    return verdict in AUTONOMOUS_PREVIEW_READY_VERDICTS


def readiness_check(*, code: str, label: str, status: CheckStatus, explanation: str, remediation: str) -> ExchangeReadinessCheckResponse:
    return ExchangeReadinessCheckResponse(
        code=code,
        label=label,
        status=status,
        explanation=explanation,
        checked_at=datetime.now(timezone.utc),
        remediation=remediation,
    )


def compute_verdict(checks: list[ExchangeReadinessCheckResponse]) -> ExchangeReadinessVerdict:
    by_code = {item.code: item for item in checks}

    credentials_stored = by_code.get("credentials_stored")
    api_reachable = by_code.get("api_reachable")
    auth_valid = by_code.get("authentication_valid")
    dangerous_permissions = by_code.get("dangerous_permissions_detected")
    trade_permission = by_code.get("trade_permission_present")
    permissions_retrieved = by_code.get("permissions_retrieved")
    account_restricted = by_code.get("account_restricted")
    product_available = by_code.get("product_btc_usd_available")
    usd_balance = by_code.get("usd_balance_retrieved")
    usd_balance_funded = by_code.get("usd_balance_funded")
    btc_balance = by_code.get("btc_balance_retrieved")

    if credentials_stored and credentials_stored.status == "fail":
        return "NOT_CONFIGURED"

    if auth_valid and auth_valid.status == "fail":
        return "AUTHENTICATION_FAILED"
    if api_reachable and api_reachable.status == "fail":
        return "AUTHENTICATION_FAILED"

    if dangerous_permissions and dangerous_permissions.status == "fail":
        return "PERMISSION_BLOCKED"
    if permissions_retrieved and permissions_retrieved.status == "fail":
        return "PERMISSION_BLOCKED"
    if trade_permission and trade_permission.status == "fail":
        return "PERMISSION_BLOCKED"
    if account_restricted and account_restricted.status == "fail":
        return "ACCOUNT_RESTRICTED"
    if product_available and product_available.status == "fail":
        return "PRODUCT_UNAVAILABLE"

    # USD readability is the hard prerequisite for quote-sized BTC-USD BUY initialization checks.
    # BTC readability remains evidence but does not block readiness by itself.
    usd_balance_available = usd_balance is None or usd_balance.status == "pass"
    if not usd_balance_available:
        return "BALANCE_UNAVAILABLE"

    if usd_balance_funded is not None and usd_balance_funded.status == "fail":
        return "INITIALIZED_BUT_UNFUNDED"

    if product_available and product_available.status == "pass" and (trade_permission is None or trade_permission.status != "fail"):
        return "READY_FOR_OPERATOR_REVIEW"

    if auth_valid and auth_valid.status == "pass":
        return "READY_FOR_PREVIEW"

    return "UNKNOWN"


def build_report(*, checks: list[ExchangeReadinessCheckResponse]) -> ExchangeReadinessReportResponse:
    checked_at = datetime.now(timezone.utc)
    return ExchangeReadinessReportResponse(
        verdict=compute_verdict(checks),
        checked_at=checked_at,
        checks=checks,
    )
