from __future__ import annotations

import argparse
import asyncio
import inspect
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_profit_cycle import CapitalCampaignProfitCycle
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.risk_event import RiskEvent
from app.services import mission_control_intelligence as mission_control_service


_MISSION_CONTROL_DRY_RUN_EVENTS = {"DRY_RUN_READY", "DRY_RUN_BLOCKED"}


@dataclass(slots=True)
class ReviewCheck:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class ReviewReport:
    checks: list[ReviewCheck]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _load_live_order(
    *,
    db,
    live_crypto_order_id: UUID | None,
    audit_correlation_id: UUID | None,
) -> LiveCryptoOrder:
    statement = select(LiveCryptoOrder)
    if live_crypto_order_id is not None:
        statement = statement.where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id)
    else:
        statement = statement.where(LiveCryptoOrder.audit_correlation_id == audit_correlation_id)
    live_order = await db.scalar(statement.limit(1))
    if live_order is None:
        raise LookupError("dry-run live crypto order not found")
    return live_order


async def _load_preview_and_profile(
    *,
    db,
    live_order: LiveCryptoOrder,
    approval_event_id: UUID | None,
) -> tuple[CryptoOrderPreview, LiveTradingProfile]:
    preview = await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.crypto_order_preview_id == live_order.crypto_order_preview_id)
        .limit(1)
    )
    if preview is None:
        raise LookupError("crypto order preview not found")

    if approval_event_id is None:
        raise LookupError("approval event not found")

    approval_event = await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.id == approval_event_id)
        .limit(1)
    )
    if approval_event is None:
        raise LookupError("approval event not found")

    profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.id == approval_event.live_trading_profile_id)
        .limit(1)
    )
    if profile is None:
        raise LookupError("live trading profile not found")

    return preview, profile


async def _count_rows(db, statement) -> int:
    count = await db.scalar(statement)
    return int(count or 0)


