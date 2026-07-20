from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.asset import Asset
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.models.strategy_aggregate_decision import StrategyAggregateDecision
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.decisions.ingestion import DECISION_ENGINE_VERSION
from app.services.position_lifecycle.contracts import PositionLifecycleEvaluation
from app.schemas.capital_campaign_domain import (
    CapitalCampaignDefinitionResponse,
    CapitalCampaignPreviewRequest,
    LifecycleEvidenceInput,
    RiskPreviewInput,
    StrategyEvidenceInput,
)
from app.services.capital_campaign_domain.preview_engine import build_campaign_preview
from app.services.position_lifecycle.evaluator import evaluate_position_lifecycle
from app.services.position_lifecycle.policy_registry import resolve_lifecycle_policy
from app.services.position_lifecycle.source_adapter import load_position_snapshots
from app.services.profitability.engine import ProfitabilityInput, evaluate_exit_profitability
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceRequest,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
    persist_risk_decision,
)
from app.services.risk.risk_context import resolve_execution_risk_context
from app.services.strategy_outcomes.service import fetch_strategy_scorecards
from app.models.parameter_set import ParameterSet
from app.services.strategy_roster.decision_aggregator import (
    AGGREGATE_STRATEGY_IDENTITY,
    AGGREGATE_STRATEGY_SLUG,
    AGGREGATE_STRATEGY_VERSION,
    AggregationConfig,
    AggregationResult,
    StrategyOutcomeSummary,
    StrategyProposalInput,
    aggregate_strategy_proposals,
    resolve_action_position_transition,
)
from app.services.strategy_roster.registry import ENABLED_PHASE1_ROSTER

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CampaignAuthoritativeCycleResult:
    composition: dict[str, Any]
    preview: Any | None


_DEFAULT_INGESTION_GRACE_MINUTES = 5
_INTERVAL_INGESTION_GRACE_MINUTES = {
    "15m": _DEFAULT_INGESTION_GRACE_MINUTES,
}


def _interval_minutes(interval: str | None) -> int | None:
    value = str(interval or "").strip().lower()
    if not value:
        return None
    if value.endswith("m"):
        raw = value[:-1]
        return int(raw) if raw.isdigit() and int(raw) > 0 else None
    if value.endswith("h"):
        raw = value[:-1]
        return int(raw) * 60 if raw.isdigit() and int(raw) > 0 else None
    if value.endswith("d"):
        raw = value[:-1]
        return int(raw) * 1440 if raw.isdigit() and int(raw) > 0 else None
    return None


def _normalize_symbol(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _product_symbol(value: str) -> str:
    return _normalize_symbol(value).split("-", 1)[0]


def _trigger_to_instrument(trigger: str) -> str | None:
    parts = [item.strip().lower() for item in trigger.split("_") if item.strip()]
    if len(parts) < 3:
        return None
    product_token = parts[1].upper()
    if not product_token:
        return None
    return f"{product_token}-USD"


def _scoped_instruments_for_trigger(*, allowed_instruments: list[str], trigger: str) -> list[str]:
    normalized_allowed = []
    seen: set[str] = set()
    for item in allowed_instruments:
        normalized = _normalize_symbol(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_allowed.append(normalized)
    trigger_instrument = _trigger_to_instrument(trigger)
    if trigger_instrument is not None and trigger_instrument in seen:
        return [trigger_instrument]
    return normalized_allowed


def _extract_preferred_strategy_identity(metadata_evidence: dict[str, Any]) -> str | None:
    candidates = [
        metadata_evidence.get("canonical_strategy_identity"),
        metadata_evidence.get("selected_strategy_identity"),
        metadata_evidence.get("strategy_identity"),
    ]
    strategy_blob = metadata_evidence.get("strategy")
    if isinstance(strategy_blob, dict):
        candidates.extend(
            [
                strategy_blob.get("canonical_strategy_identity"),
                strategy_blob.get("selected_strategy_identity"),
                strategy_blob.get("strategy_identity"),
            ]
        )
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return None


async def _load_campaign_strategy_authority(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    campaign_version: int,
    metadata_evidence: dict[str, Any],
) -> dict[str, Any]:
    preferred_identity = _extract_preferred_strategy_identity(metadata_evidence)
    if preferred_identity:
        return {
            "authority_source": "campaign_metadata_evidence",
            "preferred_strategy_identity": preferred_identity,
        }

    try:
        package = await db.scalar(
            select(CanonicalPreviewPackage)
            .where(CanonicalPreviewPackage.campaign_id == campaign_id)
            .where(CanonicalPreviewPackage.campaign_version == campaign_version)
            .where(
                CanonicalPreviewPackage.package_state.in_(
                    ("READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED")
                )
            )
            .order_by(desc(CanonicalPreviewPackage.updated_at), desc(CanonicalPreviewPackage.generated_at))
            .limit(1)
        )
    except Exception:
        package = None

    if package is None:
        return {"authority_source": "none", "preferred_strategy_identity": None}
    if not all(hasattr(package, attr) for attr in ("package_id", "strategy_id", "parameter_set_id", "strategy_version")):
        return {"authority_source": "none", "preferred_strategy_identity": None}

    strategy_slug = None
    try:
        strategy = await db.scalar(select(Strategy).where(Strategy.id == package.strategy_id).limit(1))
    except Exception:
        strategy = None
    if strategy is not None and hasattr(strategy, "slug"):
        strategy_slug = str(strategy.slug)

    return {
        "authority_source": "canonical_preview_package_continuity_only",
        "package_id": str(package.package_id),
        "strategy_id": str(package.strategy_id),
        "parameter_set_id": str(package.parameter_set_id),
        "preferred_strategy_identity": None,
        "historical_strategy_identity": (f"{strategy_slug}@{package.strategy_version}" if strategy_slug else None),
    }


def _primary_rejection_reason(*, rejected_candidates: list[dict[str, Any]], failed_closed: bool) -> str:
    if not rejected_candidates:
        return "no_qualifying_candidate"
    if failed_closed:
        priority = [
            "risk_unavailable",
            "strategy_evidence_unavailable",
            "market_data_unavailable",
            "stale_market_data",
            "asset_mapping_unavailable",
            "provider_product_unsupported",
            "ambiguous_market_source",
        ]
    else:
        priority = [
            "position_below_minimum_order_size",
            "allocation_below_minimum",
            "non_positive_net_edge",
        ]
    for reason in priority:
        if any(item.get("reason") == reason for item in rejected_candidates):
            return reason
    return str(rejected_candidates[0].get("reason") or "no_qualifying_candidate")


def _preview_strategy_inputs_from_authoritative_evidence(*, strategy_evidence: dict[str, Any], allowed_instruments: list[str]) -> list[StrategyEvidenceInput]:
    items: list[StrategyEvidenceInput] = []
    seen: set[str] = set()
    for instrument in allowed_instruments:
        evidence = strategy_evidence.get(instrument)
        if not isinstance(evidence, dict):
            continue
        if str(evidence.get("authority_class") or "").strip().upper() != "AUTHORITATIVE":
            continue
        normalized = _normalize_symbol(instrument)
        if normalized in seen:
            continue
        seen.add(normalized)
        confidence = Decimal(str(evidence.get("confidence") or "0"))
        expected_gross_edge = Decimal(str(evidence.get("profitable_after_fees_performance") or evidence.get("expected_value") or "0"))
        items.append(
            StrategyEvidenceInput(
                instrument=normalized,
                authority_class="AUTHORITATIVE",
                confidence=confidence,
                expected_gross_edge=expected_gross_edge,
                expected_fees=Decimal("0"),
                expected_slippage=Decimal("0"),
            )
        )
    return items


def _preview_lifecycle_inputs_from_authoritative_evidence(*, position_evidence: dict[str, Any], allowed_instruments: list[str]) -> list[LifecycleEvidenceInput]:
    items: list[LifecycleEvidenceInput] = []
    seen: set[str] = set()
    for instrument in allowed_instruments:
        evidence = position_evidence.get(instrument)
        if not isinstance(evidence, dict):
            continue
        if str(evidence.get("authority_class") or "").strip().upper() not in {"AUTHORITATIVE", "STALE"}:
            continue
        lifecycle = evidence.get("lifecycle") if isinstance(evidence.get("lifecycle"), dict) else {}
        position = evidence.get("position") if isinstance(evidence.get("position"), dict) else {}
        normalized = _normalize_symbol(instrument)
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            LifecycleEvidenceInput(
                instrument=normalized,
                authority_class="AUTHORITATIVE",
                lifecycle_state=str(lifecycle.get("lifecycle_state") or "OPEN"),
                recommendation=str(lifecycle.get("recommendation") or "HOLD_FOR_PROFIT"),
                market_data_stale=bool(lifecycle.get("market_data_stale", False)),
                dust_indicator=bool(position.get("dust_indicator", False)),
                closed_indicator=bool(position.get("closed_indicator", False)),
                expected_net_realized_pnl_if_sold_now=None,
            )
        )
    return items


def _preview_risk_inputs_from_authoritative_evidence(*, risk_outputs: dict[str, Any], allowed_instruments: list[str]) -> list[RiskPreviewInput]:
    items: list[RiskPreviewInput] = []
    seen: set[str] = set()
    for instrument in allowed_instruments:
        evidence = risk_outputs.get(instrument)
        if not isinstance(evidence, dict):
            continue
        if str(evidence.get("authority_class") or "").strip().upper() != "AUTHORITATIVE":
            continue
        normalized = _normalize_symbol(instrument)
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            RiskPreviewInput(
                instrument=normalized,
                authority_class="AUTHORITATIVE",
                verdict=str(evidence.get("verdict") or "VETO"),
                reason=None if evidence.get("reason") is None else str(evidence.get("reason")),
                max_allocation=Decimal(str(evidence.get("approved_quantity") or "0")),
            )
        )
    return items


def _split_strategy_identity(identity: str | None) -> tuple[str, str | None]:
    raw = str(identity or "").strip()
    if not raw:
        return "", None
    if "@" not in raw:
        return raw, None
    slug, version = raw.split("@", 1)
    slug = slug.strip()
    version = version.strip() or None
    return slug, version


def _strategy_identity_is_coherent(*, strategy_identity: str | None, strategy_version: str | None) -> bool:
    identity_slug, identity_version = _split_strategy_identity(strategy_identity)
    if not identity_slug:
        return False
    reported_version = str(strategy_version or "").strip()
    if not reported_version:
        return identity_version is None
    reported_slug, reported_only_version = _split_strategy_identity(reported_version)
    if reported_only_version is None:
        # plain version string (for example: "1.0.0")
        return identity_version is None or identity_version == reported_slug
    # full identity string (for example: "ma_crossover@1.0.0")
    if reported_slug and reported_slug != identity_slug:
        return False
    return identity_version is None or reported_only_version == identity_version


