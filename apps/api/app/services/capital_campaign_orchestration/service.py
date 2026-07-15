from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.schemas.capital_campaign_domain import CapitalCampaignPreviewRequest
from app.services.capital_campaign_domain.service import get_campaign_definition, list_campaign_definitions, preview_campaign_definition
from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle


_CAMPAIGN_CYCLE_KIND = "campaign"
_READINESS_ELIGIBLE_STATUSES = {"DRAFT", "READY", "ACTIVE", "PAUSED"}
_SUPPORTED_TRIGGER_PROVIDER = "kraken_spot"
_SUPPORTED_TRIGGER_PRODUCT = "BTC-USD"
_SUPPORTED_TRIGGER_INTERVAL = "15m"


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


async def fetch_campaign_orchestration_readiness(*, db: AsyncSession, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    if campaign_id is None:
        campaigns = await list_campaign_definitions(db=db, campaign_id=None, status=None, latest_only=True)
        items = campaigns.items
    else:
        items = [await get_campaign_definition(db=db, campaign_id=campaign_id, version=version)]

    readiness_items: list[dict[str, Any]] = []
    for item in items:
        readiness_items.append(
            {
                "campaign_id": item.campaign_id,
                "version": item.version,
                "status": item.status,
                "ready": _eligible_for_orchestration(campaign=item),
                "allows_draft_preview": item.status == "DRAFT",
                "supported_trigger": {
                    "provider": _SUPPORTED_TRIGGER_PROVIDER,
                    "product_id": _SUPPORTED_TRIGGER_PRODUCT,
                    "interval": _SUPPORTED_TRIGGER_INTERVAL,
                },
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
        return {"mode": "campaign_orchestration_preview", "trigger": trigger, "ready": False, "reason": "latest_btc_15m_candle_not_found", "cycle_count": 0, "cycles": []}

    if campaign_id is None:
        campaigns = await list_campaign_definitions(db=db, campaign_id=None, status=None, latest_only=True)
        candidates = [
            item
            for item in campaigns.items
            if _eligible_for_orchestration(campaign=item)
            and (allow_draft_preview or item.status != "DRAFT")
        ]
    else:
        campaign = await get_campaign_definition(db=db, campaign_id=campaign_id, version=version)
        if campaign.status == "DRAFT" and not allow_draft_preview:
            candidates = []
        else:
            candidates = [campaign] if _eligible_for_orchestration(campaign=campaign) else [campaign]

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
            mandate_id=campaign.runtime_campaign_uuid,
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
            decision_record_id=None,
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
    }


async def fetch_campaign_orchestration_status(*, db: AsyncSession, campaign_id: UUID, version: int | None) -> dict[str, Any]:
    definition = await get_campaign_definition(db=db, campaign_id=campaign_id, version=version)
    cycle = await db.scalar(
        select(AutonomousCycleRun)
        .where(AutonomousCycleRun.cycle_kind == _CAMPAIGN_CYCLE_KIND)
        .where(AutonomousCycleRun.capital_campaign_id == definition.campaign_id)
        .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
        .limit(1)
    )
    return {"mode": "campaign_orchestration_status", "campaign_id": definition.campaign_id, "version": definition.version, "status": definition.status, "ready": definition.status in _READINESS_ELIGIBLE_STATUSES, "latest_cycle": _campaign_cycle_summary(cycle)}


async def fetch_campaign_orchestration_history(*, db: AsyncSession, campaign_id: UUID, version: int | None, limit: int = 20) -> dict[str, Any]:
    definition = await get_campaign_definition(db=db, campaign_id=campaign_id, version=version)
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
    return {"mode": "campaign_orchestration_history", "campaign_id": definition.campaign_id, "version": definition.version, "count": len(cycles), "items": [_campaign_cycle_summary(item) for item in cycles]}