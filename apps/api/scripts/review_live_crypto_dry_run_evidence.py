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
from app.models.exchange_connection import ExchangeConnection
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.risk_event import RiskEvent
from app.services import mission_control_intelligence as mission_control_service
from app.services.live_crypto_environment import inspect_live_crypto_environment


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


def _normalize_exchange_environment(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized not in {"production", "sandbox"}:
        raise ValueError(f"unsupported exchange environment: {environment}")
    return normalized


def _profile_environment(profile: LiveTradingProfile) -> str | None:
    provenance = profile.provenance_metadata if isinstance(profile.provenance_metadata, dict) else {}
    explicit = provenance.get("exchange_environment") or provenance.get("environment")
    if explicit is not None:
        try:
            return _normalize_exchange_environment(str(explicit))
        except ValueError:
            return None
    registration_source = str(provenance.get("registration_source") or "").lower()
    if "sandbox" in registration_source:
        return "sandbox"
    if "production" in registration_source or registration_source.startswith("human_"):
        return "production"
    return "production"


async def _load_connection(*, db, exchange_connection_id) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.exchange_connection_id == exchange_connection_id)
        .limit(1)
    )


async def _count_rows(db, statement) -> int:
    count = await db.scalar(statement)
    return int(count or 0)


async def verify_dry_run_evidence(
    *,
    db,
    live_crypto_order_id: UUID | None,
    audit_correlation_id: UUID | None,
    mission_control_range: str,
    expected_environment: str = "production",
) -> ReviewReport:
    checks: list[ReviewCheck] = []
    settings = get_settings()
    expected_environment = _normalize_exchange_environment(expected_environment)
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
    connection = await _load_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
    approval_event = None
    if approval_event_id is not None:
        approval_event = await db.scalar(
            select(LiveApprovalEvent)
            .where(LiveApprovalEvent.id == approval_event_id)
            .limit(1)
        )

    checks.append(
        ReviewCheck(
            "submission_flag_disabled",
            settings.live_crypto_order_submission_enabled is False,
            f"live_crypto_order_submission_enabled={settings.live_crypto_order_submission_enabled}",
        )
    )
    checks.append(
        ReviewCheck(
            "environment_expected",
            str(live_order.environment) == expected_environment,
            f"live_order_environment={live_order.environment}",
        )
    )
    checks.append(
        ReviewCheck(
            "preview_environment_matches",
            str(preview.environment) == expected_environment,
            f"preview_environment={preview.environment}",
        )
    )
    checks.append(
        ReviewCheck(
            "profile_environment_matches",
            _profile_environment(profile) == expected_environment,
            f"profile_environment={_profile_environment(profile) or 'missing'}",
        )
    )
    approval_environment = None if approval_event is None or not isinstance(approval_event.approval_scope, dict) else approval_event.approval_scope.get("environment")
    checks.append(
        ReviewCheck(
            "approval_environment_matches",
            str(approval_environment) == expected_environment,
            f"approval_environment={approval_environment or 'missing'}",
        )
    )
    checks.append(
        ReviewCheck(
            "connection_environment_matches",
            connection is not None and str(connection.environment) == expected_environment,
            f"connection_environment={None if connection is None else connection.environment}",
        )
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

    checks.append(ReviewCheck("approval_event_linked", approval_event is not None, f"approval_event_found={approval_event is not None}"))

    risk_event = None
    if risk_event_id_raw is not None:
        risk_event = await db.scalar(
            select(RiskEvent)
            .where(RiskEvent.id == UUID(str(risk_event_id_raw)))
            .limit(1)
        )
    checks.append(ReviewCheck("risk_event_linked", risk_event is not None, f"risk_event_found={risk_event is not None}"))

    recorded_environment = safe_provider_response.get("exchange_environment")
    checks.append(
        ReviewCheck(
            "safe_environment_matches",
            str(recorded_environment) == expected_environment,
            f"safe_exchange_environment={recorded_environment or 'missing'}",
        )
    )
    provider_mock_mode_enabled = bool(safe_provider_response.get("provider_mock_mode_enabled", False))
    if expected_environment == "sandbox":
        checks.append(
            ReviewCheck(
                "rehearsal_mode_labeled",
                str(safe_provider_response.get("rehearsal_mode", "")) in {"coinbase_sandbox", "controlled_provider_mock"},
                f"rehearsal_mode={safe_provider_response.get('rehearsal_mode', 'missing')}",
            )
        )
        checks.append(
            ReviewCheck(
                "provider_mock_boundary",
                provider_mock_mode_enabled in {True, False},
                f"provider_mock_mode_enabled={provider_mock_mode_enabled}",
            )
        )

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

    mc_items = getattr(getattr(mission_control, "operations", None), "live_crypto_readiness", None)
    readiness_items = [] if mc_items is None else list(getattr(mc_items, "items", []))
    if expected_environment == "sandbox":
        checks.append(
            ReviewCheck(
                "mission_control_sandbox_readiness_present",
                any(getattr(item, "key", None) == "sandbox_exchange_connection" for item in readiness_items),
                f"sandbox_items={len(readiness_items)}",
            )
        )
        checks.append(
            ReviewCheck(
                "mission_control_production_not_ready",
                any(getattr(item, "key", None) == "production_account_status" and not getattr(item, "ready", False) for item in readiness_items),
                "production readiness remains false",
            )
        )

        production_state = await _maybe_await(
            inspect_live_crypto_environment(db=db, exchange_environment="production")
        )
        checks.append(
            ReviewCheck(
                "production_readiness_false",
                not production_state.ready,
                f"production_ready={production_state.ready}",
            )
        )

    return ReviewReport(checks=checks)


async def _run_review(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        try:
            report = await verify_dry_run_evidence(
                db=db,
                live_crypto_order_id=args.live_crypto_order_id,
                audit_correlation_id=args.audit_correlation_id,
                mission_control_range=args.mission_control_range,
                expected_environment=args.expected_environment,
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
    parser.add_argument("--expected-environment", default="production", choices=["production", "sandbox"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run_review(args))


if __name__ == "__main__":
    raise SystemExit(main())