def _resolve_decision_signal_identity(decision_record: DecisionRecord) -> tuple[str | None, str | None, str | None, str | None]:
    signals = decision_record.generated_signals if isinstance(decision_record.generated_signals, list) else []
    identities: list[tuple[str, str, str]] = []
    for item in signals:
        if not isinstance(item, dict):
            continue
        raw_signal_identity = str(item.get("strategy_identity") or item.get("strategy") or item.get("strategy_slug") or "").strip()
        raw_signal_version = str(item.get("strategy_version") or item.get("version") or "").strip()
        signal_action = str(item.get("action") or "").strip().upper()
        if not raw_signal_identity and not raw_signal_version:
            continue

        signal_slug, signal_identity_version = _split_strategy_identity(raw_signal_identity)
        version_slug, version_only = _split_strategy_identity(raw_signal_version)

        if signal_slug and version_only and version_slug and version_slug != signal_slug:
            return None, None, None, "strategy_identity_incoherent"

        resolved_slug = signal_slug or version_slug
        resolved_version = version_only or signal_identity_version
        if not resolved_slug:
            return None, None, None, "strategy_evidence_unavailable"
        resolved_identity = resolved_slug if resolved_version is None else f"{resolved_slug}@{resolved_version}"
        identities.append((resolved_identity, resolved_identity, signal_action or "HOLD"))

    if not identities:
        return None, None, None, "strategy_evidence_unavailable"

    unique = {(identity, version, action) for identity, version, action in identities}
    if len(unique) > 1:
        return None, None, None, "strategy_identity_incoherent"
    identity, version, action = identities[0]
    return identity, version, action, None


async def _load_runtime_campaign(*, db: AsyncSession, runtime_campaign_uuid: UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == runtime_campaign_uuid).limit(1))


async def _load_latest_asset(*, db: AsyncSession, symbol: str, exchange: str) -> Asset | None:
    result = await db.execute(
        select(Asset)
        .where(Asset.symbol == symbol)
        .where(Asset.exchange == exchange)
        .where(Asset.asset_class == "crypto")
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc(), Asset.id.desc())
    )
    assets = list(result.scalars().all())
    if not assets:
        return None
    if len(assets) > 1:
        return None
    return assets[0]


