from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.live_trading_profile import LiveTradingProfile
from app.models.parameter_set import ParameterSet
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.models.strategy import Strategy


@dataclass(frozen=True, slots=True)
class CanonicalCampaignAuthorityAuditRequest:
    campaign_id: UUID
    campaign_version: int
    cycle_id: UUID
    paper_account_id: UUID
    live_trading_profile_id: UUID
    provider: str
    environment: str
    product: str


def _normalize_environment(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"production", "sandbox"}:
        raise ValueError(f"unsupported exchange environment: {value}")
    return normalized


def _exchange_label(provider: str, environment: str) -> str:
    normalized_provider = provider.strip().lower()
    return normalized_provider if environment == "production" else f"{normalized_provider}_sandbox"


def _decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _serialize_package_row(package: CanonicalPreviewPackage) -> dict[str, Any]:
    return {
        "package_id": str(package.package_id),
        "state": package.package_state,
        "strategy_id": str(package.strategy_id),
        "strategy_version": package.strategy_version,
        "parameter_set_id": str(package.parameter_set_id),
        "parameter_set_version": package.parameter_set_version,
        "decision_record_id": str(package.decision_record_id),
        "risk_event_id": str(package.risk_event_id),
        "crypto_order_preview_id": str(package.crypto_order_preview_id),
        "generated_at": _timestamp(package.generated_at),
        "created_at": _timestamp(package.created_at),
        "updated_at": _timestamp(package.updated_at),
    }


def _serialize_signal(signal: Signal) -> dict[str, Any]:
    return {
        "signal_id": str(signal.id),
        "strategy_id": str(signal.strategy_id),
        "parameter_set_id": str(signal.parameter_set_id),
        "asset_id": str(signal.asset_id),
        "signal_time": _timestamp(signal.signal_time),
        "action": signal.action,
        "status": signal.status,
        "raw_strength": _decimal(signal.raw_strength),
        "ai_confidence": _decimal(signal.ai_confidence),
        "regime_tag": signal.regime_tag,
        "created_at": _timestamp(signal.created_at),
    }


