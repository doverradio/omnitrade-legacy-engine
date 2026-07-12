from __future__ import annotations

import argparse
import asyncio
import traceback
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.db.session import AsyncSessionLocal
from app.services.decisions.linkage_integrity import build_linkage_integrity_summary


@dataclass(frozen=True, slots=True)
class VerifierReport:
    status: str
    exit_code: int
    reason: str | None
    summary: dict[str, Any] | None
    next_action: str


async def _run_verification(*, limit: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        summary = await build_linkage_integrity_summary(db=db, limit=limit)

    return summary


def _is_database_unavailable(exc: Exception) -> bool:
    if isinstance(exc, (OperationalError, InterfaceError, DBAPIError, ConnectionError, TimeoutError, OSError)):
        return True

    name = exc.__class__.__name__.lower()
    if any(
        marker in name
        for marker in (
            "connectionrefused",
            "invalidpassword",
            "invalidauthorization",
            "cannotconnect",
            "timeout",
            "network",
            "dns",
            "ssl",
            "tls",
        )
    ):
        return True

    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "connection refused",
            "could not connect",
            "name or service not known",
            "temporary failure in name resolution",
            "password authentication failed",
            "authentication failed",
            "ssl",
            "tls",
            "timed out",
            "timeout",
            "network is unreachable",
            "connection reset",
        )
    )


def _short_reason(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _build_success_report(summary: dict[str, Any]) -> VerifierReport:
    counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
    future_violations = int(counts.get("future_violations", 0) or 0)
    historical_exemptions = int(counts.get("historical_exemptions", 0) or 0)

    if future_violations > 0:
        return VerifierReport(
            status="INTEGRITY_VIOLATIONS_FOUND",
            exit_code=1,
            reason=None,
            summary=summary,
            next_action="Review Decision Linkage Integrity report.",
        )

    _ = historical_exemptions
    return VerifierReport(
        status="SUCCESS",
        exit_code=0,
        reason=None,
        summary=summary,
        next_action="No action required.",
    )


def _print_report(report: VerifierReport) -> None:
    print("========================================")
    print("Decision Linkage Integrity Verifier")
    print("========================================")
    print()
    print("Status:")
    print(report.status)
    print()

    if report.reason:
        print("Reason:")
        print(report.reason)
        print()

    if report.status == "DATABASE_UNAVAILABLE":
        print("Integrity verification did not execute.")
        print("No repository conclusions were reached.")
        print()

    if report.summary is not None:
        counts = report.summary.get("counts", {}) if isinstance(report.summary, dict) else {}
        scope = report.summary.get("scope", {}) if isinstance(report.summary, dict) else {}

        print("Summary:")
        print(f"Decision Records scanned: {int(scope.get('total_rows_scanned', 0) or 0)}")
        print(f"Previews evaluated (terminal): {int(scope.get('terminal_rows_scanned', 0) or 0)}")
        print(f"Integrity violations: {int(counts.get('future_violations', 0) or 0)}")
        print(f"Current-generation violations: {int(counts.get('future_violations', 0) or 0)}")
        print(f"Historical compatibility exemptions: {int(counts.get('historical_exemptions', 0) or 0)}")
        print()

    print("Recommended next action:")
    print(report.next_action)
    print()
    print("Exit code:")
    print(report.exit_code)


def _run_cli(*, limit: int, debug: bool) -> int:
    try:
        summary = asyncio.run(_run_verification(limit=limit))
        report = _build_success_report(summary)
    except Exception as exc:
        if _is_database_unavailable(exc):
            report = VerifierReport(
                status="DATABASE_UNAVAILABLE",
                exit_code=2,
                reason=_short_reason(exc),
                summary=None,
                next_action="Check PostgreSQL service or DATABASE_URL.",
            )
        else:
            report = VerifierReport(
                status="INTERNAL_ERROR",
                exit_code=3,
                reason=_short_reason(exc),
                summary=None,
                next_action="Investigate verifier implementation and rerun with --debug.",
            )

        if debug:
            traceback.print_exc()

    _print_report(report)
    return report.exit_code


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Decision Linkage Integrity verification for persisted preview decisions."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of latest previews to scan (default: 500).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print full traceback when verification fails.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    exit_code = _run_cli(limit=max(1, args.limit), debug=bool(args.debug))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
