from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.live_trading_profile import LiveTradingProfile


def _normalize(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


async def _load_identity(db, campaign_id: UUID) -> dict[str, object]:
    definition_rows = list(
        (
            await db.execute(
                select(CapitalCampaignDefinition)
                .where(CapitalCampaignDefinition.campaign_id == campaign_id)
                .order_by(CapitalCampaignDefinition.version.desc())
            )
        ).scalars().all()
    )
    runtime_rows = list(
        (
            await db.execute(
                select(CapitalCampaign)
                .where(CapitalCampaign.uuid == campaign_id)
                .order_by(CapitalCampaign.updated_at.desc(), CapitalCampaign.id.desc())
            )
        ).scalars().all()
    )
    cycles = list(
        (
            await db.execute(
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.capital_campaign_id == campaign_id)
                .order_by(AutonomousCycleRun.started_at.desc(), AutonomousCycleRun.cycle_id.desc())
                .limit(10)
            )
        ).scalars().all()
    )
    runtime = runtime_rows[0] if runtime_rows else None
    profile = None
    if runtime is not None and runtime.paper_account_id is not None:
        profile = await db.scalar(
            select(LiveTradingProfile)
            .where(LiveTradingProfile.paper_account_id == runtime.paper_account_id)
            .order_by(LiveTradingProfile.updated_at.desc())
            .limit(1)
        )

    return {
        "campaign_id": str(campaign_id),
        "definition_count": len(definition_rows),
        "definitions": [
            {
                "model": "capital_campaign_definitions",
                "campaign_id": str(item.campaign_id),
                "version": item.version,
                "status": item.status,
                "owner_identity": item.owner_identity,
                "capital_budget": _normalize(item.capital_budget),
                "remaining_unallocated_capital": _normalize(item.remaining_unallocated_capital),
                "allowed_venues": list(item.allowed_venues or []),
                "allowed_instruments": list(item.allowed_instruments or []),
                "risk_policy_id": item.risk_policy_id,
                "risk_policy_version": item.risk_policy_version,
                "profitability_policy_id": item.profitability_policy_id,
                "profitability_policy_version": item.profitability_policy_version,
                "compounding_policy": _normalize(item.compounding_policy),
                "profit_distribution_policy": _normalize(item.profit_distribution_policy),
                "created_at": _normalize(item.created_at),
                "updated_at": _normalize(item.updated_at),
            }
            for item in definition_rows
        ],
        "runtime": None
        if runtime is None
        else {
            "model": "capital_campaigns",
            "id": runtime.id,
            "uuid": str(runtime.uuid),
            "status": runtime.status,
            "owner": runtime.owner,
            "campaign_type": runtime.campaign_type,
            "paper_account_id": None if runtime.paper_account_id is None else str(runtime.paper_account_id),
            "strategy_id": None if runtime.strategy_id is None else str(runtime.strategy_id),
            "definition_campaign_id": None if runtime.definition_campaign_id is None else str(runtime.definition_campaign_id),
            "definition_version": runtime.definition_version,
            "starting_capital": _normalize(runtime.starting_capital),
            "current_equity": _normalize(runtime.current_equity),
            "created_at": _normalize(runtime.created_at),
            "updated_at": _normalize(runtime.updated_at),
        },
        "live_profile": None
        if profile is None
        else {
            "id": str(profile.id),
            "paper_account_id": str(profile.paper_account_id),
            "operating_mode": profile.operating_mode,
            "lifecycle_state": profile.lifecycle_state,
            "approval_state": profile.approval_state,
            "paper_default_mode": profile.paper_default_mode,
            "risk_authority_model": profile.risk_authority_model,
            "created_at": _normalize(profile.created_at),
            "updated_at": _normalize(profile.updated_at),
        },
        "recent_cycles": [
            {
                "cycle_id": str(item.cycle_id),
                "cycle_kind": item.cycle_kind,
                "mandate_id": None if item.mandate_id is None else str(item.mandate_id),
                "mandate_version_id": None if item.mandate_version_id is None else str(item.mandate_version_id),
                "capital_campaign_id": None if item.capital_campaign_id is None else str(item.capital_campaign_id),
                "capital_campaign_version": item.capital_campaign_version,
                "state": item.state,
                "proposed_action": item.proposed_action,
                "risk_verdict": item.risk_verdict,
                "failure_reason": item.failure_reason,
                "started_at": _normalize(item.started_at),
                "completed_at": _normalize(item.completed_at),
            }
            for item in cycles
        ],
        "relationships": {
            "runtime_pins_definition": runtime is not None and len(definition_rows) > 0 and runtime.definition_campaign_id == campaign_id,
            "definition_versions": [item.version for item in definition_rows],
            "runtime_definition_version": None if runtime is None else runtime.definition_version,
        },
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only campaign identity audit")
    parser.add_argument("--campaign-id", action="append", type=UUID, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        result = [await _load_identity(db, campaign_id=item) for item in args.campaign_id]

    print(json.dumps({"campaigns": result}, sort_keys=True, default=_normalize, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))