async def run_canonical_campaign_authority_audit(
    *,
    db: AsyncSession,
    request: CanonicalCampaignAuthorityAuditRequest,
) -> dict[str, Any]:
    environment = _normalize_environment(request.environment)
    provider = request.provider.strip().lower()
    exchange = _exchange_label(provider, environment)
    product = request.product.strip().upper().replace("/", "-")
    product_symbol = product.split("-", 1)[0]

    definition = await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == request.campaign_id)
        .where(CapitalCampaignDefinition.version == request.campaign_version)
        .limit(1)
    )
    runtime = await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == request.campaign_id)
        .limit(1)
    )
    paper_account = await db.get(PaperAccount, request.paper_account_id)
    live_profile = await db.get(LiveTradingProfile, request.live_trading_profile_id)
    asset = await db.scalar(
        select(Asset)
        .where(Asset.symbol == product_symbol)
        .where(Asset.exchange == exchange)
        .order_by(desc(Asset.created_at), desc(Asset.id))
        .limit(1)
    )
    strategy = None
    if runtime is not None and runtime.strategy_id is not None:
        strategy = await db.get(Strategy, runtime.strategy_id)

    parameter_sets: list[ParameterSet] = []
    if strategy is not None:
        parameter_sets = list(
            (
                await db.execute(
                    select(ParameterSet)
                    .where(ParameterSet.strategy_id == strategy.id)
                    .order_by(desc(ParameterSet.created_at), desc(ParameterSet.id))
                )
            )
            .scalars()
            .all()
        )

    packages = list(
        (
            await db.execute(
                select(CanonicalPreviewPackage)
                .where(CanonicalPreviewPackage.campaign_id == request.campaign_id)
                .where(CanonicalPreviewPackage.campaign_version == request.campaign_version)
                .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id))
            )
        )
        .scalars()
        .all()
    )

    cycle = await db.get(AutonomousCycleRun, request.cycle_id)
    if cycle is None:
        raise LookupError(f"campaign cycle not found: {request.cycle_id}")

    decision = await db.get(DecisionRecord, cycle.decision_record_id) if cycle.decision_record_id is not None else None
    decision_snapshot = await db.get(DecisionSnapshot, cycle.decision_record_id) if cycle.decision_record_id is not None else None
    risk_event = await db.get(RiskEvent, cycle.risk_event_id) if cycle.risk_event_id is not None else None
    preview = await db.get(CryptoOrderPreview, cycle.preview_id) if cycle.preview_id is not None else None

    linked_package = None
    linkage_filters = []
    if cycle.decision_record_id is not None:
        linkage_filters.append(CanonicalPreviewPackage.decision_record_id == cycle.decision_record_id)
    if cycle.risk_event_id is not None:
        linkage_filters.append(CanonicalPreviewPackage.risk_event_id == cycle.risk_event_id)
    if cycle.preview_id is not None:
        linkage_filters.append(CanonicalPreviewPackage.crypto_order_preview_id == cycle.preview_id)
    if linkage_filters:
        linked_package = await db.scalar(
            select(CanonicalPreviewPackage)
            .where(CanonicalPreviewPackage.campaign_id == request.campaign_id)
            .where(CanonicalPreviewPackage.campaign_version == request.campaign_version)
            .where(or_(*linkage_filters))
            .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id))
            .limit(1)
        )

    source_lineage = _safe_dict(decision.source_lineage if decision is not None else None)
    signal_ids = []
    for raw in _safe_list(source_lineage.get("signals")):
        try:
            signal_ids.append(UUID(str(raw)))
        except (ValueError, TypeError, AttributeError):
            continue

    signal_rows: list[Signal] = []
    if signal_ids:
        signal_rows = list(
            (
                await db.execute(
                    select(Signal)
                    .where(Signal.id.in_(signal_ids))
                    .order_by(desc(Signal.signal_time), desc(Signal.id))
                )
            )
            .scalars()
            .all()
        )

    metadata_evidence = _safe_dict(definition.metadata_evidence if definition is not None else None)
    cycle_context = _safe_dict(cycle.cycle_context)
    composition = _safe_dict(cycle_context.get("authoritative_composition"))
    authoritative = _safe_dict(composition.get("authoritative_evidence"))
    strategy_evidence_by_instrument = _safe_dict(authoritative.get("strategy"))

    package_rows = [_serialize_package_row(item) for item in packages]
    historical_strategy_authority = [
        {
            "authority_source": "canonical_preview_package_history",
            "package_id": item["package_id"],
            "strategy_id": item["strategy_id"],
            "strategy_version": item["strategy_version"],
            "parameter_set_id": item["parameter_set_id"],
            "parameter_set_version": item["parameter_set_version"],
            "created_at": item["created_at"],
            "generated_at": item["generated_at"],
        }
        for item in package_rows
    ]

    return {
        "command": "canonical-campaign-authority-audit",
        "inputs": {
            "campaign_id": str(request.campaign_id),
            "campaign_version": request.campaign_version,
            "cycle_id": str(request.cycle_id),
            "paper_account_id": str(request.paper_account_id),
            "live_trading_profile_id": str(request.live_trading_profile_id),
            "provider": provider,
            "environment": environment,
            "product": product,
        },
        "identity_linkage": {
            "cycle_matches_campaign_id": cycle.capital_campaign_id == request.campaign_id,
            "cycle_matches_campaign_version": cycle.capital_campaign_version == request.campaign_version,
            "runtime_matches_definition_campaign_id": (
                None
                if runtime is None
                else runtime.definition_campaign_id == request.campaign_id
            ),
            "runtime_matches_definition_version": (
                None
                if runtime is None
                else runtime.definition_version == request.campaign_version
            ),
            "runtime_matches_requested_paper_account": (
                None
                if runtime is None
                else runtime.paper_account_id == request.paper_account_id
            ),
            "runtime_exchange": None if runtime is None else runtime.exchange,
            "expected_exchange": exchange,
        },
        "campaign_definition": None
        if definition is None
        else {
            "campaign_id": str(definition.campaign_id),
            "version": definition.version,
            "status": definition.status,
            "allowed_asset_classes": list(definition.allowed_asset_classes or []),
            "allowed_venues": list(definition.allowed_venues or []),
            "allowed_instruments": list(definition.allowed_instruments or []),
            "campaign_modes": list(definition.campaign_modes or []),
            "metadata_evidence": metadata_evidence,
            "risk_policy": {
                "id": definition.risk_policy_id,
                "version": definition.risk_policy_version,
            },
            "profitability_policy": {
                "id": definition.profitability_policy_id,
                "version": definition.profitability_policy_version,
            },
            "capital_budget": _decimal(definition.capital_budget),
            "minimum_position_size": _decimal(definition.minimum_position_size),
            "maximum_position_size": _decimal(definition.maximum_position_size),
            "maximum_total_exposure": _decimal(definition.maximum_total_exposure),
            "remaining_unallocated_capital": _decimal(definition.remaining_unallocated_capital),
        },
        "runtime_campaign": None
        if runtime is None
        else {
            "runtime_id": runtime.id,
            "runtime_uuid": str(runtime.uuid),
            "definition_campaign_id": None if runtime.definition_campaign_id is None else str(runtime.definition_campaign_id),
            "definition_version": runtime.definition_version,
            "status": runtime.status,
            "paper_account_id": None if runtime.paper_account_id is None else str(runtime.paper_account_id),
            "exchange": runtime.exchange,
            "provider": provider,
            "strategy_id": None if runtime.strategy_id is None else str(runtime.strategy_id),
            "starting_capital": _decimal(runtime.starting_capital),
            "current_equity": _decimal(runtime.current_equity),
            "realized_profit": _decimal(runtime.realized_profit),
            "fees": _decimal(runtime.fees),
            "created_at": _timestamp(runtime.created_at),
            "updated_at": _timestamp(runtime.updated_at),
        },
        "paper_account": None
        if paper_account is None
        else {
            "paper_account_id": str(paper_account.id),
            "asset_class": paper_account.asset_class,
            "is_active": bool(paper_account.is_active),
            "starting_balance": _decimal(paper_account.starting_balance),
            "current_cash_balance": _decimal(paper_account.current_cash_balance),
            "created_at": _timestamp(paper_account.created_at),
        },
        "live_trading_profile": None
        if live_profile is None
        else {
            "live_trading_profile_id": str(live_profile.id),
            "paper_account_id": str(live_profile.paper_account_id),
            "operating_mode": live_profile.operating_mode,
            "lifecycle_state": live_profile.lifecycle_state,
            "approval_state": live_profile.approval_state,
            "provenance_metadata": _safe_dict(live_profile.provenance_metadata),
            "created_at": _timestamp(live_profile.created_at),
            "updated_at": _timestamp(live_profile.updated_at),
        },
        "asset_mapping": None
        if asset is None
        else {
            "asset_id": str(asset.id),
            "symbol": asset.symbol,
            "base_currency": asset.base_currency,
            "exchange": asset.exchange,
            "provider": provider,
            "active": bool(asset.is_active),
            "supports_fractional": bool(asset.supports_fractional),
            "minimum_order_notional": _decimal(asset.min_order_notional),
            "quantity_step": _decimal(asset.qty_step_size),
        },
        "strategy_authority": {
            "campaign_definition_metadata": {
                "canonical_strategy_identity": metadata_evidence.get("canonical_strategy_identity"),
                "selected_strategy_identity": metadata_evidence.get("selected_strategy_identity"),
                "strategy_identity": metadata_evidence.get("strategy_identity"),
                "strategy": metadata_evidence.get("strategy"),
            },
            "runtime_campaign_strategy_id": None if runtime is None or runtime.strategy_id is None else str(runtime.strategy_id),
            "linked_strategy": None
            if strategy is None
            else {
                "strategy_id": str(strategy.id),
                "slug": strategy.slug,
                "name": strategy.name,
                "module_version": strategy.module_version,
                "is_active": bool(strategy.is_active),
                "created_at": _timestamp(strategy.created_at),
            },
            "linked_parameter_sets": [
                {
                    "parameter_set_id": str(item.id),
                    "strategy_id": str(item.strategy_id),
                    "label": item.label,
                    "created_by": item.created_by,
                    "created_at": _timestamp(item.created_at),
                }
                for item in parameter_sets
            ],
            "current_cycle_strategy_evidence": {
                "strategy_authority": _safe_dict(authoritative.get("strategy_authority")),
                "strategy_by_instrument": strategy_evidence_by_instrument,
                "selected_decision": _safe_dict(composition.get("selected_decision")),
            },
            "historical_canonical_package_evidence": historical_strategy_authority,
            "historical_continuity_only": True,
        },
        "canonical_packages": {
            "count": len(package_rows),
            "items": package_rows,
        },
        "target_cycle": {
            "cycle_id": str(cycle.cycle_id),
            "state": cycle.state,
            "evaluation_stage": cycle.evaluation_stage,
            "termination_stage": cycle.termination_stage,
            "primary_failure_reason": cycle.failure_reason,
            "proposed_action": cycle.proposed_action,
            "decision_record_id": None if cycle.decision_record_id is None else str(cycle.decision_record_id),
            "risk_event_id": None if cycle.risk_event_id is None else str(cycle.risk_event_id),
            "preview_id": None if cycle.preview_id is None else str(cycle.preview_id),
            "cycle_context": cycle_context,
            "diagnostics": _safe_dict(cycle.diagnostics),
            "deterministic_explanation": list(cycle.deterministic_explanation or []),
            "started_at": _timestamp(cycle.started_at),
            "completed_at": _timestamp(cycle.completed_at),
        },
        "cycle_linked_evidence": {
            "decision_record": None
            if decision is None
            else {
                "decision_id": str(decision.decision_id),
                "timestamp": _timestamp(decision.timestamp),
                "timeframe": decision.timeframe,
                "trade_accepted": bool(decision.trade_accepted),
                "trade_rejected_reason": decision.trade_rejected_reason,
                "asset": _safe_dict(decision.asset),
                "source_lineage": source_lineage,
            },
            "decision_snapshot": None
            if decision_snapshot is None
            else {
                "decision_id": str(decision_snapshot.decision_id),
                "strategy_version": decision_snapshot.strategy_version,
                "parameter_set_version": decision_snapshot.parameter_set_version,
                "timestamp": _timestamp(decision_snapshot.timestamp),
            },
            "risk_event": None
            if risk_event is None
            else {
                "risk_event_id": str(risk_event.id),
                "paper_account_id": None if risk_event.paper_account_id is None else str(risk_event.paper_account_id),
                "related_signal_id": None if risk_event.related_signal_id is None else str(risk_event.related_signal_id),
                "event_type": risk_event.event_type,
                "action_taken": risk_event.action_taken,
                "detail": _safe_dict(risk_event.detail),
                "created_at": _timestamp(risk_event.created_at),
            },
            "crypto_order_preview": None
            if preview is None
            else {
                "crypto_order_preview_id": str(preview.crypto_order_preview_id),
                "provider": preview.provider,
                "environment": preview.environment,
                "product_id": preview.product_id,
                "side": preview.side,
                "status": preview.status,
                "readiness_verdict": preview.readiness_verdict,
                "decision_record_id": None if preview.decision_record_id is None else str(preview.decision_record_id),
                "risk_event_id": None if preview.risk_event_id is None else str(preview.risk_event_id),
                "strategy_id": None if preview.strategy_id is None else str(preview.strategy_id),
                "parameter_set_id": None if preview.parameter_set_id is None else str(preview.parameter_set_id),
                "requested_amount": _decimal(preview.requested_amount),
                "estimated_fee": _decimal(preview.estimated_fee),
                "created_at": _timestamp(preview.created_at),
            },
            "canonical_package": None if linked_package is None else _serialize_package_row(linked_package),
            "signals": [_serialize_signal(item) for item in signal_rows],
            "candidate_evidence": {
                "eligible_candidates": list(_safe_list(composition.get("eligible_candidates"))),
                "rejected_candidates": list(_safe_list(composition.get("rejected_candidates"))),
                "ranked_candidates": list(_safe_list(composition.get("ranked_candidates"))),
            },
        },
    }