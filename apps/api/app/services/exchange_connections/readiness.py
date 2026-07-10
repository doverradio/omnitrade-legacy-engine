from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from app.schemas.exchange_connections import (
    ExchangeReadinessCheckResponse,
    ExchangeReadinessReportResponse,
    ExchangeReadinessVerdict,
)

CheckStatus = Literal["pass", "warn", "fail"]


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

    api_reachable = by_code.get("api_reachable")
    auth_valid = by_code.get("authentication_valid")
    clock_synced = by_code.get("clock_synchronized")
    permissions = by_code.get("permissions_retrieved")
    accounts = by_code.get("accounts_retrieved")
    balances = by_code.get("balances_retrieved")

    if api_reachable and api_reachable.status == "fail":
        return "UNREACHABLE"
    if auth_valid and auth_valid.status == "fail":
        return "AUTHENTICATION_FAILED"
    if clock_synced and clock_synced.status == "fail":
        return "CLOCK_SKEW"

    if permissions and permissions.status == "fail":
        return "PERMISSION_INSUFFICIENT"

    critical_fail = any(item.status == "fail" for item in checks)
    if critical_fail:
        return "MISCONFIGURED"

    if accounts and balances and permissions and accounts.status == "pass" and balances.status == "pass" and permissions.status == "pass":
        return "READY_FOR_PREVIEW"

    if api_reachable and auth_valid and api_reachable.status == "pass" and auth_valid.status == "pass":
        return "READ_ONLY_READY"

    return "UNKNOWN"


def build_report(*, checks: list[ExchangeReadinessCheckResponse]) -> ExchangeReadinessReportResponse:
    checked_at = datetime.now(timezone.utc)
    return ExchangeReadinessReportResponse(
        verdict=compute_verdict(checks),
        checked_at=checked_at,
        checks=checks,
    )
