from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.candle import Candle
from app.schemas.capital_campaign_domain import CampaignCompoundingPolicy, CapitalCampaignPreviewRequest
from app.services.capital_campaign_domain.repository import CapitalCampaignDomainRepository
from app.services.capital_campaign_domain.service import get_campaign_definition, list_campaign_definitions, preview_campaign_definition
from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle


_CAMPAIGN_CYCLE_KIND = "campaign"
_READINESS_ELIGIBLE_STATUSES = {"DRAFT", "READY", "ACTIVE", "PAUSED"}
_SUPPORTED_TRIGGER_PROVIDER = "kraken_spot"
_SUPPORTED_TRIGGER_PRODUCT = "BTC-USD"
_SUPPORTED_TRIGGER_INTERVAL = "15m"
_COMMISSIONED_BLOB_KEY = "commissioned_seed_campaign"
_LEGACY_COMMISSIONED_METADATA_KEYS = {
    "state",
    "authority_metadata",
    "evidence_metadata",
    "transition_history",
    "seen_idempotency_keys",
    "commissioning",
    "entry_execution",
    "ownership_reconciliation",
    "exit_recommendation",
    "operator_control",
}


def _assess_compounding_policy(raw_policy: Any) -> dict[str, Any]:
    if not isinstance(raw_policy, dict):
        return {
            "valid": False,
            "status": "invalid_payload",
            "policy_type": None,
            "blockers": ["compounding_policy_invalid_payload"],
            "warnings": [],
        }

    if not str(raw_policy.get("policy_type") or "").strip():
        return {
            "valid": False,
            "status": "legacy_missing_policy_type",
            "policy_type": None,
            "blockers": ["compounding_policy_missing_policy_type"],
            "warnings": ["legacy_compounding_policy_payload"],
        }

    try:
        policy = CampaignCompoundingPolicy.model_validate(raw_policy)
    except Exception:
        return {
            "valid": False,
            "status": "invalid_payload",
            "policy_type": None,
            "blockers": ["compounding_policy_invalid_payload"],
            "warnings": [],
        }

    return {
        "valid": True,
        "status": "valid",
        "policy_type": policy.policy_type,
        "blockers": [],
        "warnings": [],
    }


def _commissioned_metadata_shape(metadata: Any) -> tuple[bool, str | None]:
    if not isinstance(metadata, dict):
        return False, None
    nested = metadata.get(_COMMISSIONED_BLOB_KEY)
    if isinstance(nested, dict):
        return True, "nested_commissioned_seed_campaign"
    if any(key in metadata for key in _LEGACY_COMMISSIONED_METADATA_KEYS):
        return True, "legacy_top_level_commissioned_fields"
    return False, None


async def _load_runtime_for_definition(*, db: AsyncSession, campaign_id: UUID) -> CapitalCampaign | None:
    return await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == campaign_id)
        .order_by(desc(CapitalCampaign.updated_at), desc(CapitalCampaign.id))
        .limit(1)
    )


async def _load_btc_asset_for_provider(*, db: AsyncSession) -> Asset | None:
    asset = await db.scalar(
        select(Asset)
        .where(Asset.exchange == _SUPPORTED_TRIGGER_PROVIDER)
        .where(Asset.symbol.in_(["BTC", "XBT", "XXBT"]))
        .order_by(desc(Asset.created_at), desc(Asset.id))
        .limit(1)
    )
    return asset


def _build_campaign_snapshot(*, definition: CapitalCampaignDefinition, runtime: CapitalCampaign | None, asset: Asset | None, policy_assessment: dict[str, Any]) -> dict[str, Any]:
    commissioned_metadata_present, commissioned_metadata_shape = _commissioned_metadata_shape(definition.metadata_evidence)
    return {
        "campaign_id": str(definition.campaign_id),
        "version": definition.version,
        "status": definition.status,
        "owner_identity": definition.owner_identity,
        "exchange": None if runtime is None else runtime.exchange,
        "paper_account_id": None if runtime is None or runtime.paper_account_id is None else str(runtime.paper_account_id),
        "runtime_campaign_id": None if runtime is None else runtime.id,
        "capital_budget": format(Decimal(str(definition.capital_budget)), "f"),
        "remaining_unallocated_capital": format(Decimal(str(definition.remaining_unallocated_capital)), "f"),
        "maximum_position_size": format(Decimal(str(definition.maximum_position_size)), "f"),
        "maximum_total_exposure": format(Decimal(str(definition.maximum_total_exposure)), "f"),
        "allowed_venues": list(definition.allowed_venues or []),
        "allowed_instruments": list(definition.allowed_instruments or []),
        "commissioned_metadata_present": commissioned_metadata_present,
        "commissioned_metadata_shape": commissioned_metadata_shape,
        "linked_asset": None
        if asset is None
        else {
            "asset_id": str(asset.id),
            "symbol": asset.symbol,
            "exchange": asset.exchange,
        },
        "compounding_policy_compatibility": {
            "status": policy_assessment["status"],
            "policy_type": policy_assessment["policy_type"],
            "blockers": list(policy_assessment["blockers"]),
            "warnings": list(policy_assessment["warnings"]),
        },
    }