async def verify_dry_run_evidence(
    *,
    db,
    live_crypto_order_id: UUID | None,
    audit_correlation_id: UUID | None,
    mission_control_range: str,
) -> ReviewReport:
    checks: list[ReviewCheck] = []
    settings = get_settings()
    live_order = await _load_live_order(
        db=db,
        live_crypto_order_id=live_crypto_order_id,
        audit_correlation_id=audit_correlation_id,
    )
    safe_provider_response = live_order.safe_provider_response or {}
    approval_event_id_raw = safe_provider_response.get("approval_event_id")
    approval_event_id = UUID(str(approval_event_id_raw)) if approval_event_id_raw is not None else None
    preview, profile = await _load_preview_and_profile(
        db=db,
        live_order=live_order,
        approval_event_id=approval_event_id,
    )

    mode = str(safe_provider_response.get("mode", ""))
    checks.append(ReviewCheck("mode", mode == "dry_run", f"mode={mode or 'missing'}"))

    submission_skipped = bool(safe_provider_response.get("submission_skipped", False))
    checks.append(ReviewCheck("submission_skipped", submission_skipped, f"submission_skipped={submission_skipped}"))

    submission_skip_reason = str(safe_provider_response.get("submission_skip_reason", ""))
    skip_reason_ok = "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false" in submission_skip_reason and "LIVE_CRYPTO_DRY_RUN_ENABLED=true" in submission_skip_reason
    checks.append(ReviewCheck("submission_skip_reason", skip_reason_ok, f"submission_skip_reason={submission_skip_reason or 'missing'}"))

    provider_order_absent = live_order.provider_order_id is None
    submitted_absent = live_order.submitted_at is None
    acknowledged_absent = live_order.acknowledged_at is None
    filled_absent = live_order.filled_at is None
    checks.append(ReviewCheck("provider_order_id_absent", provider_order_absent, f"provider_order_id={live_order.provider_order_id}"))
    checks.append(ReviewCheck("submitted_at_absent", submitted_absent, f"submitted_at={live_order.submitted_at}"))
    checks.append(ReviewCheck("acknowledged_at_absent", acknowledged_absent, f"acknowledged_at={live_order.acknowledged_at}"))
    checks.append(ReviewCheck("filled_at_absent", filled_absent, f"filled_at={live_order.filled_at}"))

    requested_quote_size = Decimal(str(live_order.requested_quote_size))
    checks.append(ReviewCheck("amount_cap", requested_quote_size <= Decimal("5"), f"requested_quote_size={format(requested_quote_size, 'f')}"))

    risk_event_id_raw = safe_provider_response.get("risk_event_id")
    approved_intent_fingerprint = safe_provider_response.get("approved_intent_fingerprint")
    evidence_fingerprint = safe_provider_response.get("evidence_fingerprint")
    checks.append(ReviewCheck("approval_event_id_present", approval_event_id_raw is not None, f"approval_event_id={approval_event_id_raw}"))
    checks.append(ReviewCheck("risk_event_id_present", risk_event_id_raw is not None, f"risk_event_id={risk_event_id_raw}"))
    checks.append(ReviewCheck("intent_fingerprint_present", bool(approved_intent_fingerprint), f"approved_intent_fingerprint={approved_intent_fingerprint}"))
    checks.append(ReviewCheck("evidence_fingerprint_present", bool(evidence_fingerprint), f"evidence_fingerprint={evidence_fingerprint}"))

    approval_event = None
    if approval_event_id is not None:
        approval_event = await db.scalar(
            select(LiveApprovalEvent)
            .where(LiveApprovalEvent.id == approval_event_id)
            .limit(1)
        )
    checks.append(ReviewCheck("approval_event_linked", approval_event is not None, f"approval_event_found={approval_event is not None}"))

    risk_event = None
    if risk_event_id_raw is not None:
        risk_event = await db.scalar(
            select(RiskEvent)
            .where(RiskEvent.id == UUID(str(risk_event_id_raw)))
            .limit(1)
        )
    checks.append(ReviewCheck("risk_event_linked", risk_event is not None, f"risk_event_found={risk_event is not None}"))

    freshness_checks = {
        "preview_age_seconds": settings.live_crypto_preview_max_age_seconds,
        "readiness_age_seconds": settings.live_crypto_readiness_max_age_seconds,
        "heartbeat_age_seconds": settings.live_crypto_readiness_max_age_seconds,
        "balance_age_seconds": settings.live_crypto_balance_max_age_seconds,
        "price_age_seconds": settings.live_crypto_price_max_age_seconds,
    }
    for field_name, limit in freshness_checks.items():
        observed = safe_provider_response.get(field_name)
        passed = observed is not None and int(observed) >= 0 and int(observed) <= int(limit)
        checks.append(ReviewCheck(field_name, passed, f"{field_name}={observed}"))

    live_accounting_count = await _count_rows(
        db,
        select(func.count()).select_from(LiveAccountingRecord).where(LiveAccountingRecord.live_crypto_order_id == live_order.live_crypto_order_id),
    )
    checks.append(ReviewCheck("live_accounting_absent", live_accounting_count == 0, f"live_accounting_records={live_accounting_count}"))

    reconciliation_count = await _count_rows(
        db,
        select(func.count()).select_from(LiveReconciliationEvent).where(LiveReconciliationEvent.live_crypto_order_id == live_order.live_crypto_order_id),
    )
    checks.append(ReviewCheck("reconciliation_absent", reconciliation_count == 0, f"live_reconciliation_events={reconciliation_count}"))

    audit_window_start = live_order.created_at
    capital_audit_count = await _count_rows(
        db,
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity_type.in_(["capital_campaign", "capital_campaign_profit_cycle"]))
        .where(AuditLog.created_at >= audit_window_start),
    )
    checks.append(ReviewCheck("capital_mutation_absent", capital_audit_count == 0, f"capital_audit_rows={capital_audit_count}"))

    campaign = await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.paper_account_id == profile.paper_account_id)
        .order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
        .limit(1)
    )
    if campaign is not None:
        profit_cycle_count = await _count_rows(
            db,
            select(func.count())
            .select_from(CapitalCampaignProfitCycle)
            .where(CapitalCampaignProfitCycle.capital_campaign_id == campaign.id)
            .where(CapitalCampaignProfitCycle.created_at >= audit_window_start),
        )
        checks.append(ReviewCheck("profit_cycle_mutation_absent", profit_cycle_count == 0, f"profit_cycle_rows_after_dry_run={profit_cycle_count}"))

    mission_control = await _maybe_await(
        mission_control_service.build_mission_control_intelligence(db=db, range_value=mission_control_range)
    )
    matching_events = [
        item
        for item in getattr(mission_control, "timeline_events", [])
        if item.event_type in _MISSION_CONTROL_DRY_RUN_EVENTS
        and str(item.metadata.get("mode", "")) == "dry_run"
    ]
    checks.append(ReviewCheck("mission_control_annotation_present", bool(matching_events), f"matching_mission_control_events={len(matching_events)}"))

    return ReviewReport(checks=checks)


async def _run_review(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        try:
            report = await verify_dry_run_evidence(
                db=db,
                live_crypto_order_id=args.live_crypto_order_id,
                audit_correlation_id=args.audit_correlation_id,
                mission_control_range=args.mission_control_range,
            )
        except Exception as exc:
            print(f"review_failed={str(exc)}")
            return 1

    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"{status} {check.name} {check.detail}")

    print(f"review_summary={'PASS' if report.passed else 'FAIL'}")
    return 0 if report.passed else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review dry-run evidence without mutating state")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live-crypto-order-id", type=UUID)
    group.add_argument("--audit-correlation-id", type=UUID)
    parser.add_argument("--mission-control-range", default="24h", choices=["24h", "72h", "7d", "30d", "90d", "all"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run_review(args))


if __name__ == "__main__":
    raise SystemExit(main())