async def _load_latest_closed_candle(*, db: AsyncSession, asset_id: UUID, interval: str, now: datetime) -> Candle | None:
    result = await db.execute(
        select(Candle)
        .where(Candle.asset_id == asset_id)
        .where(Candle.interval == interval)
        .where(Candle.close_time <= now)
        .order_by(Candle.close_time.desc(), Candle.open_time.desc(), Candle.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _build_aggregate_decision_idempotency_key(
    *,
    roster_run_id: UUID,
    asset_id: UUID,
    candle_close_time: datetime,
    campaign_id: UUID,
    campaign_version: int,
    config_version: str,
    environment: str,
) -> str:
    return _hash_payload(
        {
            "kind": "strategy_aggregate_decision",
            "roster_run_id": str(roster_run_id),
            "asset_id": str(asset_id),
            "candle_close_time": candle_close_time.isoformat(),
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "config_version": config_version,
            "environment": environment,
        }
    )


async def _recent_strategy_failure_streak(
    *,
    db: AsyncSession,
    asset_id: UUID,
    interval: str,
    strategy_slug: str,
    before_candle_close_time: datetime,
    limit: int = 5,
) -> int:
    result = await db.execute(
        select(StrategyRosterProposal.evaluation_status)
        .where(StrategyRosterProposal.asset_id == asset_id)
        .where(StrategyRosterProposal.interval == interval)
        .where(StrategyRosterProposal.strategy_slug == strategy_slug)
        .where(StrategyRosterProposal.candle_close_time < before_candle_close_time)
        .order_by(StrategyRosterProposal.candle_close_time.desc())
        .limit(limit)
    )
    streak = 0
    for row in result.all():
        if row[0] == "FAILED":
            streak += 1
        else:
            break
    return streak


async def _load_latest_roster_proposal_group(
    *,
    db: AsyncSession,
    asset_id: UUID,
    provider: str,
    product_id: str,
    interval: str,
    expected_candle_close_time: datetime,
    required_trigger: str,
    now: datetime,
    max_age_minutes: int,
    scheduled_cycle_id: UUID | None = None,
) -> tuple[StrategyRosterRun | None, list[StrategyRosterProposal], str | None]:
    """Select one complete roster run by exact governed scope, never inference."""
    statement = (
        select(StrategyRosterRun)
        .where(StrategyRosterRun.asset_id == asset_id)
        .where(StrategyRosterRun.provider == provider)
        .where(StrategyRosterRun.product_id == product_id)
        .where(StrategyRosterRun.interval == interval)
        .where(StrategyRosterRun.candle_close_time == expected_candle_close_time)
        .where(StrategyRosterRun.trigger == required_trigger)
    )
    if scheduled_cycle_id is not None:
        statement = statement.where(StrategyRosterRun.scheduled_cycle_id == scheduled_cycle_id)
    runs = list((await db.execute(statement)).scalars().all())
    if not runs:
        return None, [], "exact_roster_run_unavailable"
    if len(runs) != 1:
        return None, [], "ambiguous_exact_roster_run"
    run = runs[0]
    candle_age_seconds = (_as_utc(now) - _as_utc(run.candle_close_time)).total_seconds()
    if (
        run.completed_at is None
        or run.strategies_failed_count != 0
        or bool(run.strategies_failed)
        or run.strategies_completed_count != run.strategies_requested_count
        or sorted(run.strategies_completed) != sorted(run.strategies_requested)
        # strategy_roster.service always persists error_summary as
        # {"failed": failed} -- a non-empty dict even when failed == [].
        # Checking truthiness of the dict itself (rather than its "failed"
        # payload) made every roster run, including fully successful ones,
        # look "failed"; the actual failure signal is whether that list is
        # non-empty, which strategies_failed/strategies_failed_count above
        # already establish independently.
        or bool((run.error_summary or {}).get("failed"))
        or run.execution_mode != "SHADOW"
        or run.live_submission_allowed
        or candle_age_seconds < -60
        or candle_age_seconds > max_age_minutes * 60
    ):
        return None, [], "roster_run_incomplete_or_failed"
    proposals = list(
        (
            await db.execute(
                select(StrategyRosterProposal)
                .where(StrategyRosterProposal.roster_run_id == run.roster_run_id)
                .order_by(StrategyRosterProposal.strategy_slug.asc(), StrategyRosterProposal.proposal_id.asc())
            )
        ).scalars().all()
    )
    if len(proposals) != run.strategies_completed_count:
        return None, [], "roster_proposal_count_mismatch"
    for proposal in proposals:
        if (
            proposal.asset_id != run.asset_id
            or proposal.provider != run.provider
            or proposal.product_id != run.product_id
            or proposal.interval != run.interval
            or proposal.candle_close_time != run.candle_close_time
            or proposal.scheduled_cycle_id != run.scheduled_cycle_id
            or proposal.execution_mode != "SHADOW"
            or proposal.live_submission_allowed
        ):
            return None, [], "roster_proposal_scope_conflict"
    return run, proposals, None


def _resolve_scorecard_summary(*, scorecard_by_slug: dict[str, Any], dominant_contributor_identity: str | None) -> Any | None:
    # The aggregate's reported strategy_identity is always the canonical
    # AGGREGATE_STRATEGY_IDENTITY (never a real roster slug -- see item 1 of
    # the production-safety review), so the representative scorecard shown in
    # evidence must be looked up via the informational dominant_contributor_identity,
    # not the reported identity itself.
    dominant_slug = dominant_contributor_identity.split("@", 1)[0] if dominant_contributor_identity else None
    return scorecard_by_slug.get(dominant_slug) if dominant_slug else None


def _build_aggregate_evidence_dict(
    *,
    roster_run_id: UUID,
    candle_close_time: datetime,
    aggregate_decision_id: UUID,
    decision_record: DecisionRecord,
    final_action: str,
    primary_strategy_identity: str | None,
    primary_strategy_version: str | None,
    dominant_contributor_identity: str | None,
    eligible_strategy_count: int,
    weighted_buy_score: Decimal,
    weighted_sell_score: Decimal,
    weighted_hold_score: Decimal,
    thresholds_applied: dict[str, Any],
    deterministic_explanation: list[str],
    strategy_contributions: list[dict[str, Any]],
    scorecard_by_slug: dict[str, Any],
) -> dict[str, Any]:
    scorecard_summary = _resolve_scorecard_summary(scorecard_by_slug=scorecard_by_slug, dominant_contributor_identity=dominant_contributor_identity)
    score = None
    if primary_strategy_identity is not None:
        score = str(weighted_buy_score if final_action == "BUY" else (weighted_sell_score if final_action == "SELL" else weighted_hold_score))
    return {
        "authority_class": "AUTHORITATIVE",
        "source_type": "strategy_decision_aggregator",
        "source_identity": {
            "decision_record_id": str(decision_record.decision_id),
            "strategy_roster_run_id": str(roster_run_id),
            "aggregate_decision_id": str(aggregate_decision_id),
            "scorecard_strategy_slug": scorecard_summary.strategy_slug if scorecard_summary is not None else None,
        },
        "observed_at": candle_close_time.isoformat(),
        "freshness": "fresh",
        "availability": "available",
        "reason": "strategy evidence resolved from governed multi-strategy roster aggregate decision",
        "strategy_identity": primary_strategy_identity,
        "strategy_version": primary_strategy_version,
        "action": final_action,
        "score": score,
        "confidence": None,
        "sample_size": scorecard_summary.aggregate.total_evaluated if scorecard_summary is not None else 0,
        "profitable_after_fees_performance": None
        if scorecard_summary is None or scorecard_summary.aggregate.average_fee_adjusted_return_pct is None
        else format(scorecard_summary.aggregate.average_fee_adjusted_return_pct, "f"),
        "expected_value": None,
        "evidence_timestamp": candle_close_time.isoformat(),
        "scorecard": None
        if scorecard_summary is None
        else {
            "best_strategy_slug": scorecard_summary.strategy_slug,
            "aggregate_total_evaluated": scorecard_summary.aggregate.total_evaluated,
            "aggregate_average_fee_adjusted_return_pct": None
            if scorecard_summary.aggregate.average_fee_adjusted_return_pct is None
            else format(scorecard_summary.aggregate.average_fee_adjusted_return_pct, "f"),
            "aggregate_overall_correct_pct": None
            if scorecard_summary.aggregate.overall_correct_pct is None
            else format(scorecard_summary.aggregate.overall_correct_pct, "f"),
        },
        "decision_record": {
            "decision_id": str(decision_record.decision_id),
            "trade_accepted": decision_record.trade_accepted,
            "trade_rejected_reason": decision_record.trade_rejected_reason,
            "supporting_strategies": decision_record.supporting_strategies,
            "opposing_strategies": decision_record.opposing_strategies,
            "expected_risk": decision_record.expected_risk,
            "expected_reward": decision_record.expected_reward,
            "generated_signals": decision_record.generated_signals,
        },
        "aggregate_evidence": {
            "aggregate_decision_id": str(aggregate_decision_id),
            "dominant_contributor_identity": dominant_contributor_identity,
            "eligible_strategy_count": eligible_strategy_count,
            "weighted_buy_score": str(weighted_buy_score),
            "weighted_sell_score": str(weighted_sell_score),
            "weighted_hold_score": str(weighted_hold_score),
            "thresholds_applied": thresholds_applied,
            "deterministic_explanation": list(deterministic_explanation),
            "contributions": [
                {
                    "strategy_slug": item.get("strategy_slug") if isinstance(item, dict) else item.strategy_slug,
                    "strategy_identity": item.get("strategy_identity") if isinstance(item, dict) else item.strategy_identity,
                    "raw_action": item.get("raw_action") if isinstance(item, dict) else item.raw_action,
                    "eligible": item.get("eligible") if isinstance(item, dict) else item.eligible,
                    "exclusion_reason": item.get("exclusion_reason") if isinstance(item, dict) else item.exclusion_reason,
                    "weight": item.get("weight") if isinstance(item, dict) else item.weight,
                }
                for item in strategy_contributions
            ],
        },
    }


async def load_strategy_aggregate_evidence(
    *,
    db: AsyncSession,
    roster_run_id: UUID,
    asset_id: UUID,
    candle_close_time: datetime,
    campaign_id: UUID,
    campaign_version: int,
    config_version: str,
    environment: str,
    provider: str,
    product_id: str,
    interval: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Pure read. Never performs any INSERT/UPDATE/DELETE. Returns evidence
    for an aggregate decision only if one has already been persisted for this
    exact (roster_run_id, asset_id, candle_close_time, campaign_id,
    campaign_version, config_version, environment) scope; returns
    (None, "not_yet_computed") otherwise. Safe to call from diagnostics,
    dashboards, or any other read-only context."""
    idempotency_key = _build_aggregate_decision_idempotency_key(
        roster_run_id=roster_run_id,
        asset_id=asset_id,
        candle_close_time=candle_close_time,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        config_version=config_version,
        environment=environment,
    )
    aggregate_row = await db.scalar(
        select(StrategyAggregateDecision).where(StrategyAggregateDecision.idempotency_key == idempotency_key).limit(1)
    )
    if aggregate_row is None or aggregate_row.decision_record_id is None:
        return None, "not_yet_computed"
    decision_record = await db.get(DecisionRecord, aggregate_row.decision_record_id)
    if decision_record is None:
        return None, "aggregate_decision_record_missing"
    if (
        aggregate_row.roster_run_id != roster_run_id
        or aggregate_row.asset_id != asset_id
        or aggregate_row.candle_close_time != candle_close_time
        or aggregate_row.campaign_id != campaign_id
        or aggregate_row.campaign_version != campaign_version
        or aggregate_row.environment != environment
        or aggregate_row.provider != provider
        or aggregate_row.product_id != product_id
        or aggregate_row.interval != interval
    ):
        return None, "aggregate_campaign_scope_mismatch"
    if (
        aggregate_row.primary_strategy_identity != AGGREGATE_STRATEGY_IDENTITY
        or aggregate_row.primary_strategy_version != AGGREGATE_STRATEGY_VERSION
    ):
        return None, "aggregate_identity_conflict"
    generated_signals = decision_record.generated_signals if isinstance(decision_record.generated_signals, list) else []
    if len(generated_signals) != 1 or generated_signals[0].get("strategy_identity") != AGGREGATE_STRATEGY_IDENTITY:
        return None, "generated_signal_identity_conflict"
    if generated_signals[0].get("strategy_version") != AGGREGATE_STRATEGY_VERSION or generated_signals[0].get("action") != aggregate_row.final_action:
        return None, "generated_signal_payload_conflict"

    contributions = aggregate_row.strategy_contributions if isinstance(aggregate_row.strategy_contributions, list) else []
    contribution_lineage = {
        (item.get("strategy_identity"), item.get("raw_action"), str(item.get("weight")))
        for item in contributions
        if isinstance(item, dict) and item.get("eligible")
    }
    decision_lineage = {
        (item.get("strategy_identity"), item.get("action"), str(item.get("weight")))
        for item in list(decision_record.supporting_strategies or []) + list(decision_record.opposing_strategies or [])
        if isinstance(item, dict)
    }
    if decision_lineage != contribution_lineage:
        return None, "aggregate_contributor_lineage_mismatch"

    snapshot = await db.get(DecisionSnapshot, decision_record.decision_id)
    snapshot_inputs = snapshot.strategy_inputs if snapshot is not None and isinstance(snapshot.strategy_inputs, dict) else {}
    try:
        snapshot_scores_match = (
            Decimal(str(snapshot_inputs.get("weighted_buy_score"))) == Decimal(str(aggregate_row.weighted_buy_score))
            and Decimal(str(snapshot_inputs.get("weighted_sell_score"))) == Decimal(str(aggregate_row.weighted_sell_score))
            and Decimal(str(snapshot_inputs.get("weighted_hold_score"))) == Decimal(str(aggregate_row.weighted_hold_score))
        )
    except Exception:
        snapshot_scores_match = False
    if (
        snapshot is None
        or snapshot_inputs.get("roster_run_id") != str(roster_run_id)
        or snapshot_inputs.get("contributions") != contributions
        or not snapshot_scores_match
    ):
        return None, "decision_snapshot_aggregate_mismatch"

    scorecard_by_slug: dict[str, Any] = {}
    try:
        scorecards = await fetch_strategy_scorecards(db=db, provider=provider, product_id=product_id, interval=interval)
        scorecard_by_slug = {item.strategy_slug: item for item in scorecards}
    except Exception:
        scorecard_by_slug = {}

    evidence = _build_aggregate_evidence_dict(
        roster_run_id=roster_run_id,
        candle_close_time=candle_close_time,
        aggregate_decision_id=aggregate_row.aggregate_decision_id,
        decision_record=decision_record,
        final_action=aggregate_row.final_action,
        primary_strategy_identity=aggregate_row.primary_strategy_identity,
        primary_strategy_version=aggregate_row.primary_strategy_version,
        dominant_contributor_identity=aggregate_row.dominant_contributor_identity,
        eligible_strategy_count=aggregate_row.eligible_strategy_count,
        weighted_buy_score=Decimal(str(aggregate_row.weighted_buy_score)),
        weighted_sell_score=Decimal(str(aggregate_row.weighted_sell_score)),
        weighted_hold_score=Decimal(str(aggregate_row.weighted_hold_score)),
        thresholds_applied=aggregate_row.thresholds_applied,
        deterministic_explanation=aggregate_row.deterministic_explanation,
        strategy_contributions=contributions,
        scorecard_by_slug=scorecard_by_slug,
    )
    return evidence, None


async def resolve_or_create_strategy_aggregate_evidence(
    *,
    db: AsyncSession,
    asset_id: UUID,
    product_id: str,
    interval: str,
    campaign_id: UUID,
    campaign_version: int,
    environment: str,
    paper_account_id: UUID,
    runtime_campaign_id: int,
    asset: Asset,
    candle_item: Candle,
    now: datetime,
    provider: str = "kraken_spot",
    actor: str = "strategy_decision_aggregator",
    required_trigger: str,
    scheduled_cycle_id: UUID | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Governed read-or-create operation for the authoritative campaign's
    strategy evidence. Tries a pure read first (load_strategy_aggregate_evidence);
    only computes and persists a new StrategyAggregateDecision + DecisionRecord +
    DecisionSnapshot when no matching one exists yet for this exact scope.
    This function DOES write to the database on a cache-miss -- callers must
    only invoke it from within the authoritative campaign composition
    transaction, never from a read-only diagnostic context (use
    load_strategy_aggregate_evidence for that instead). Persistence is
    idempotent: repeated calls for the same scope never create duplicate rows."""
    settings = get_settings()

    run, proposals, selection_reason = await _load_latest_roster_proposal_group(
        db=db,
        asset_id=asset_id,
        provider=provider,
        product_id=product_id,
        interval=interval,
        expected_candle_close_time=candle_item.close_time,
        required_trigger=required_trigger,
        now=now,
        max_age_minutes=settings.strategy_aggregator_max_evidence_age_minutes,
        scheduled_cycle_id=scheduled_cycle_id,
    )
    if run is None or not proposals:
        logger.info(
            "strategy_aggregate_skipped reason=%s asset_id=%s product_id=%s interval=%s",
            selection_reason or "no_roster_proposals", asset_id, product_id, interval,
        )
        return None, selection_reason or "strategy_evidence_unavailable"

    roster_run_id = proposals[0].roster_run_id
    candle_close_time = proposals[0].candle_close_time

    # Pure-read fast path: if this exact scope was already resolved (e.g. a
    # prior cycle, or a retry after a downstream failure earlier in the same
    # composition), reuse it without any write and without the cost of
    # re-fetching scorecards/position/failure-streak evidence.
    cached_evidence, cached_reason = await load_strategy_aggregate_evidence(
        db=db,
        roster_run_id=roster_run_id,
        asset_id=asset_id,
        candle_close_time=candle_close_time,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        config_version=settings.strategy_aggregator_config_version,
        environment=environment,
        provider=provider,
        product_id=product_id,
        interval=interval,
    )
    if cached_evidence is not None:
        logger.info(
            "strategy_aggregate_skipped roster_run_id=%s campaign_id=%s candle_close=%s reason=idempotent_replay",
            roster_run_id, campaign_id, candle_close_time.isoformat(),
        )
        return cached_evidence, None

    data_quality_failed = False
    scorecard_by_slug: dict[str, Any] = {}
    try:
        scorecards = await fetch_strategy_scorecards(db=db, provider=provider, product_id=product_id, interval=interval)
        scorecard_by_slug = {item.strategy_slug: item for item in scorecards}
    except Exception:
        data_quality_failed = True

    proposal_inputs: list[StrategyProposalInput] = []
    for proposal in proposals:
        outcome_evidence = None
        scorecard = scorecard_by_slug.get(proposal.strategy_slug)
        if scorecard is not None:
            outcome_evidence = StrategyOutcomeSummary(
                sample_size=scorecard.aggregate.total_evaluated,
                overall_correct_pct=scorecard.aggregate.overall_correct_pct,
                average_fee_adjusted_return_pct=scorecard.aggregate.average_fee_adjusted_return_pct,
                # Reliable current-regime evidence is not yet available at this
                # integration point -- do not fabricate a match/mismatch signal.
                regime_match=None,
            )
        failure_streak = await _recent_strategy_failure_streak(
            db=db,
            asset_id=asset_id,
            interval=interval,
            strategy_slug=proposal.strategy_slug,
            before_candle_close_time=candle_close_time,
        )
        proposal_inputs.append(
            StrategyProposalInput(
                strategy_slug=proposal.strategy_slug,
                strategy_identity=proposal.strategy_identity,
                strategy_version=proposal.strategy_version,
                action=proposal.action,
                confidence=proposal.confidence,
                strength=proposal.strength,
                evaluation_status=proposal.evaluation_status,
                evaluated_at=proposal.evaluated_at,
                roster_run_id=str(proposal.roster_run_id),
                asset_id=str(proposal.asset_id),
                candle_close_time=proposal.candle_close_time,
                registered_and_enabled=proposal.strategy_slug in ENABLED_PHASE1_ROSTER,
                outcome_evidence=outcome_evidence,
                recent_failure_streak=failure_streak,
            )
        )

    position = await _load_position_evidence(
        db=db, account_id=paper_account_id, campaign_id=runtime_campaign_id, symbol=product_id, asset=asset, candle=candle_item, now=now
    )
    position_row = position.get("position") if isinstance(position, dict) else None
    position_open = bool(
        position_row is not None
        and position_row.get("closed_indicator") is False
        and position_row.get("quantity") not in (None, "0", "0.0")
    )
    if position.get("authority_class") == "UNAVAILABLE":
        position_state = "UNKNOWN"
    else:
        position_state = "OPEN" if position_open else "FLAT"

    config = AggregationConfig(
        config_version=settings.strategy_aggregator_config_version,
        min_eligible_strategies=settings.strategy_aggregator_min_eligible_strategies,
        min_buy_agreement=settings.strategy_aggregator_min_buy_agreement,
        min_sell_agreement=settings.strategy_aggregator_min_sell_agreement,
        min_confidence=settings.strategy_aggregator_min_confidence,
        max_evidence_age_minutes=settings.strategy_aggregator_max_evidence_age_minutes,
        min_outcome_sample_size=settings.strategy_aggregator_min_outcome_sample_size,
        veto_on_data_quality_failure=settings.strategy_aggregator_veto_on_data_quality_failure,
    )

    result = aggregate_strategy_proposals(
        proposals=proposal_inputs,
        position_open=position_open,
        now=now,
        config=config,
        data_quality_failed=data_quality_failed,
    )

    idempotency_key = _build_aggregate_decision_idempotency_key(
        roster_run_id=roster_run_id,
        asset_id=asset_id,
        candle_close_time=candle_close_time,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        config_version=config.config_version,
        environment=environment,
    )

    # The cache-miss path above already proved no row exists for this exact
    # idempotency key; _persist_strategy_aggregate_decision performs the one
    # and only write for this scope. A concurrent duplicate insert (a second
    # worker cycle racing this one) is prevented at the database level by the
    # UNIQUE constraint on strategy_aggregate_decisions.idempotency_key.
    try:
        async with db.begin_nested():
            aggregate_row, decision_record = await _persist_strategy_aggregate_decision(
                db=db,
                idempotency_key=idempotency_key,
                roster_run_id=roster_run_id,
                asset_id=asset_id,
                candle_close_time=candle_close_time,
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                environment=environment,
                provider=provider,
                product_id=product_id,
                interval=interval,
                position_state=position_state,
                result=result,
                actor=actor,
            )
    except IntegrityError:
        # A competing worker may have won the exact idempotency-key race. The
        # savepoint rolls back only this attempted insert chain; validate and
        # reuse the winner rather than poisoning the caller's transaction.
        raced_evidence, raced_reason = await load_strategy_aggregate_evidence(
            db=db,
            roster_run_id=roster_run_id,
            asset_id=asset_id,
            candle_close_time=candle_close_time,
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            config_version=config.config_version,
            environment=environment,
            provider=provider,
            product_id=product_id,
            interval=interval,
        )
        if raced_evidence is not None:
            return raced_evidence, None
        return None, f"aggregate_idempotency_conflict:{raced_reason or 'winner_unavailable'}"
    log_event = "strategy_aggregate_failed_closed" if result.failed_closed else "strategy_aggregate_completed"

    logger.info(
        "%s roster_run_id=%s campaign_id=%s candle_close=%s eligible=%s buy=%s sell=%s hold=%s action=%s reason=%s",
        log_event, roster_run_id, campaign_id, candle_close_time.isoformat(),
        result.eligible_strategy_count, result.weighted_buy_score, result.weighted_sell_score,
        result.weighted_hold_score, result.final_action, result.explanation,
    )

    if decision_record is None or aggregate_row.decision_record_id is None:
        return None, "strategy_evidence_unavailable"

    evidence = _build_aggregate_evidence_dict(
        roster_run_id=roster_run_id,
        candle_close_time=candle_close_time,
        aggregate_decision_id=aggregate_row.aggregate_decision_id,
        decision_record=decision_record,
        final_action=result.final_action,
        primary_strategy_identity=result.primary_strategy_identity,
        primary_strategy_version=result.primary_strategy_version,
        dominant_contributor_identity=result.dominant_contributor_identity,
        eligible_strategy_count=result.eligible_strategy_count,
        weighted_buy_score=result.weighted_buy_score,
        weighted_sell_score=result.weighted_sell_score,
        weighted_hold_score=result.weighted_hold_score,
        thresholds_applied=result.thresholds_applied,
        deterministic_explanation=list(result.deterministic_explanation),
        strategy_contributions=list(result.contributions),
        scorecard_by_slug=scorecard_by_slug,
    )
    return evidence, None


async def _ensure_aggregate_strategy_catalog_entry(*, db: AsyncSession, actor: str) -> Strategy:
    """Idempotently ensures a real, active Strategy catalog row (+ a
    ParameterSet) exists for AGGREGATE_STRATEGY_IDENTITY. Canonical package
    composition (_resolve_strategy_and_parameter_binding in
    canonical_preview_package.py) requires a real Strategy row matching
    (slug, module_version, is_active=True) plus a ParameterSet to resolve any
    strategy_identity it is handed -- including this one."""
    strategy = await db.scalar(
        select(Strategy)
        .where(Strategy.slug == AGGREGATE_STRATEGY_SLUG)
        .where(Strategy.module_version == AGGREGATE_STRATEGY_VERSION)
        .limit(1)
    )
    if strategy is None:
        strategy = Strategy(
            name="Strategy Roster Aggregate",
            slug=AGGREGATE_STRATEGY_SLUG,
            description=(
                "Canonical identity for governed multi-strategy roster aggregate decisions "
                "(app.services.strategy_roster.decision_aggregator). Not an independently "
                "executable strategy module -- represents the ensemble outcome, never an "
                "individual contributor."
            ),
            module_version=AGGREGATE_STRATEGY_VERSION,
            is_active=True,
        )
        db.add(strategy)
        await db.flush()
    elif not strategy.is_active:
        # Fail closed rather than silently reactivating a catalog row someone
        # deliberately deactivated.
        raise ValueError("strategy_roster_aggregate catalog entry exists but is not active")

    parameter_set = await db.scalar(select(ParameterSet).where(ParameterSet.strategy_id == strategy.id).limit(1))
    if parameter_set is None:
        db.add(
            ParameterSet(
                strategy_id=strategy.id,
                label="default",
                params={},
                created_by=actor,
            )
        )
        await db.flush()
    return strategy


async def _persist_strategy_aggregate_decision(
    *,
    db: AsyncSession,
    idempotency_key: str,
    roster_run_id: UUID,
    asset_id: UUID,
    candle_close_time: datetime,
    campaign_id: UUID,
    campaign_version: int,
    environment: str,
    provider: str,
    product_id: str,
    interval: str,
    position_state: str,
    result: AggregationResult,
    actor: str,
) -> tuple[StrategyAggregateDecision, DecisionRecord]:
    now = datetime.now(timezone.utc)
    if result.primary_strategy_identity == AGGREGATE_STRATEGY_IDENTITY:
        await _ensure_aggregate_strategy_catalog_entry(db=db, actor=actor)
    contribution_payload = [
        {
            "strategy_slug": item.strategy_slug,
            "strategy_identity": item.strategy_identity,
            "raw_action": item.raw_action,
            "raw_confidence": item.raw_confidence,
            "raw_strength": item.raw_strength,
            "eligible": item.eligible,
            "exclusion_reason": item.exclusion_reason,
            "weight": item.weight,
            "evidence_basis": item.evidence_basis,
            "weighted_buy": item.weighted_buy,
            "weighted_sell": item.weighted_sell,
            "weighted_hold": item.weighted_hold,
        }
        for item in result.contributions
    ]
    supporting_strategies = [
        {"strategy_identity": item.strategy_identity, "action": item.raw_action, "weight": item.weight}
        for item in result.contributions
        if item.eligible and item.raw_action == result.final_action
    ]
    opposing_strategies = [
        {"strategy_identity": item.strategy_identity, "action": item.raw_action, "weight": item.weight}
        for item in result.contributions
        if item.eligible and item.raw_action != result.final_action
    ]

    decision_record = DecisionRecord(
        idempotency_key=f"strategy_aggregate_decision:{idempotency_key}",
        source_lineage={
            "strategy_roster_runs": [str(roster_run_id)],
            "campaigns": [str(campaign_id)],
            "risk_events": [],
            "crypto_order_previews": [],
            "signals": [],
            "model_outputs": [],
            "trades": [],
        },
        field_provenance={
            "generated_signals": [{"entity_type": "strategy_roster_runs", "entity_id": str(roster_run_id)}],
            "supporting_strategies": [{"entity_type": "strategy_roster_runs", "entity_id": str(roster_run_id)}],
        },
        version=DECISION_ENGINE_VERSION,
        timestamp=now,
        asset={"product_id": product_id, "provider": provider},
        timeframe=interval,
        market_regime={"state": "unknown", "source": "strategy_decision_aggregator"},
        indicators={},
        generated_signals=[
            {
                "strategy_identity": result.primary_strategy_identity,
                "strategy_version": result.primary_strategy_version,
                "action": result.final_action,
            }
        ]
        if result.primary_strategy_identity is not None
        else [{"strategy_identity": None, "strategy_version": None, "action": result.final_action}],
        signal_strength=None,
        confidence=None,
        supporting_strategies=supporting_strategies,
        opposing_strategies=opposing_strategies,
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=result.final_action in {"BUY", "SELL"} and not result.failed_closed,
        trade_rejected_reason=None if result.final_action in {"BUY", "SELL"} and not result.failed_closed else result.explanation,
        execution_details={"stage": "strategy_decision_aggregator", "actor": actor},
        exit_details=None,
        pnl=None,
        duration=None,
        outcome="pending_preview" if result.final_action in {"BUY", "SELL"} else "not_taken",
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=["strategy_decision_aggregator"],
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )
    db.add(decision_record)
    await db.flush()

    snapshot = DecisionSnapshot(
        decision_id=decision_record.decision_id,
        timestamp=now,
        asset=decision_record.asset,
        exchange=provider,
        timeframe=interval,
        ohlcv_context=[],
        indicators={},
        generated_features={"eligible_strategy_count": result.eligible_strategy_count},
        market_regime=decision_record.market_regime,
        volatility={"state": "unknown"},
        spread_liquidity_context=None,
        strategy_inputs={
            "roster_run_id": str(roster_run_id),
            "contributions": contribution_payload,
            "weighted_buy_score": str(result.weighted_buy_score),
            "weighted_sell_score": str(result.weighted_sell_score),
            "weighted_hold_score": str(result.weighted_hold_score),
        },
        risk_inputs={"position_state": position_state},
        current_position_state=position_state,
        open_trades=[],
        portfolio_exposure={},
        parameter_set_version="unknown",
        strategy_version=result.primary_strategy_version or "unknown",
        ai_model_version="none",
        decision_engine_version=DECISION_ENGINE_VERSION,
        configuration_version=f"strategy_decision_aggregator_{result.thresholds_applied.get('config_version', 'v1')}",
    )
    db.add(snapshot)
    await db.flush()

    aggregate_row = StrategyAggregateDecision(
        idempotency_key=idempotency_key,
        roster_run_id=roster_run_id,
        asset_id=asset_id,
        candle_close_time=candle_close_time,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        environment=environment,
        provider=provider,
        product_id=product_id,
        interval=interval,
        strategy_contributions=contribution_payload,
        eligible_strategy_count=result.eligible_strategy_count,
        weighted_buy_score=result.weighted_buy_score,
        weighted_sell_score=result.weighted_sell_score,
        weighted_hold_score=result.weighted_hold_score,
        position_state=position_state,
        thresholds_applied=result.thresholds_applied,
        final_action=result.final_action,
        primary_strategy_identity=result.primary_strategy_identity,
        primary_strategy_version=result.primary_strategy_version,
        dominant_contributor_identity=result.dominant_contributor_identity,
        explanation=result.explanation,
        deterministic_explanation=list(result.deterministic_explanation),
        decision_record_id=decision_record.decision_id,
    )
    db.add(aggregate_row)
    await db.flush()
    return aggregate_row, decision_record


async def resolve_and_persist_strategy_aggregate_evidence(
    *,
    db: AsyncSession,
    asset_id: UUID,
    product_id: str,
    interval: str,
    campaign_id: UUID,
    campaign_version: int,
    environment: str,
    paper_account_id: UUID,
    runtime_campaign_id: int,
    asset: Asset,
    candle_item: Candle,
    now: datetime,
    preferred_strategy_identity: str | None = None,
    required_trigger: str,
    scheduled_cycle_id: UUID | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Explicit transactional write API for governed aggregate evidence.

    No commit occurs here. The caller owns commit/rollback of the aggregate,
    DecisionRecord, DecisionSnapshot and all downstream composition writes.
    """
    # Historical contributor preference is continuity metadata only. It must
    # never filter or otherwise alter current ensemble membership.
    _ = preferred_strategy_identity
    return await resolve_or_create_strategy_aggregate_evidence(
        db=db,
        asset_id=asset_id,
        product_id=product_id,
        interval=interval,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        environment=environment,
        paper_account_id=paper_account_id,
        runtime_campaign_id=runtime_campaign_id,
        asset=asset,
        candle_item=candle_item,
        now=now,
        required_trigger=required_trigger,
        scheduled_cycle_id=scheduled_cycle_id,
    )


async def _load_position_evidence(
    *,
    db: AsyncSession,
    account_id: UUID | None,
    campaign_id: int,
    symbol: str,
    asset: Asset,
    candle: Candle,
    now: datetime,
) -> dict[str, Any]:
    if account_id is None:
        return {
            "authority_class": "UNAVAILABLE",
            "source_type": "campaign_account",
            "source_identity": {"paper_account_id": None},
            "observed_at": now.isoformat(),
            "freshness": "unavailable",
            "availability": "unavailable",
            "reason": "paper_account_unavailable",
            "position": None,
            "lifecycle": None,
            "profitability": None,
        }

    snapshots = await load_position_snapshots(db=db, account_id=account_id, campaign_id=campaign_id)
    snapshot = next((item for item in snapshots if _product_symbol(item.symbol) == _product_symbol(symbol)), None)
    if snapshot is None:
        return {
            "authority_class": "AUTHORITATIVE",
            "source_type": "position_lifecycle",
            "source_identity": {"paper_account_id": str(account_id), "campaign_id": campaign_id},
            "observed_at": now.isoformat(),
            "freshness": "fresh",
            "availability": "available",
            "reason": "no_open_position",
            "position": None,
            "lifecycle": None,
            "profitability": None,
        }

    policy = resolve_lifecycle_policy(asset_class=snapshot.asset_class, symbol=snapshot.symbol, venue=asset.exchange, now=now)
    if policy is None:
        return {
            "authority_class": "UNAVAILABLE",
            "source_type": "position_lifecycle",
            "source_identity": {"paper_account_id": str(account_id), "campaign_id": campaign_id, "symbol": snapshot.symbol},
            "observed_at": now.isoformat(),
            "freshness": "unavailable",
            "availability": "unavailable",
            "reason": "lifecycle_policy_unavailable",
            "position": None,
            "lifecycle": None,
            "profitability": None,
        }

    evaluation = evaluate_position_lifecycle(snapshot=snapshot, policy=policy, now=now)
    profitability = None
    if snapshot.position_size > Decimal("0") and snapshot.current_price is not None:
        max_hold_until = None
        if snapshot.opened_at is not None and policy.max_hold_minutes is not None:
            max_hold_until = snapshot.opened_at + timedelta(minutes=policy.max_hold_minutes)
        profitability = evaluate_exit_profitability(
            ProfitabilityInput(
                position_size=snapshot.position_size,
                entry_price=snapshot.entry_price,
                current_price=snapshot.current_price,
                accumulated_entry_and_carry_costs=snapshot.accumulated_entry_and_carry_costs,
                estimated_exit_fee_rate=policy.estimated_exit_fee_rate,
                estimated_slippage_rate=policy.estimated_slippage_rate,
                minimum_net_profit_to_exit=policy.minimum_net_profit_to_exit,
                stop_loss_price=policy.stop_loss_price,
                now=now,
                max_hold_until=max_hold_until,
            )
        )

    return {
        "authority_class": "AUTHORITATIVE" if not evaluation.market_data_stale else "STALE",
        "source_type": "position_lifecycle",
        "source_identity": {
            "paper_account_id": str(account_id),
            "campaign_id": campaign_id,
            "symbol": snapshot.symbol,
            "position_id": snapshot.position_id,
            "candle_id": None if snapshot.market_data_candle_id is None else str(snapshot.market_data_candle_id),
        },
        "observed_at": now.isoformat(),
        "freshness": "fresh" if not evaluation.market_data_stale else "stale",
        "availability": "available",
        "reason": evaluation.reason,
        "position": {
            "quantity": format(snapshot.position_size, "f"),
            "entry_price": format(snapshot.entry_price, "f"),
            "paid_costs": format(snapshot.accumulated_entry_and_carry_costs, "f"),
            "current_market_value": None if evaluation.current_market_value is None else format(evaluation.current_market_value, "f"),
            "break_even_price": None if evaluation.break_even_price is None else format(evaluation.break_even_price, "f"),
            "minimum_profitable_exit_price": None if evaluation.minimum_profitable_exit_price is None else format(evaluation.minimum_profitable_exit_price, "f"),
            "expected_net_pnl_if_sold_now": None if evaluation.expected_net_realized_pnl_if_sold_now is None else format(evaluation.expected_net_realized_pnl_if_sold_now, "f"),
            "lifecycle_state": evaluation.lifecycle_state,
            "lifecycle_recommendation": evaluation.recommendation,
            "stale_indicator": evaluation.stale_indicator,
            "dust_indicator": evaluation.dust_indicator,
            "closed_indicator": evaluation.closed_indicator,
            "market_data_source": snapshot.market_data_source,
            "market_data_timestamp": None if snapshot.market_data_timestamp is None else snapshot.market_data_timestamp.isoformat(),
            "market_data_age_minutes": snapshot.market_data_age_minutes,
            "market_data_interval": snapshot.market_data_interval,
            "market_data_candle_id": snapshot.market_data_candle_id,
        },
        "lifecycle": {
            "lifecycle_state": evaluation.lifecycle_state,
            "recommendation": evaluation.recommendation,
            "reason": evaluation.reason,
            "market_data_stale": evaluation.market_data_stale,
            "stale_indicator": evaluation.stale_indicator,
            "dust_indicator": evaluation.dust_indicator,
            "closed_indicator": evaluation.closed_indicator,
        },
        "profitability": None
        if profitability is None
        else {
            "entry_price": format(profitability.entry_price, "f"),
            "current_price": format(profitability.current_price, "f"),
            "current_market_value": format(profitability.current_market_value, "f"),
            "gross_pnl": format(profitability.gross_pnl, "f"),
            "paid_costs": format(profitability.paid_costs, "f"),
            "estimated_exit_fee": format(profitability.estimated_exit_fee, "f"),
            "estimated_slippage": format(profitability.estimated_slippage, "f"),
            "break_even_price": None if profitability.break_even_price is None else format(profitability.break_even_price, "f"),
            "minimum_profitable_exit_price": None if profitability.minimum_profitable_exit_price is None else format(profitability.minimum_profitable_exit_price, "f"),
            "expected_net_realized_pnl_if_sold_now": format(profitability.expected_net_realized_pnl_if_sold_now, "f"),
            "recommendation": profitability.recommendation,
            "reason": profitability.reason,
        },
    }


async def _load_market_evidence(
    *,
    db: AsyncSession,
    symbol: str,
    exchange: str,
    candle_interval: str,
    now: datetime,
) -> tuple[dict[str, Any], Asset | None, Candle | None]:
    base = _product_symbol(symbol)
    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.symbol == base)
            .where(Asset.asset_class == "crypto")
            .where(Asset.is_active.is_(True))
            .order_by(Asset.created_at.desc(), Asset.id.desc())
        )
    ).scalars().all()
    if not assets:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "asset_table",
                "source_identity": {"symbol": base, "exchange": exchange},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "asset_mapping_unavailable",
            },
            None,
            None,
        )

    matching_assets = [item for item in assets if _normalize_symbol(item.exchange) == _normalize_symbol(exchange) and str(item.base_currency or "").upper() in {"USD", "USDC", "USDT"}]
    if len(matching_assets) > 1:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "asset_table",
                "source_identity": {"symbol": base, "exchange": exchange},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "ambiguous_market_source",
            },
            None,
            None,
        )
    if not matching_assets:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "asset_table",
                "source_identity": {"symbol": base, "exchange": exchange},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "provider_product_unsupported",
            },
            None,
            None,
        )

    asset = matching_assets[0]
    candle = await _load_latest_closed_candle(db=db, asset_id=asset.id, interval=candle_interval, now=now)
    if candle is None:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "candle_table",
                "source_identity": {"asset_id": str(asset.id), "interval": candle_interval},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "market_data_unavailable",
            },
            asset,
            None,
        )

    close_time_utc = candle.close_time.astimezone(timezone.utc)
    freshness_seconds = int((now - close_time_utc).total_seconds())
    freshness_minutes = freshness_seconds // 60
    candle_interval_minutes = _interval_minutes(candle.interval)
    ingestion_grace_minutes = _INTERVAL_INGESTION_GRACE_MINUTES.get(str(candle.interval or "").strip().lower(), 0)
    if candle_interval_minutes is None:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "candle_table",
                "source_identity": {"asset_id": str(asset.id), "candle_id": candle.id, "interval": candle.interval},
                "observed_at": close_time_utc.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "stale_market_data",
                "asset_id": str(asset.id),
                "provider": asset.exchange,
                "product": symbol,
                "latest_closed_candle_id": candle.id,
                "interval": candle.interval,
                "close_price": format(Decimal(candle.close), "f"),
                "close_timestamp": close_time_utc.isoformat(),
                "evaluation_timestamp": now.isoformat(),
                "freshness_seconds": freshness_seconds,
                "freshness_minutes": freshness_minutes,
                "candle_interval_minutes": None,
                "ingestion_grace_minutes": None,
                "maximum_age_minutes": None,
                "freshness_verdict": "fail_closed_interval_unparseable",
            },
            asset,
            candle,
        )

    maximum_age_minutes = candle_interval_minutes + ingestion_grace_minutes
    if freshness_seconds < 0:
        return (
            {
                "authority_class": "STALE",
                "source_type": "candle_table",
                "source_identity": {"asset_id": str(asset.id), "candle_id": candle.id, "interval": candle.interval},
                "observed_at": close_time_utc.isoformat(),
                "freshness": "stale",
                "availability": "available",
                "reason": "stale_market_data",
                "asset_id": str(asset.id),
                "provider": asset.exchange,
                "product": symbol,
                "latest_closed_candle_id": candle.id,
                "interval": candle.interval,
                "close_price": format(Decimal(candle.close), "f"),
                "close_timestamp": close_time_utc.isoformat(),
                "evaluation_timestamp": now.isoformat(),
                "freshness_seconds": freshness_seconds,
                "freshness_minutes": freshness_minutes,
                "candle_interval_minutes": candle_interval_minutes,
                "ingestion_grace_minutes": ingestion_grace_minutes,
                "maximum_age_minutes": maximum_age_minutes,
                "freshness_verdict": "fail_closed_future_timestamp",
            },
            asset,
            candle,
        )

    if freshness_seconds > (maximum_age_minutes * 60):
        return (
            {
                "authority_class": "STALE",
                "source_type": "candle_table",
                "source_identity": {"asset_id": str(asset.id), "candle_id": candle.id, "interval": candle.interval},
                "observed_at": close_time_utc.isoformat(),
                "freshness": "stale",
                "availability": "available",
                "reason": "stale_market_data",
                "asset_id": str(asset.id),
                "provider": asset.exchange,
                "product": symbol,
                "latest_closed_candle_id": candle.id,
                "interval": candle.interval,
                "close_price": format(Decimal(candle.close), "f"),
                "close_timestamp": close_time_utc.isoformat(),
                "evaluation_timestamp": now.isoformat(),
                "freshness_seconds": freshness_seconds,
                "freshness_minutes": freshness_minutes,
                "candle_interval_minutes": candle_interval_minutes,
                "ingestion_grace_minutes": ingestion_grace_minutes,
                "maximum_age_minutes": maximum_age_minutes,
                "freshness_verdict": "stale",
            },
            asset,
            candle,
        )

    return (
        {
            "authority_class": "AUTHORITATIVE",
            "source_type": "candle_table",
            "source_identity": {"asset_id": str(asset.id), "candle_id": candle.id, "interval": candle.interval},
            "observed_at": candle.close_time.astimezone(timezone.utc).isoformat(),
            "freshness": "fresh",
            "availability": "available",
            "reason": "market data resolved from canonical asset and candle tables",
            "asset_id": str(asset.id),
            "provider": asset.exchange,
            "product": symbol,
            "latest_closed_candle_id": candle.id,
            "interval": candle.interval,
            "close_price": format(Decimal(candle.close), "f"),
            "close_timestamp": close_time_utc.isoformat(),
            "evaluation_timestamp": now.isoformat(),
            "freshness_seconds": freshness_seconds,
            "freshness_minutes": freshness_minutes,
            "candle_interval_minutes": candle_interval_minutes,
            "ingestion_grace_minutes": ingestion_grace_minutes,
            "maximum_age_minutes": maximum_age_minutes,
            "freshness_verdict": "fresh",
        },
        asset,
        candle,
    )


async def compose_campaign_authoritative_cycle(
    *,
    db: AsyncSession,
    campaign_definition: CapitalCampaignDefinitionResponse,
    trigger: str,
    candle: Candle,
) -> CampaignAuthoritativeCycleResult:
    now = datetime.now(timezone.utc)
    runtime_campaign = await _load_runtime_campaign(db=db, runtime_campaign_uuid=campaign_definition.runtime_campaign_uuid)
    if runtime_campaign is None or runtime_campaign.paper_account_id is None:
        composition = {
            "campaign_id": str(campaign_definition.campaign_id),
            "campaign_version": campaign_definition.version,
            "execution_mode": "preview",
            "execution_submitted": False,
            "provider_order_id": None,
            "failed_closed": True,
            "termination_stage": "failed_closed",
            "proposed_action": "FAILED_CLOSED",
            "failure_reason": "runtime_campaign_or_paper_account_unavailable",
            "selected_decision": {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": "runtime_campaign_or_paper_account_unavailable"},
            "eligible_candidates": [],
            "rejected_candidates": [],
            "ranked_candidates": [],
            "risk_outputs": [],
            "authoritative_evidence": {},
            "deterministic_explanation": ["runtime_campaign_or_paper_account_unavailable"],
            "candidate_instruments": list(campaign_definition.allowed_instruments),
            "decision_evidence": {},
        }
        return CampaignAuthoritativeCycleResult(composition=composition, preview=None)

    paper_account = await db.scalar(select(PaperAccount).where(PaperAccount.id == runtime_campaign.paper_account_id).limit(1))
    if paper_account is None:
        composition = {
            "campaign_id": str(campaign_definition.campaign_id),
            "campaign_version": campaign_definition.version,
            "execution_mode": "preview",
            "execution_submitted": False,
            "provider_order_id": None,
            "failed_closed": True,
            "termination_stage": "failed_closed",
            "proposed_action": "FAILED_CLOSED",
            "failure_reason": "paper_account_unavailable",
            "selected_decision": {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": "paper_account_unavailable"},
            "eligible_candidates": [],
            "rejected_candidates": [],
            "ranked_candidates": [],
            "risk_outputs": [],
            "authoritative_evidence": {},
            "deterministic_explanation": ["paper_account_unavailable"],
            "candidate_instruments": list(campaign_definition.allowed_instruments),
            "decision_evidence": {},
        }
        return CampaignAuthoritativeCycleResult(composition=composition, preview=None)

    strategy_authority = await _load_campaign_strategy_authority(
        db=db,
        campaign_id=campaign_definition.campaign_id,
        campaign_version=campaign_definition.version,
        metadata_evidence=dict(getattr(campaign_definition, "metadata_evidence", {}) or {}),
    )

    allowed_instruments = _scoped_instruments_for_trigger(
        allowed_instruments=list(campaign_definition.allowed_instruments),
        trigger=trigger,
    )
    market_evidence: dict[str, Any] = {}
    strategy_evidence: dict[str, Any] = {}
    position_evidence: dict[str, Any] = {}
    risk_outputs: dict[str, Any] = {}
    candidate_rows: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    for instrument in allowed_instruments:
        market, asset, candle_item = await _load_market_evidence(
            db=db,
            symbol=instrument,
            exchange=runtime_campaign.exchange or "kraken_spot",
            candle_interval=candle.interval,
            now=now,
        )
        market_evidence[instrument] = market
        if asset is None or candle_item is None or market.get("reason") in {"asset_mapping_unavailable", "provider_product_unsupported", "ambiguous_market_source", "market_data_unavailable", "stale_market_data"}:
            rejected_candidates.append({"instrument": instrument, "reason": market.get("reason", "market_data_unavailable"), "market": market})
            continue

        strategy, strategy_reason = await resolve_and_persist_strategy_aggregate_evidence(
            db=db,
            asset_id=asset.id,
            product_id=instrument,
            interval=candle.interval,
            campaign_id=campaign_definition.campaign_id,
            campaign_version=campaign_definition.version,
            environment="production",
            paper_account_id=runtime_campaign.paper_account_id,
            runtime_campaign_id=runtime_campaign.id,
            asset=asset,
            candle_item=candle_item,
            now=now,
            preferred_strategy_identity=strategy_authority.get("preferred_strategy_identity"),
            required_trigger=trigger,
            scheduled_cycle_id=getattr(candle, "scheduled_cycle_id", None),
        )
        if strategy is None:
            rejected_candidates.append({"instrument": instrument, "reason": strategy_reason or "strategy_evidence_unavailable", "market": market})
            strategy_evidence[instrument] = {"authority_class": "UNAVAILABLE", "reason": strategy_reason or "strategy_evidence_unavailable"}
            continue
        strategy_identity = str(strategy.get("strategy_identity") or "").strip()
        strategy_version = str(strategy.get("strategy_version") or "").strip()
        decision_record_id = str((strategy.get("source_identity") or {}).get("decision_record_id") or "").strip()
        if not _strategy_identity_is_coherent(strategy_identity=strategy_identity, strategy_version=strategy_version):
            rejected_candidates.append(
                {
                    "instrument": instrument,
                    "reason": "strategy_identity_incoherent",
                    "market": market,
                    "strategy": strategy,
                }
            )
            strategy_evidence[instrument] = {
                "authority_class": "UNAVAILABLE",
                "reason": "strategy_identity_incoherent",
                "strategy_identity": strategy_identity,
                "strategy_version": strategy_version,
            }
            continue
        historical_identity = str(strategy_authority.get("historical_strategy_identity") or "").strip()
        if historical_identity and historical_identity != strategy_identity:
            rejected_candidates.append(
                {
                    "instrument": instrument,
                    "reason": "strategy_continuity_conflict",
                    "market": market,
                    "strategy": strategy,
                    "historical_strategy_identity": historical_identity,
                    "strategy_identity": strategy_identity,
                }
            )
            strategy_evidence[instrument] = {
                "authority_class": "UNAVAILABLE",
                "reason": "strategy_continuity_conflict",
                "strategy_identity": strategy_identity,
                "historical_strategy_identity": historical_identity,
            }
            continue
        if not decision_record_id:
            rejected_candidates.append(
                {
                    "instrument": instrument,
                    "reason": "decision_record_linkage_missing",
                    "market": market,
                    "strategy": strategy,
                }
            )
            strategy_evidence[instrument] = {
                "authority_class": "UNAVAILABLE",
                "reason": "decision_record_linkage_missing",
                "strategy_identity": strategy_identity,
                "strategy_version": strategy_version,
            }
            continue
        action = str(strategy.get("action") or "").strip().upper()
        if action in {"HOLD", "NO_ACTION", "NONE"}:
            rejected_candidates.append(
                {
                    "instrument": instrument,
                    "reason": "strategy_hold_signal",
                    "market": market,
                    "strategy": strategy,
                    "decision_record_id": decision_record_id,
                    "strategy_identity": strategy_identity,
                    "strategy_version": strategy_version,
                }
            )
            strategy_evidence[instrument] = strategy
            continue
        strategy_evidence[instrument] = strategy

        position = await _load_position_evidence(
            db=db,
            account_id=runtime_campaign.paper_account_id,
            campaign_id=runtime_campaign.id,
            symbol=instrument,
            asset=asset,
            candle=candle_item,
            now=now,
        )
        position_evidence[instrument] = position

        position_row = position.get("position")
        position_open = bool(
            position_row is not None
            and position_row.get("closed_indicator") is False
            and position_row.get("quantity") not in {None, "0", "0.0"}
        )
        position_state = "UNKNOWN" if position.get("authority_class") == "UNAVAILABLE" else ("OPEN" if position_open else "FLAT")
        compounding_policy = getattr(campaign_definition, "compounding_policy", None)
        compounding_allowed = bool(
            "COMPOUND" in set(getattr(campaign_definition, "campaign_modes", []) or [])
            and compounding_policy is not None
            and Decimal(str(getattr(compounding_policy, "reinvestment_percentage", "0"))) > Decimal("0")
        )
        transition = resolve_action_position_transition(
            action=action,
            position_state=position_state,
            compounding_allowed=compounding_allowed,
        )
        if transition == "HOLD":
            rejected_candidates.append(
                {
                    "instrument": instrument,
                    "reason": "action_position_transition_hold",
                    "action": action,
                    "position_state": position_state,
                    "compounding_allowed": compounding_allowed,
                    "market": market,
                    "strategy": strategy,
                    "position": position,
                    "decision_record_id": decision_record_id,
                    "strategy_identity": strategy_identity,
                    "strategy_version": strategy_version,
                }
            )
            continue

        risk_result = None
        risk_reason = None
        risk_verdict = None
        approved_quantity = None
        if position.get("authority_class") == "UNAVAILABLE":
            risk_outputs[instrument] = {
                "authority_class": "UNAVAILABLE",
                "source_type": "risk_engine",
                "source_identity": None,
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "risk_unavailable",
            }
            rejected_candidates.append({"instrument": instrument, "reason": "risk_unavailable", "market": market, "strategy": strategy, "position": position})
            continue

        risk_context = await resolve_execution_risk_context(db=db, paper_account=paper_account, asset=asset)
        price = Decimal(str(candle_item.close))
        campaign_capital_budget = Decimal(str(getattr(campaign_definition, "capital_budget", campaign_definition.remaining_unallocated_capital)))
        requested_proving_amount = Decimal(str(getattr(campaign_definition, "minimum_position_size", Decimal("0"))))
        enforce_requested_proving_amount = requested_proving_amount == Decimal("5")
        paper_account_cash_balance = Decimal(str(getattr(paper_account, "current_cash_balance", getattr(paper_account, "starting_balance", "0"))))
        runtime_available_authority = getattr(runtime_campaign, "available_authority", None)
        if runtime_available_authority is None:
            runtime_available_authority = getattr(runtime_campaign, "available_capital", None)
        if runtime_available_authority is None and runtime_campaign.current_equity is not None:
            runtime_available_authority = Decimal(str(runtime_campaign.current_equity))
        runtime_available_authority_decimal = None
        if runtime_available_authority is not None:
            runtime_available_authority_decimal = Decimal(str(runtime_available_authority))
        minimum_viable_amount = max(
            Decimal(str(campaign_definition.minimum_position_size)),
            Decimal(str(asset.min_order_notional or "0")),
            requested_proving_amount,
        )
        proposed_allocation = None
        if transition == "CLOSE_CANDIDATE":
            side = "sell"
            quantity = Decimal(str(position["position"]["quantity"]))
        else:
            side = "buy"
            cap_terms = [campaign_definition.remaining_unallocated_capital, campaign_definition.maximum_position_size, campaign_definition.maximum_total_exposure, paper_account_cash_balance]
            if enforce_requested_proving_amount:
                cap_terms.append(requested_proving_amount)
            if runtime_available_authority_decimal is not None:
                cap_terms.append(runtime_available_authority_decimal)
            proposed_allocation = min(cap_terms)
            if proposed_allocation < minimum_viable_amount:
                rejected_candidates.append(
                    {
                        "instrument": instrument,
                        "reason": "position_below_minimum_order_size",
                        "market": market,
                        "strategy": strategy,
                        "position": position,
                        "strategy_identity": strategy_identity,
                        "strategy_version": strategy_version,
                        "decision_record_id": decision_record_id,
                        "sizing_trace": {
                            "campaign_capital_budget": format(campaign_capital_budget, "f"),
                            "campaign_remaining_unallocated_capital": format(campaign_definition.remaining_unallocated_capital, "f"),
                            "runtime_current_equity": format(Decimal(str(runtime_campaign.current_equity or "0")), "f"),
                            "runtime_available_authority": None
                            if runtime_available_authority_decimal is None
                            else format(runtime_available_authority_decimal, "f"),
                            "paper_account_cash": format(paper_account_cash_balance, "f"),
                            "risk_account_equity": format(Decimal(str(risk_context.account_equity)), "f"),
                            "requested_proving_amount": format(requested_proving_amount, "f"),
                            "liquid_cash_cap": format(paper_account_cash_balance, "f"),
                            "pre_risk_proposed_amount": format(proposed_allocation, "f"),
                            "minimum_position_size": format(campaign_definition.minimum_position_size, "f"),
                            "minimum_order_notional": format(Decimal(str(asset.min_order_notional or "0")), "f"),
                            "minimum_viable_amount": format(minimum_viable_amount, "f"),
                            "final_amount": "0",
                        },
                    }
                )
                continue
            quantity = proposed_allocation / price

        try:
            risk_result = evaluate_signal_risk(
                request=RiskEvaluationRequest(
                    signal_id=UUID(int=0),
                    paper_account_id=runtime_campaign.paper_account_id,
                    asset_id=asset.id,
                    side=side,
                    quantity=quantity,
                    account_equity=risk_context.account_equity,
                    max_position_size_pct=risk_context.max_position_size_pct,
                    min_order_notional=asset.min_order_notional,
                    qty_step_size=asset.qty_step_size,
                    supports_fractional=asset.supports_fractional,
                    start_of_day_equity=risk_context.start_of_day_equity,
                    current_equity=risk_context.current_equity,
                    max_daily_loss_pct=risk_context.max_daily_loss_pct,
                    high_water_mark_equity=risk_context.high_water_mark_equity,
                    max_drawdown_pct=risk_context.max_drawdown_pct,
                    consecutive_losses_on_pair=risk_context.consecutive_losses_on_pair,
                    cooldown_after_losses=risk_context.cooldown_after_losses,
                    last_loss_at=risk_context.last_loss_at,
                    cooldown_duration_minutes=risk_context.cooldown_duration_minutes,
                    evaluation_time=risk_context.evaluation_time,
                    data_is_stale=risk_context.data_is_stale,
                    data_has_gaps=risk_context.data_has_gaps,
                    global_kill_switch_engaged_state=risk_context.global_kill_switch_engaged_state,
                    global_kill_switch_rearm_required=risk_context.global_kill_switch_rearm_required,
                    account_kill_switch_engaged_state=risk_context.account_kill_switch_engaged_state,
                    account_kill_switch_rearm_required=risk_context.account_kill_switch_rearm_required,
                    global_kill_switch_state_observed=risk_context.global_kill_switch_state_observed,
                    account_kill_switch_state_observed=risk_context.account_kill_switch_state_observed,
                    actor="campaign_orchestration",
                ),
                reference_price=Decimal(str(candle_item.close)),
                context=RiskEvaluationContext(
                    global_kill_switch_engaged=bool(risk_context.global_kill_switch_engaged_state),
                    account_trading_paused=False,
                    asset_in_no_trade_zone=False,
                    pair_in_cooldown=False,
                    would_breach_daily_loss=False,
                    would_breach_drawdown=False,
                    has_computable_stop_loss=True,
                    bypass_sizing_rule=False,
                ),
            )
            risk_summary = {
                "authority_class": "AUTHORITATIVE",
                "source_type": "risk_engine",
                "source_identity": {"paper_account_id": str(runtime_campaign.paper_account_id), "asset_id": str(asset.id)},
                "observed_at": risk_context.evaluation_time.isoformat(),
                "freshness": "fresh",
                "availability": "available",
                "reason": risk_result.reason_code or risk_result.action.value,
                "verdict": "ALLOW" if risk_result.action == RiskDecisionAction.APPROVE else ("REDUCE" if risk_result.action == RiskDecisionAction.RESIZE else "VETO"),
                "approved_quantity": format(risk_result.approved_quantity, "f"),
                "risk_event_id": None,
                "policy_identity": risk_context.risk_policy_source,
                "policy_version": None,
                "evaluated_at": risk_context.evaluation_time.isoformat(),
                "sizing_trace": {
                    "campaign_capital_budget": format(campaign_capital_budget, "f"),
                    "campaign_remaining_unallocated_capital": format(campaign_definition.remaining_unallocated_capital, "f"),
                    "runtime_current_equity": format(Decimal(str(runtime_campaign.current_equity or "0")), "f"),
                    "runtime_available_authority": None
                    if runtime_available_authority_decimal is None
                    else format(runtime_available_authority_decimal, "f"),
                    "paper_account_cash": format(paper_account_cash_balance, "f"),
                    "risk_account_equity": format(Decimal(str(risk_context.account_equity)), "f"),
                    "requested_proving_amount": format(requested_proving_amount, "f"),
                    "campaign_allocation": None if proposed_allocation is None else format(proposed_allocation, "f"),
                    "liquid_cash_cap": format(paper_account_cash_balance, "f"),
                    "position_size_percentage": format(Decimal(str(risk_context.max_position_size_pct)), "f"),
                    "pre_risk_proposed_amount": format(quantity * price, "f"),
                    "risk_resized_amount": format(risk_result.approved_quantity * price, "f"),
                    "minimum_viable_amount": format(Decimal(str(asset.min_order_notional or "0")), "f"),
                    "final_amount": format(risk_result.approved_quantity * price, "f"),
                },
            }
            persist_result = await persist_risk_decision(
                db=db,
                request=RiskDecisionPersistenceRequest(
                    paper_account_id=runtime_campaign.paper_account_id,
                    signal_id=None,
                    actor="campaign_orchestration",
                    evaluation_result=risk_result,
                ),
            )
            risk_summary["risk_event_id"] = str(persist_result.risk_event_id)
            if risk_result.action == RiskDecisionAction.REJECT:
                risk_summary["reason"] = risk_result.reason_code or "risk_rejected"
        except Exception as exc:
            risk_summary = {
                "authority_class": "UNAVAILABLE",
                "source_type": "risk_engine",
                "source_identity": {"paper_account_id": str(runtime_campaign.paper_account_id), "asset_id": str(asset.id)},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": f"risk_unavailable:{exc.__class__.__name__}",
                "verdict": "VETO",
                "approved_quantity": "0",
                "risk_event_id": None,
                "policy_identity": risk_context.risk_policy_source,
                "policy_version": None,
                "evaluated_at": now.isoformat(),
                "sizing_trace": {
                    "campaign_capital_budget": format(campaign_capital_budget, "f"),
                    "campaign_remaining_unallocated_capital": format(campaign_definition.remaining_unallocated_capital, "f"),
                    "runtime_current_equity": format(Decimal(str(runtime_campaign.current_equity or "0")), "f"),
                    "runtime_available_authority": None
                    if runtime_available_authority_decimal is None
                    else format(runtime_available_authority_decimal, "f"),
                    "paper_account_cash": format(paper_account_cash_balance, "f"),
                    "risk_account_equity": format(Decimal(str(risk_context.account_equity)), "f"),
                    "requested_proving_amount": format(requested_proving_amount, "f"),
                    "campaign_allocation": None if proposed_allocation is None else format(proposed_allocation, "f"),
                    "liquid_cash_cap": format(paper_account_cash_balance, "f"),
                    "position_size_percentage": format(Decimal(str(risk_context.max_position_size_pct)), "f"),
                    "pre_risk_proposed_amount": format(quantity * price, "f"),
                    "risk_resized_amount": "0",
                    "minimum_viable_amount": format(Decimal(str(asset.min_order_notional or "0")), "f"),
                    "final_amount": "0",
                },
            }
            rejected_candidates.append({"instrument": instrument, "reason": "risk_unavailable", "market": market, "strategy": strategy, "position": position, "risk": risk_summary})
            risk_outputs[instrument] = risk_summary
            continue
        risk_outputs[instrument] = risk_summary

        expected_gross_edge = strategy.get("profitable_after_fees_performance")
        if expected_gross_edge is None and strategy.get("expected_value") is not None:
            expected_gross_edge = strategy.get("expected_value")
        expected_gross_edge_decimal = Decimal(str(expected_gross_edge or "0"))
        expected_fees = Decimal(str(candle_item.close)) * Decimal("0.0001")
        expected_slippage = Decimal(str(candle_item.close)) * Decimal("0.0001")
        expected_net_edge = expected_gross_edge_decimal - expected_fees - expected_slippage
        expected_net_dollars = expected_net_edge * Decimal(str(candle_item.close)) / Decimal("100")
        if position["position"] is not None and position["position"].get("profitability") is not None:
            expected_net_dollars = Decimal(str(position["position"]["expected_net_pnl_if_sold_now"] or "0"))
            current_market_value = Decimal(str(position["position"]["current_market_value"] or "0"))
            if current_market_value > 0:
                expected_net_edge = (expected_net_dollars / current_market_value) * Decimal("100")

        if risk_summary["verdict"] == "VETO":
            rejected_candidates.append({"instrument": instrument, "reason": risk_summary["reason"], "market": market, "strategy": strategy, "position": position, "risk": risk_summary})
            continue

        candidate_kind = "ADD_POSITION_PROPOSED" if transition == "ADD_CANDIDATE" else "OPEN_POSITION_PROPOSED"
        if transition == "CLOSE_CANDIDATE":
            candidate_kind = "CLOSE_POSITION_PROPOSED" if expected_net_dollars > Decimal("0") else "HOLD_POSITION"
        elif expected_net_dollars <= Decimal("0"):
            rejected_candidates.append({"instrument": instrument, "reason": "non_positive_net_edge", "market": market, "strategy": strategy, "position": position, "risk": risk_summary})
            continue

        candidate_rows.append(
            {
                "instrument": instrument,
                "decision_kind": candidate_kind,
                "expected_net_dollars": format(expected_net_dollars, "f"),
                "expected_net_edge_pct": format(expected_net_edge, "f"),
                "risk_adjusted_score": format(expected_net_dollars * (Decimal(str(strategy.get("confidence") or "1")) if strategy.get("confidence") is not None else Decimal("1")), "f"),
                "confidence": strategy.get("confidence"),
                "sample_size": strategy.get("sample_size"),
                "strategy_identity": strategy_identity,
                "strategy_version": strategy_version,
                "decision_record_id": decision_record_id,
                "expected_fees": format(expected_fees, "f"),
                "expected_slippage": format(expected_slippage, "f"),
                "proposed_allocation": format(
                    proposed_allocation if proposed_allocation is not None else Decimal("0"),
                    "f",
                ),
                "maximum_risk_approved_allocation": risk_summary.get("approved_quantity"),
                "campaign_constraint_result": "pass",
                "rank": None,
                "rejection_reasons": [],
                "market_evidence": market,
                "strategy_evidence": strategy,
                "position_evidence": position,
                "risk_evidence": risk_summary,
            }
        )

    candidate_rows.sort(key=lambda item: (Decimal(str(item["expected_net_dollars"])), Decimal(str(item["risk_adjusted_score"])), item["instrument"]), reverse=True)
    for index, item in enumerate(candidate_rows, start=1):
        item["rank"] = index

    selected = candidate_rows[0] if candidate_rows else None
    critical_rejections = {
        "risk_unavailable",
        "strategy_evidence_unavailable",
        "strategy_identity_incoherent",
        "strategy_continuity_conflict",
        "decision_record_linkage_missing",
        "market_data_unavailable",
        "stale_market_data",
        "asset_mapping_unavailable",
        "provider_product_unsupported",
        "ambiguous_market_source",
    }
    failed_closed = bool(rejected_candidates) and not candidate_rows and any(item.get("reason") in critical_rejections for item in rejected_candidates)
    if selected is None:
        if failed_closed:
            first_reason = _primary_rejection_reason(rejected_candidates=rejected_candidates, failed_closed=True)
            selected_decision = {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": first_reason}
        else:
            hold_reason = _primary_rejection_reason(rejected_candidates=rejected_candidates, failed_closed=False)
            hold_lineage = next((item for item in rejected_candidates if item.get("decision_record_id")), None)
            selected_decision = {
                "decision_kind": "HOLD",
                "reason": hold_reason,
                "decision_record_id": None if hold_lineage is None else hold_lineage.get("decision_record_id"),
                "strategy_identity": None if hold_lineage is None else hold_lineage.get("strategy_identity"),
                "strategy_version": None if hold_lineage is None else hold_lineage.get("strategy_version"),
                "sizing_trace": None if hold_lineage is None else hold_lineage.get("sizing_trace"),
            }
    else:
        selected_decision = {
            "decision_kind": selected["decision_kind"],
            "instrument": selected["instrument"],
            "decision_record_id": selected.get("decision_record_id"),
            "strategy_identity": selected.get("strategy_identity"),
            "strategy_version": selected.get("strategy_version"),
            "why_this_asset": f"best risk-adjusted net economics among authoritative candidates: {selected['expected_net_dollars']}",
            "why_not_other_assets": [item["instrument"] for item in candidate_rows[1:]],
            "why_not_cash": "selected candidate exceeds cash baseline" if Decimal(str(selected["expected_net_dollars"])) > Decimal("0") else "cash baseline preferred",
            "costs_included": {
                "expected_fees": selected.get("expected_fees"),
                "expected_slippage": selected.get("expected_slippage"),
            },
            "risk_verdict": selected["risk_evidence"]["verdict"],
            "evidence_freshness": selected["market_evidence"]["freshness"],
            "missing_evidence": [item["reason"] for item in rejected_candidates],
            "campaign_constraints": {
                    "maximum_open_positions": getattr(campaign_definition, "maximum_open_positions", len(candidate_rows)),
                "maximum_position_size": format(campaign_definition.maximum_position_size, "f"),
                "maximum_total_exposure": format(campaign_definition.maximum_total_exposure, "f"),
                "remaining_unallocated_capital": format(campaign_definition.remaining_unallocated_capital, "f"),
            },
            "sizing_trace": selected["risk_evidence"].get("sizing_trace"),
        }

    composition = {
        "campaign_id": str(campaign_definition.campaign_id),
        "campaign_version": campaign_definition.version,
        "execution_mode": "preview",
        "execution_submitted": False,
        "provider_order_id": None,
        "decision_record_id": selected_decision.get("decision_record_id"),
        "failed_closed": failed_closed,
        "termination_stage": "failed_closed" if failed_closed else ("preview_generated" if selected is not None else "hold_no_package_created"),
        "proposed_action": "FAILED_CLOSED" if failed_closed else (selected["decision_kind"] if selected is not None else "HOLD"),
        "failure_reason": None if selected is not None and not failed_closed else (selected_decision.get("reason") if selected is None else None),
        "selected_decision": selected_decision,
        "eligible_candidates": candidate_rows,
        "rejected_candidates": rejected_candidates,
        "ranked_candidates": candidate_rows,
        "risk_outputs": risk_outputs,
        "authoritative_evidence": {
            "market": market_evidence,
            "strategy": strategy_evidence,
            "position": position_evidence,
            "risk": risk_outputs,
            "authority_class": "AUTHORITATIVE",
            "strategy_authority": strategy_authority,
        },
        "deterministic_explanation": [
            f"trigger={trigger}",
            f"campaign_version={campaign_definition.version}",
            f"scoped_instruments={','.join(allowed_instruments)}",
            f"strategy_authority_source={strategy_authority.get('authority_source')}",
            f"candidates={len(candidate_rows)}",
            f"rejected={len(rejected_candidates)}",
        ],
        "decision_evidence": selected_decision,
        "candidate_instruments": allowed_instruments,
    }
    preview_strategy_inputs = _preview_strategy_inputs_from_authoritative_evidence(
        strategy_evidence=strategy_evidence,
        allowed_instruments=allowed_instruments,
    )
    preview_lifecycle_inputs = _preview_lifecycle_inputs_from_authoritative_evidence(
        position_evidence=position_evidence,
        allowed_instruments=allowed_instruments,
    )
    preview_risk_inputs = _preview_risk_inputs_from_authoritative_evidence(
        risk_outputs=risk_outputs,
        allowed_instruments=allowed_instruments,
    )

    preview = build_campaign_preview(
        campaign=campaign_definition,
        request=CapitalCampaignPreviewRequest(
            candidate_instruments=allowed_instruments,
            strategy_evidence=preview_strategy_inputs,
            lifecycle_snapshots=preview_lifecycle_inputs,
            risk_preview=preview_risk_inputs,
        ),
        now=now,
    )
    composition["preview"] = preview.model_dump(mode="json")
    return CampaignAuthoritativeCycleResult(composition=composition, preview=preview)