def _orchestration_ready(*, definition: CapitalCampaignDefinition, policy_assessment: dict[str, Any]) -> bool:
    if not policy_assessment["valid"]:
        return False
    allowed_instruments = {value.strip().upper() for value in definition.allowed_instruments or []}
    allowed_venues = {value.strip().lower() for value in definition.allowed_venues or []}
    return (
        definition.status in _READINESS_ELIGIBLE_STATUSES
        and _SUPPORTED_TRIGGER_PRODUCT in allowed_instruments
        and _SUPPORTED_TRIGGER_PROVIDER in allowed_venues
    )


async def _load_orchestration_definition(*, db: AsyncSession, campaign_id: UUID, version: int | None) -> tuple[CapitalCampaignDefinition, CapitalCampaign | None, Asset | None, dict[str, Any]]:
    definition_stmt = select(CapitalCampaignDefinition).where(CapitalCampaignDefinition.campaign_id == campaign_id)
    if version is not None:
        definition_stmt = definition_stmt.where(CapitalCampaignDefinition.version == version)
    definition = await db.scalar(
        definition_stmt.order_by(desc(CapitalCampaignDefinition.version), desc(CapitalCampaignDefinition.created_at)).limit(1)
    )
    if definition is None:
        raise LookupError("capital campaign definition not found")

    runtime = await _load_runtime_for_definition(db=db, campaign_id=definition.campaign_id)
    asset = await _load_btc_asset_for_provider(db=db)
    policy_assessment = _assess_compounding_policy(definition.compounding_policy)
    return definition, runtime, asset, policy_assessment


async def _list_orchestration_definitions(*, db: AsyncSession) -> list[tuple[CapitalCampaignDefinition, CapitalCampaign | None, Asset | None, dict[str, Any]]]:
    rows = list(
        (
            await db.execute(
                select(CapitalCampaignDefinition)
                .order_by(desc(CapitalCampaignDefinition.campaign_id), desc(CapitalCampaignDefinition.version), desc(CapitalCampaignDefinition.created_at))
            )
        ).scalars().all()
    )
    latest_by_campaign: dict[UUID, CapitalCampaignDefinition] = {}
    for item in rows:
        latest_by_campaign.setdefault(item.campaign_id, item)

    asset = await _load_btc_asset_for_provider(db=db)
    result: list[tuple[CapitalCampaignDefinition, CapitalCampaign | None, Asset | None, dict[str, Any]]] = []
    for definition in latest_by_campaign.values():
        runtime = await _load_runtime_for_definition(db=db, campaign_id=definition.campaign_id)
        policy_assessment = _assess_compounding_policy(definition.compounding_policy)
        result.append((definition, runtime, asset, policy_assessment))
    return result


def build_campaign_orchestration_idempotency_key(
    *,
    campaign_id: UUID,
    version: int,
    trigger: str,
    candle_close_time: datetime,
    eligible_instruments: list[str],
    execution_mode: str,
) -> str:
    payload = {
        "campaign_id": str(campaign_id),
        "version": version,
        "trigger": trigger,
        "candle_close_time": candle_close_time.astimezone(timezone.utc).isoformat(),
        "eligible_instruments": sorted({value.strip().upper() for value in eligible_instruments if value.strip()}),
        "execution_mode": execution_mode.strip().lower(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _campaign_cycle_summary(cycle: AutonomousCycleRun | None) -> dict[str, Any]:
    if cycle is None:
        return {
            "cycle_id": None,
            "state": None,
            "cycle_kind": None,
            "capital_campaign_id": None,
            "capital_campaign_version": None,
            "started_at": None,
            "completed_at": None,
            "termination_stage": None,
            "failure_reason": None,
            "deterministic_explanation": [],
            "preview": None,
        }

    preview = cycle.cycle_context.get("campaign_preview") if isinstance(cycle.cycle_context, dict) else None
    return {
        "cycle_id": cycle.cycle_id,
        "state": cycle.state,
        "cycle_kind": cycle.cycle_kind,
        "capital_campaign_id": cycle.capital_campaign_id,
        "capital_campaign_version": cycle.capital_campaign_version,
        "started_at": cycle.started_at,
        "completed_at": cycle.completed_at,
        "termination_stage": cycle.termination_stage,
        "failure_reason": cycle.failure_reason,
        "deterministic_explanation": list(cycle.deterministic_explanation or []),
        "preview": preview,
    }


async def _resolve_latest_btc_candle(*, db: AsyncSession) -> Candle | None:
    asset = await db.scalar(
        select(Asset)
        .where(Asset.symbol == "BTC")
        .where(Asset.exchange == _SUPPORTED_TRIGGER_PROVIDER)
        .limit(1)
    )
    if asset is None:
        asset = await db.scalar(select(Asset).where(Asset.symbol == "BTC").limit(1))
    if asset is None:
        return None

    return await db.scalar(
        select(Candle)
        .where(Candle.asset_id == asset.id)
        .where(Candle.interval == _SUPPORTED_TRIGGER_INTERVAL)
        .order_by(Candle.close_time.desc(), Candle.open_time.desc())
        .limit(1)
    )


def _eligible_for_orchestration(*, campaign) -> bool:
    allowed_instruments = {value.strip().upper() for value in campaign.allowed_instruments}
    allowed_venues = {value.strip().lower() for value in campaign.allowed_venues}
    return (
        campaign.status in _READINESS_ELIGIBLE_STATUSES
        and _SUPPORTED_TRIGGER_PRODUCT in allowed_instruments
        and _SUPPORTED_TRIGGER_PROVIDER in allowed_venues
    )


def _campaign_level_skip_reason(*, campaign, allow_draft_preview: bool) -> str | None:
    if campaign.status not in _READINESS_ELIGIBLE_STATUSES:
        return "status_not_eligible"
    allowed_instruments = {value.strip().upper() for value in campaign.allowed_instruments}
    if _SUPPORTED_TRIGGER_PRODUCT not in allowed_instruments:
        return "instrument_not_allowed"
    allowed_venues = {value.strip().lower() for value in campaign.allowed_venues}
    if _SUPPORTED_TRIGGER_PROVIDER not in allowed_venues:
        return "venue_not_allowed"
    if campaign.status == "DRAFT" and not allow_draft_preview:
        return "draft_preview_not_allowed"
    return None


def _considered_campaign_entry(campaign) -> dict[str, Any]:
    return {
        "campaign_id": str(campaign.campaign_id),
        "version": campaign.version,
        "status": campaign.status,
        "allowed_instruments": list(campaign.allowed_instruments or []),
        "allowed_venues": list(campaign.allowed_venues or []),
    }


def _eligible_campaign_entry(campaign) -> dict[str, Any]:
    return {
        "campaign_id": str(campaign.campaign_id),
        "version": campaign.version,
    }


def _skipped_campaign_entry(campaign, reason: str) -> dict[str, Any]:
    return {
        "campaign_id": str(campaign.campaign_id),
        "version": campaign.version,
        "reason": reason,
    }


async def fetch_campaign_orchestration_readiness(*, db: AsyncSession, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    if campaign_id is None:
        campaigns = await _list_orchestration_definitions(db=db)
    else:
        campaigns = [await _load_orchestration_definition(db=db, campaign_id=campaign_id, version=version)]

    readiness_items: list[dict[str, Any]] = []
    for definition, runtime, asset, policy_assessment in campaigns:
        ready = _orchestration_ready(definition=definition, policy_assessment=policy_assessment)
        readiness_items.append(
            {
                "campaign_id": definition.campaign_id,
                "version": definition.version,
                "status": definition.status,
                "ready": ready,
                "allows_draft_preview": definition.status == "DRAFT",
                "supported_trigger": {
                    "provider": _SUPPORTED_TRIGGER_PROVIDER,
                    "product_id": _SUPPORTED_TRIGGER_PRODUCT,
                    "interval": _SUPPORTED_TRIGGER_INTERVAL,
                },
                "blockers": list(policy_assessment["blockers"]),
                "warnings": list(policy_assessment["warnings"]),
                "campaign_snapshot": _build_campaign_snapshot(
                    definition=definition,
                    runtime=runtime,
                    asset=asset,
                    policy_assessment=policy_assessment,
                ),
            }
        )

    return {"mode": "campaign_orchestration_readiness", "campaign_count": len(readiness_items), "items": readiness_items}


async def run_campaign_orchestration_preview_for_candle(
    *,
    db: AsyncSession,
    campaign_id: UUID | None = None,
    version: int | None = None,
    trigger: str = "kraken_btc_15m_candle_close",
    allow_draft_preview: bool = False,
) -> dict[str, Any]:
    candle = await _resolve_latest_btc_candle(db=db)
    if candle is None:
        return {
            "mode": "campaign_orchestration_preview",
            "trigger": trigger,
            "ready": False,
            "reason": "latest_btc_15m_candle_not_found",
            "cycle_count": 0,
            "cycles": [],
            "considered_campaigns": [],
            "eligible_campaigns": [],
            "skipped_campaigns": [],
        }

    considered_campaigns: list[dict[str, Any]] = []
    eligible_campaigns: list[dict[str, Any]] = []
    skipped_campaigns: list[dict[str, Any]] = []
    candidates: list[Any] = []

    if campaign_id is None:
        raw_rows = await CapitalCampaignDomainRepository(db).list(campaign_id=None, status=None, latest_only=True)
        campaigns = await list_campaign_definitions(db=db, campaign_id=None, status=None, latest_only=True)
        domain_by_key = {(item.campaign_id, item.version): item for item in campaigns.items}

        for row in raw_rows:
            considered_campaigns.append(_considered_campaign_entry(row))
            domain_item = domain_by_key.get((row.campaign_id, row.version))
            if domain_item is None:
                runtime = await _load_runtime_for_definition(db=db, campaign_id=row.campaign_id)
                reason = "runtime_campaign_missing" if runtime is None else "runtime_definition_version_mismatch"
                skipped_campaigns.append(_skipped_campaign_entry(row, reason))
                continue

            skip_reason = _campaign_level_skip_reason(campaign=domain_item, allow_draft_preview=allow_draft_preview)
            if skip_reason is not None:
                skipped_campaigns.append(_skipped_campaign_entry(domain_item, skip_reason))
                continue

            eligible_campaigns.append(_eligible_campaign_entry(domain_item))
            candidates.append(domain_item)
    else:
        campaign = await get_campaign_definition(db=db, campaign_id=campaign_id, version=version)
        considered_campaigns.append(_considered_campaign_entry(campaign))
        if campaign.status == "DRAFT" and not allow_draft_preview:
            skipped_campaigns.append(_skipped_campaign_entry(campaign, "draft_preview_not_allowed"))
        else:
            # An explicitly requested campaign_id is always submitted to composition
            # once it clears the draft-preview gate, independent of
            # _eligible_for_orchestration -- preserved as-is since changing it would
            # alter campaign-authorization behavior, out of scope for this
            # observability repair.
            eligible_campaigns.append(_eligible_campaign_entry(campaign))
            candidates.append(campaign)

    created_cycles: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for campaign in candidates:
        composition_result = await compose_campaign_authoritative_cycle(
            db=db,
            campaign_definition=campaign,
            trigger=trigger,
            candle=candle,
        )
        composition = composition_result.composition
        idempotency_key = build_campaign_orchestration_idempotency_key(
            campaign_id=campaign.campaign_id,
            version=campaign.version,
            trigger=trigger,
            candle_close_time=candle.close_time,
            eligible_instruments=list(composition.get("candidate_instruments") or list(campaign.allowed_instruments)),
            execution_mode="preview",
        )
        existing = await db.scalar(select(AutonomousCycleRun).where(AutonomousCycleRun.idempotency_key == idempotency_key).limit(1))
        if existing is not None:
            created_cycles.append(_campaign_cycle_summary(existing))
            continue

        cycle = AutonomousCycleRun(
            idempotency_key=idempotency_key,
            mandate_id=None,
            mandate_version_id=None,
            cycle_kind=_CAMPAIGN_CYCLE_KIND,
            capital_campaign_id=campaign.campaign_id,
            capital_campaign_version=campaign.version,
            state="FAILED_CLOSED" if composition.get("failed_closed") else "COMPLETE",
            evaluation_stage="campaign_authoritative_preview",
            termination_stage=composition.get("termination_stage"),
            failure_reason=composition.get("failure_reason"),
            deterministic_explanation=list(composition.get("deterministic_explanation") or []),
            cycle_context={
                "campaign_id": str(campaign.campaign_id),
                "campaign_version": campaign.version,
                "trigger": trigger,
                "candle": {
                    "asset_id": str(candle.asset_id),
                    "open_time": candle.open_time.isoformat(),
                    "close_time": candle.close_time.isoformat(),
                },
                "campaign_preview": composition_result.preview.model_dump(mode="json") if composition_result.preview is not None else None,
                "authoritative_composition": composition,
                "supported_trigger": {
                    "provider": _SUPPORTED_TRIGGER_PROVIDER,
                    "product_id": _SUPPORTED_TRIGGER_PRODUCT,
                    "interval": _SUPPORTED_TRIGGER_INTERVAL,
                },
            },
            diagnostics={"status": "complete", "trigger": trigger, "decision_kind": (composition.get("selected_decision") or {}).get("decision_kind"), "failure_reason": composition.get("failure_reason")},
            proposed_action=composition.get("proposed_action") or "NO_ACTION",
            mandate_verdict="NOT_APPLICABLE",
            risk_verdict=(composition.get("selected_decision") or {}).get("risk_verdict") or "NOT_APPLICABLE",
            decision_record_id=(
                UUID(str(composition.get("decision_record_id")))
                if composition.get("decision_record_id")
                else None
            ),
            preview_id=None,
            mandate_evaluation_id=None,
            risk_event_id=None if not composition.get("risk_outputs") else next((UUID(value.get("risk_event_id")) for value in (composition.get("risk_outputs") or {}).values() if value.get("risk_event_id")), None),
            audit_correlation_id=uuid4(),
            software_build_version=None,
            started_at=now,
            completed_at=now,
        )
        db.add(cycle)
        await db.flush()
        created_cycles.append(_campaign_cycle_summary(cycle))

    await db.commit()
    return {
        "mode": "campaign_orchestration_preview",
        "trigger": trigger,
        "ready": bool(created_cycles),
        "reason": None if created_cycles else "no_campaign_candidates",
        "cycle_count": len(created_cycles),
        "cycles": created_cycles,
        "considered_campaigns": considered_campaigns,
        "eligible_campaigns": eligible_campaigns,
        "skipped_campaigns": skipped_campaigns,
    }


async def fetch_campaign_orchestration_status(*, db: AsyncSession, campaign_id: UUID, version: int | None) -> dict[str, Any]:
    definition, runtime, asset, policy_assessment = await _load_orchestration_definition(db=db, campaign_id=campaign_id, version=version)
    cycle = await db.scalar(
        select(AutonomousCycleRun)
        .where(AutonomousCycleRun.cycle_kind == _CAMPAIGN_CYCLE_KIND)
        .where(AutonomousCycleRun.capital_campaign_id == definition.campaign_id)
        .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
        .limit(1)
    )
    return {
        "mode": "campaign_orchestration_status",
        "campaign_id": definition.campaign_id,
        "version": definition.version,
        "status": definition.status,
        "ready": _orchestration_ready(definition=definition, policy_assessment=policy_assessment),
        "blockers": list(policy_assessment["blockers"]),
        "warnings": list(policy_assessment["warnings"]),
        "campaign_snapshot": _build_campaign_snapshot(
            definition=definition,
            runtime=runtime,
            asset=asset,
            policy_assessment=policy_assessment,
        ),
        "latest_cycle": _campaign_cycle_summary(cycle),
    }


async def fetch_campaign_orchestration_history(*, db: AsyncSession, campaign_id: UUID, version: int | None, limit: int = 20) -> dict[str, Any]:
    definition, runtime, asset, policy_assessment = await _load_orchestration_definition(db=db, campaign_id=campaign_id, version=version)
    cycles = list(
        (
            await db.execute(
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.cycle_kind == _CAMPAIGN_CYCLE_KIND)
                .where(AutonomousCycleRun.capital_campaign_id == definition.campaign_id)
                .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
                .limit(max(1, limit))
            )
        ).scalars().all()
    )
    return {
        "mode": "campaign_orchestration_history",
        "campaign_id": definition.campaign_id,
        "version": definition.version,
        "count": len(cycles),
        "blockers": list(policy_assessment["blockers"]),
        "warnings": list(policy_assessment["warnings"]),
        "campaign_snapshot": _build_campaign_snapshot(
            definition=definition,
            runtime=runtime,
            asset=asset,
            policy_assessment=policy_assessment,
        ),
        "items": [_campaign_cycle_summary(item) for item in cycles],
    }