from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.strategy import Strategy
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.strategies.identity import build_strategy_identity

_PACKAGE_STATES = {"READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}


async def inspect_mandate_evaluation_identity_propagation(
    *, db: AsyncSession, cycle_id: uuid.UUID, decision_record_id: uuid.UUID,
) -> dict[str, Any]:
    cycle = await db.scalar(
        select(AutonomousCycleRun).where(AutonomousCycleRun.cycle_id == cycle_id).limit(1)
    )
    campaign_cycle = cycle if cycle is not None and cycle.cycle_kind == "campaign" else None
    autonomous_cycle = cycle if cycle is not None and cycle.cycle_kind == "autonomous" else None
    roster_run = None
    if campaign_cycle is not None:
        context = campaign_cycle.cycle_context if isinstance(campaign_cycle.cycle_context, dict) else {}
        candle = context.get("candle") if isinstance(context.get("candle"), dict) else {}
        close_raw = candle.get("close_time")
        trigger = str(context.get("trigger") or "")
        if close_raw and trigger:
            close_time = datetime.fromisoformat(str(close_raw).replace("Z", "+00:00"))
            roster_run = await db.scalar(
                select(StrategyRosterRun).where(
                    StrategyRosterRun.candle_close_time == close_time,
                    StrategyRosterRun.trigger == trigger,
                ).limit(1)
            )
        if roster_run is not None and roster_run.scheduled_cycle_id is not None:
            autonomous_cycle = await db.scalar(
                select(AutonomousCycleRun).where(
                    AutonomousCycleRun.cycle_id == roster_run.scheduled_cycle_id,
                    AutonomousCycleRun.cycle_kind == "autonomous",
                ).limit(1)
            )
    evaluation_id = None if campaign_cycle is None else campaign_cycle.mandate_evaluation_id
    evaluation = None if evaluation_id is None else await db.scalar(
        select(AutonomousCapitalMandateEvaluation)
        .where(AutonomousCapitalMandateEvaluation.evaluation_id == evaluation_id)
        .limit(1)
    )
    missing_at = []
    if autonomous_cycle is None: missing_at.append("autonomous_cycle_resolution")
    if campaign_cycle is None: missing_at.append("campaign_cycle_resolution")
    if campaign_cycle is not None and campaign_cycle.mandate_id is None: missing_at.append("campaign_cycle.mandate_id")
    if campaign_cycle is not None and campaign_cycle.mandate_version_id is None: missing_at.append("campaign_cycle.mandate_version_id")
    if campaign_cycle is not None and campaign_cycle.mandate_evaluation_id is None: missing_at.append("campaign_cycle.mandate_evaluation_id")
    if campaign_cycle is not None and campaign_cycle.decision_record_id != decision_record_id: missing_at.append("campaign_cycle.decision_record_id_mismatch")
    return {
        "verdict": "COMPLETE" if not missing_at and evaluation is not None else "INCOMPLETE",
        "requested_cycle_id": str(cycle_id),
        "requested_decision_record_id": str(decision_record_id),
        "autonomous_cycle_exists": autonomous_cycle is not None,
        "campaign_cycle_exists": campaign_cycle is not None,
        "autonomous_cycle": None if autonomous_cycle is None else {
            "exists": True, "cycle_id": str(autonomous_cycle.cycle_id),
            "mandate_id": None if autonomous_cycle.mandate_id is None else str(autonomous_cycle.mandate_id),
            "mandate_version_id": None if autonomous_cycle.mandate_version_id is None else str(autonomous_cycle.mandate_version_id),
            "mandate_evaluation_id": None if autonomous_cycle.mandate_evaluation_id is None else str(autonomous_cycle.mandate_evaluation_id),
            "decision_record_id": None if autonomous_cycle.decision_record_id is None else str(autonomous_cycle.decision_record_id),
            "proposed_action": autonomous_cycle.proposed_action,
        },
        "campaign_cycle": None if campaign_cycle is None else {
            "exists": True, "cycle_id": str(campaign_cycle.cycle_id),
            "campaign_id": None if campaign_cycle.capital_campaign_id is None else str(campaign_cycle.capital_campaign_id),
            "campaign_version": campaign_cycle.capital_campaign_version,
            "mandate_id": None if campaign_cycle.mandate_id is None else str(campaign_cycle.mandate_id),
            "mandate_version_id": None if campaign_cycle.mandate_version_id is None else str(campaign_cycle.mandate_version_id),
            "mandate_evaluation_id": None if campaign_cycle.mandate_evaluation_id is None else str(campaign_cycle.mandate_evaluation_id),
            "decision_record_id": None if campaign_cycle.decision_record_id is None else str(campaign_cycle.decision_record_id),
            "preview_id": None if campaign_cycle.preview_id is None else str(campaign_cycle.preview_id),
            "proposed_action": campaign_cycle.proposed_action,
        },
        "evaluation": None if evaluation is None else {
            "evaluation_id": str(evaluation.evaluation_id),
            "decision_record_id": None if evaluation.decision_id is None else str(evaluation.decision_id),
            "proposed_action": evaluation.proposed_action,
            "authorization_result": evaluation.authorization_result,
            "approval_result": evaluation.approval_result,
        },
        "roster_run_id": None if roster_run is None else str(roster_run.roster_run_id),
        "missing_at": missing_at,
        "read_only": True,
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _package_item(package: CanonicalPreviewPackage, *, now: datetime) -> dict[str, Any]:
    return {
        "package_id": str(package.package_id),
        "campaign_id": str(package.campaign_id),
        "campaign_version": package.campaign_version,
        "decision_record_id": str(package.decision_record_id),
        "state": package.package_state,
        "authority_source": package.authorization_source,
        "mandate_id": None if package.mandate_id is None else str(package.mandate_id),
        "created_at": _iso(package.created_at),
        "preview_expires_at": _iso(package.preview_expires_at),
        "authorization_expires_at": _iso(package.authorization_expires_at),
        "stale": package.preview_expires_at <= now,
        "superseded": package.package_state == "SUPERSEDED" or package.superseded_at is not None,
    }


def _comparison(
    *, field: str, mandate: Any, package: Any, source: str, reason: str, match: bool | None = None,
) -> dict[str, Any]:
    passed = mandate == package if match is None else match
    return {
        "field": field,
        "mandate_value": None if mandate is None else str(mandate),
        "package_value": None if package is None else str(package),
        "match": passed,
        "canonical_source": source,
        "reason_code": None if passed else reason,
    }


async def inspect_automatic_mandate_activation_readiness(
    *, db: AsyncSession, provider: str, environment: str, product: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    settings = get_settings()
    reasons: list[dict[str, str]] = []
    packages = list((await db.scalars(
        select(CanonicalPreviewPackage)
        .where(
            CanonicalPreviewPackage.provider == provider,
            CanonicalPreviewPackage.environment == environment,
            CanonicalPreviewPackage.product == product,
            CanonicalPreviewPackage.package_state.in_(_PACKAGE_STATES),
        )
        .order_by(CanonicalPreviewPackage.generated_at.desc())
    )).all())
    eligible_packages = [p for p in packages if p.preview_expires_at > now and p.package_state != "SUPERSEDED"]
    if not packages:
        reasons.append({"code": "no_package_available", "action": "Wait for an executable decision to create a READY package."})
    elif len(eligible_packages) != 1:
        reasons.append({"code": "ambiguous_eligible_packages" if eligible_packages else "stale_package", "action": "Resolve stale or conflicting canonical packages before enablement."})
    if any(p.authorization_source == "HUMAN" for p in eligible_packages):
        reasons.append({"code": "conflicting_human_authority", "action": "Use a mandate-authorized package; do not convert human evidence."})

    mandates = list((await db.scalars(
        select(AutonomousCapitalMandate)
        .where(
            AutonomousCapitalMandate.status == "ACTIVE",
            AutonomousCapitalMandate.autonomy_level == "LEVEL_2",
            AutonomousCapitalMandate.provider == provider,
            AutonomousCapitalMandate.exchange_environment == environment,
        )
        .order_by(AutonomousCapitalMandate.created_at.asc()).limit(2)
    )).all())
    if len(mandates) != 1:
        reasons.append({"code": "missing_active_level2_mandate" if not mandates else "ambiguous_active_level2_mandates", "action": "Commission exactly one matching ACTIVE LEVEL_2 mandate."})

    mandate_payload = None
    if len(mandates) == 1:
        mandate = mandates[0]
        authorization = await db.scalar(
            select(AutonomousCapitalMandateAuthorization)
            .where(
                AutonomousCapitalMandateAuthorization.mandate_id == mandate.mandate_id,
                AutonomousCapitalMandateAuthorization.authorization_state == "AUTHORIZED",
                AutonomousCapitalMandateAuthorization.revoked_at.is_(None),
            ).order_by(AutonomousCapitalMandateAuthorization.recorded_at.desc()).limit(1)
        )
        version = None if authorization is None else await db.scalar(
            select(AutonomousCapitalMandateVersion).where(
                AutonomousCapitalMandateVersion.mandate_version_id == authorization.mandate_version_id
            ).limit(1)
        )
        package = eligible_packages[0] if len(eligible_packages) == 1 else None
        evaluation = None if package is None or package.mandate_evaluation_id is None else await db.scalar(
            select(AutonomousCapitalMandateEvaluation)
            .where(AutonomousCapitalMandateEvaluation.evaluation_id == package.mandate_evaluation_id)
            .limit(1)
        )
        runtime_campaign = None if package is None else await db.scalar(
            select(CapitalCampaign).where(CapitalCampaign.uuid == package.runtime_campaign_id).limit(1)
        )
        strategy = None if package is None else await db.scalar(
            select(Strategy).where(Strategy.id == package.strategy_id).limit(1)
        )
        preview = None if package is None else await db.scalar(
            select(CryptoOrderPreview).where(CryptoOrderPreview.crypto_order_preview_id == package.crypto_order_preview_id).limit(1)
        )
        decision = None if package is None else await db.scalar(
            select(DecisionRecord).where(DecisionRecord.decision_id == package.decision_record_id).limit(1)
        )
        profile = None if package is None else await db.scalar(
            select(LiveTradingProfile).where(LiveTradingProfile.id == package.live_trading_profile_id).limit(1)
        )
        strategy_identity = None if strategy is None else build_strategy_identity(
            slug=strategy.slug, module_version=package.strategy_version,
        )
        connection_raw = None if package is None else (package.market_evidence_identity or {}).get("exchange_connection_id")
        try:
            connection_id = None if connection_raw is None else uuid.UUID(str(connection_raw))
        except ValueError:
            connection_id = None
        connection = None if connection_id is None else await db.scalar(
            select(ExchangeConnection).where(ExchangeConnection.exchange_connection_id == connection_id).limit(1)
        )
        comparisons = [] if package is None else [
            _comparison(field="package_mandate", mandate=mandate.mandate_id, package=package.mandate_id, source="autonomous_capital_mandates.mandate_id ↔ canonical_preview_packages.mandate_id", reason="package_mandate_mismatch"),
            _comparison(field="package_mandate_version", mandate=None if version is None else version.mandate_version_id, package=package.mandate_version_id, source="autonomous_capital_mandate_versions.mandate_version_id ↔ canonical_preview_packages.mandate_version_id", reason="package_mandate_version_mismatch"),
            _comparison(field="evaluation_mandate", mandate=mandate.mandate_id, package=None if evaluation is None else evaluation.mandate_id, source="autonomous_capital_mandate_evaluations.mandate_id ↔ autonomous_capital_mandates.mandate_id", reason="matching_mandate_evaluation_missing"),
            _comparison(field="evaluation_version", mandate=None if version is None else version.mandate_version_id, package=None if evaluation is None else evaluation.mandate_version_id, source="autonomous_capital_mandate_evaluations.mandate_version_id ↔ autonomous_capital_mandate_versions.mandate_version_id", reason="mandate_evaluation_version_mismatch"),
            _comparison(field="evaluation_decision", mandate=package.decision_record_id, package=None if evaluation is None else evaluation.decision_id, source="autonomous_capital_mandate_evaluations.decision_id ↔ canonical_preview_packages.decision_record_id", reason="mandate_evaluation_decision_mismatch"),
            _comparison(field="evaluation_action", mandate=package.side, package=None if evaluation is None else evaluation.proposed_action, source="autonomous_capital_mandate_evaluations.proposed_action ↔ canonical_preview_packages.side", reason="mandate_evaluation_side_mismatch"),
            _comparison(field="evaluation_authorization", mandate="AUTHORIZED", package=None if evaluation is None else evaluation.authorization_result, source="autonomous_capital_mandate_evaluations.authorization_result", reason="mandate_evaluation_not_authorized"),
            _comparison(field="evaluation_approval", mandate="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE", package=None if evaluation is None else evaluation.approval_result, source="autonomous_capital_mandate_evaluations.approval_result", reason="mandate_evaluation_approval_mismatch"),
            _comparison(field="mandate_version_owner", mandate=mandate.mandate_id, package=None if version is None else version.mandate_id, source="autonomous_capital_mandate_versions.mandate_id ↔ autonomous_capital_mandates.mandate_id", reason="mandate_version_mismatch"),
            _comparison(field="campaign_runtime", mandate=mandate.capital_campaign_id, package=None if runtime_campaign is None else runtime_campaign.id, source="autonomous_capital_mandates.capital_campaign_id ↔ capital_campaigns.id", reason="campaign_identity_mismatch"),
            _comparison(field="campaign_uuid", mandate=None if runtime_campaign is None else runtime_campaign.definition_campaign_id, package=package.campaign_id, source="capital_campaigns.definition_campaign_id ↔ canonical_preview_packages.campaign_id", reason="campaign_identity_mismatch"),
            _comparison(field="campaign_version", mandate=None if runtime_campaign is None else runtime_campaign.definition_version, package=package.campaign_version, source="capital_campaigns.definition_version ↔ canonical_preview_packages.campaign_version", reason="campaign_version_mismatch"),
            _comparison(field="paper_account_id", mandate=mandate.paper_account_id, package=package.paper_account_id, source="autonomous_capital_mandates ↔ canonical_preview_packages", reason="paper_account_mismatch"),
            _comparison(field="live_trading_profile_id", mandate=mandate.live_trading_profile_id, package=package.live_trading_profile_id, source="autonomous_capital_mandates ↔ canonical_preview_packages", reason="profile_mismatch"),
            _comparison(field="exchange_connection_id", mandate=mandate.exchange_connection_id, package=connection_id, source="autonomous_capital_mandates.exchange_connection_id ↔ canonical_preview_packages.market_evidence_identity", reason="connection_mismatch"),
            _comparison(field="provider", mandate=mandate.provider, package=package.provider, source="autonomous_capital_mandates ↔ canonical_preview_packages", reason="provider_mismatch"),
            _comparison(field="environment", mandate=mandate.exchange_environment, package=package.environment, source="autonomous_capital_mandates ↔ canonical_preview_packages", reason="environment_mismatch"),
            _comparison(field="product", mandate=None if version is None else version.allowed_products, package=package.product, match=version is not None and package.product in version.allowed_products, source="autonomous_capital_mandate_versions.allowed_products ↔ canonical_preview_packages.product", reason="product_mismatch"),
            _comparison(field="side", mandate=None if version is None else version.allowed_order_sides, package=package.side, match=version is not None and package.side in version.allowed_order_sides, source="autonomous_capital_mandate_versions.allowed_order_sides ↔ canonical_preview_packages.side", reason="side_mismatch"),
            _comparison(field="strategy_identity", mandate=None if version is None else version.allowed_strategy_versions, package=strategy_identity, match=version is not None and strategy_identity in version.allowed_strategy_versions, source="strategies.slug+module_version ↔ autonomous_capital_mandate_versions.allowed_strategy_versions", reason="strategy_identity_mismatch"),
            _comparison(field="max_order_notional", mandate=None if version is None else version.max_order_notional_usd, package=package.risk_approved_amount, match=version is not None and package.risk_approved_amount <= version.max_order_notional_usd, source="autonomous_capital_mandate_versions.max_order_notional_usd ↔ canonical_preview_packages.risk_approved_amount", reason="capital_limit_mismatch"),
            _comparison(field="authorized_capital", mandate=None if version is None else version.authorized_capital_usd, package=package.risk_approved_amount, match=version is not None and package.risk_approved_amount <= version.authorized_capital_usd, source="autonomous_capital_mandate_versions.authorized_capital_usd ↔ canonical_preview_packages.risk_approved_amount", reason="capital_limit_mismatch"),
            _comparison(field="max_open_exposure", mandate=None if version is None else version.max_open_exposure_usd, package=package.risk_approved_amount, match=version is not None and package.risk_approved_amount <= version.max_open_exposure_usd, source="autonomous_capital_mandate_versions.max_open_exposure_usd ↔ canonical_preview_packages.risk_approved_amount", reason="capital_limit_mismatch"),
            _comparison(field="max_daily_deployed", mandate=None if version is None else version.max_daily_deployed_usd, package=package.risk_approved_amount, match=version is not None and package.risk_approved_amount <= version.max_daily_deployed_usd, source="autonomous_capital_mandate_versions.max_daily_deployed_usd ↔ canonical_preview_packages.risk_approved_amount", reason="capital_limit_mismatch"),
            _comparison(field="approval_policy", mandate="MANDATE_ALLOWED", package=None if version is None else version.approval_policy, source="autonomous_capital_mandate_versions.approval_policy", reason="mandate_approval_policy_mismatch"),
            _comparison(field="profile_paper_account", mandate=package.paper_account_id, package=None if profile is None else profile.paper_account_id, source="canonical_preview_packages.paper_account_id ↔ live_trading_profiles.paper_account_id", reason="profile_paper_account_mismatch"),
            _comparison(field="connection_provider", mandate=package.provider, package=None if connection is None else connection.provider, source="canonical_preview_packages.provider ↔ exchange_connections.provider", reason="connection_provider_mismatch"),
            _comparison(field="connection_environment", mandate=package.environment, package=None if connection is None else connection.environment, source="canonical_preview_packages.environment ↔ exchange_connections.environment", reason="connection_environment_mismatch"),
            _comparison(field="preview_decision_record", mandate=package.decision_record_id, package=None if preview is None else preview.decision_record_id, source="canonical_preview_packages.decision_record_id ↔ crypto_order_previews.decision_record_id", reason="preview_decision_mismatch"),
            _comparison(field="decision_record_exists", mandate=package.decision_record_id, package=None if decision is None else decision.decision_id, source="canonical_preview_packages.decision_record_id ↔ decision_records.decision_id", reason="decision_record_missing"),
            _comparison(field="preview_provider", mandate=package.provider, package=None if preview is None else preview.provider, source="canonical_preview_packages.provider ↔ crypto_order_previews.provider", reason="preview_provider_mismatch"),
            _comparison(field="preview_environment", mandate=package.environment, package=None if preview is None else preview.environment, source="canonical_preview_packages.environment ↔ crypto_order_previews.environment", reason="preview_environment_mismatch"),
            _comparison(field="preview_product", mandate=package.product, package=None if preview is None else preview.product_id, source="canonical_preview_packages.product ↔ crypto_order_previews.product_id", reason="preview_product_mismatch"),
            _comparison(field="preview_side", mandate=package.side, package=None if preview is None else preview.side, source="canonical_preview_packages.side ↔ crypto_order_previews.side", reason="preview_side_mismatch"),
            _comparison(field="preview_strategy", mandate=package.strategy_id, package=None if preview is None else preview.strategy_id, source="canonical_preview_packages.strategy_id ↔ crypto_order_previews.strategy_id", reason="preview_strategy_mismatch"),
            _comparison(field="preview_notional", mandate=package.proposed_order_amount, package=None if preview is None else preview.requested_amount, source="canonical_preview_packages.proposed_order_amount ↔ crypto_order_previews.requested_amount", reason="preview_notional_mismatch"),
        ]
        if mandate.revoked_at is not None or (mandate.expires_at is not None and mandate.expires_at <= now):
            reasons.append({"code": "mandate_expired_or_revoked", "action": "Renew or replace the mandate through governed lifecycle commands."})
        if mandate.status != "ACTIVE" or mandate.autonomy_level != "LEVEL_2":
            reasons.append({"code": "mandate_not_active_level2", "action": "Commission an ACTIVE LEVEL_2 mandate for unattended progression."})
        if authorization is None or (authorization.expires_at is not None and authorization.expires_at <= now):
            reasons.append({"code": "mandate_authorization_inactive", "action": "Restore valid owner authorization."})
        if version is None or not version.is_active or not version.is_authorized:
            reasons.append({"code": "mandate_version_inactive", "action": "Activate the authorized mandate version."})
        evaluation_comparisons = [item for item in comparisons if item["field"].startswith("evaluation_")]
        identity_comparisons = [item for item in comparisons if not item["field"].startswith("evaluation_")]
        if package is not None and any(not item["match"] for item in evaluation_comparisons):
            reasons.append({"code": "matching_mandate_evaluation_missing", "action": "Generate a fresh package from a successfully mandate-evaluated autonomous cycle."})
        if any(not item["match"] for item in identity_comparisons):
            reasons.append({"code": "package_identity_mismatch", "action": "Generate a package whose account, profile, venue, strategy, side, product, and capital scope match the mandate."})
        mandate_payload = {
            "mandate_id": str(mandate.mandate_id), "status": mandate.status,
            "autonomy_level": mandate.autonomy_level, "expires_at": _iso(mandate.expires_at),
            "revoked": mandate.revoked_at is not None,
            "authorization_active": authorization is not None,
            "mandate_version_id": None if version is None else str(version.mandate_version_id),
            "mandate_version": None if version is None else version.version_number,
            "matching_evaluation_id": None if evaluation is None else str(evaluation.evaluation_id),
            "evaluation_readiness": {
                "status": "SUCCESSFUL_MATCH" if evaluation_comparisons and all(item["match"] for item in evaluation_comparisons) else "PREFLIGHT_BLOCKED",
                "canonical_operation": "evaluate_and_record_mandate",
                "package_ready_state_requires_persisted_evaluation": True,
            },
            "identity_comparisons": comparisons,
            "campaign_runtime_id": None if mandate.capital_campaign_id is None else mandate.capital_campaign_id,
            "paper_account_id": None if mandate.paper_account_id is None else str(mandate.paper_account_id),
            "live_trading_profile_id": str(mandate.live_trading_profile_id),
            "exchange_connection_id": str(mandate.exchange_connection_id),
            "provider": mandate.provider,
            "environment": mandate.exchange_environment,
            "allowed_products": None if version is None else version.allowed_products,
            "allowed_sides": None if version is None else version.allowed_order_sides,
            "allowed_strategy_versions": None if version is None else version.allowed_strategy_versions,
            "authorized_capital_usd": None if version is None else str(version.authorized_capital_usd),
            "max_order_notional_usd": None if version is None else str(version.max_order_notional_usd),
        }

    activations = list((await db.scalars(select(CanonicalProvingActivation).where(
        CanonicalProvingActivation.provider == provider,
        CanonicalProvingActivation.environment == environment,
        CanonicalProvingActivation.product == product,
        CanonicalProvingActivation.activation_state == "ACTIVE",
        CanonicalProvingActivation.expires_at > now,
    ).limit(2))).all())
    if len(activations) > 1:
        reasons.append({"code": "conflicting_active_activations", "action": "Resolve conflicting proving activations."})

    latest_pipeline = await db.scalar(select(AuditLog).where(
        AuditLog.action == "orchestration_worker_full_pipeline_completed"
    ).order_by(AuditLog.created_at.desc()).limit(1))
    enabled = settings.automatic_mandate_package_activation_enabled
    if reasons:
        verdict = "NOT_READY"
    elif enabled:
        verdict = "ALREADY_ENABLED_AND_HEALTHY"
    else:
        verdict = "READY_TO_ENABLE"
    return {
        "verdict": verdict,
        "reason_codes": reasons,
        "configuration": {
            "automatic_mandate_package_activation_enabled": enabled,
            "live_crypto_preparation_enabled": settings.live_crypto_preparation_enabled,
            "live_crypto_order_submission_enabled": settings.live_crypto_order_submission_enabled,
            "provider": provider, "environment": environment, "product": product,
        },
        "worker": {
            "deployed_application_version": os.getenv("DEPLOYED_GIT_SHA") or os.getenv("GIT_SHA"),
            "latest_completed_pipeline_at": None if latest_pipeline is None else _iso(latest_pipeline.created_at),
            "automatic_activation_service_present": True,
        },
        "mandate": mandate_payload,
        "packages": [_package_item(item, now=now) for item in packages],
        "package_inventory": {
            state: [str(item.package_id) for item in packages if item.package_state == state]
            for state in ("READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED")
        },
        "eligible_package_count": len(eligible_packages),
        "active_activation_count": len(activations),
        "submission_boundary": {
            "activation_implies_submission": False,
            "live_submission_flag_enabled": settings.live_crypto_order_submission_enabled,
            "submission_callable_reachable": False,
            "provider_submission_callable_reachable": False,
        },
        "read_only": True,
    }


async def inspect_automatic_mandate_activation_proof(*, db: AsyncSession, package_id: uuid.UUID) -> dict[str, Any]:
    reasons: list[str] = []
    package = await db.scalar(select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == package_id).limit(1))
    if package is None:
        return {"verdict": "NOT_PROVEN", "package_id": str(package_id), "reason_codes": ["package_missing"], "read_only": True}
    evaluation = None if package.mandate_evaluation_id is None else await db.scalar(
        select(AutonomousCapitalMandateEvaluation).where(AutonomousCapitalMandateEvaluation.evaluation_id == package.mandate_evaluation_id).limit(1)
    )
    dry_order = None if package.dry_run_live_crypto_order_id is None else await db.scalar(
        select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == package.dry_run_live_crypto_order_id).limit(1)
    )
    activation = await db.scalar(select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package_id).limit(1))
    if package.authorization_source != "MANDATE" or package.approval_event_id is not None:
        reasons.append("human_authority_contamination")
    if evaluation is None or evaluation.approval_result != "APPROVAL_SATISFIED_BY_ACTIVE_MANDATE" or evaluation.authorization_result != "AUTHORIZED":
        reasons.append("mandate_authorization_evidence_missing")
    dry_evidence = {} if dry_order is None or not isinstance(dry_order.safe_provider_response, dict) else dry_order.safe_provider_response
    if dry_order is None or dry_order.status != "DRY_RUN_READY": reasons.append("dry_run_evidence_missing")
    if dry_evidence.get("dry_run") is not True or dry_evidence.get("submission_skipped") is not True: reasons.append("dry_run_boundary_violated")
    if activation is None or activation.authority_source != "MANDATE" or activation.approval_event_id is not None: reasons.append("mandate_activation_evidence_missing")
    correlation = None if package.authority_audit_correlation_id is None else str(package.authority_audit_correlation_id)
    if dry_evidence.get("authority_audit_correlation_id") != correlation or activation is None or str(activation.authority_audit_correlation_id) != correlation:
        reasons.append("audit_correlation_mismatch")
    if activation is not None and (
        activation.campaign_id != package.campaign_id
        or activation.campaign_version != package.campaign_version
        or activation.paper_account_id != package.paper_account_id
        or activation.live_trading_profile_id != package.live_trading_profile_id
        or activation.provider != package.provider
        or activation.environment != package.environment
        or activation.product != package.product
        or activation.dry_run_live_crypto_order_id != package.dry_run_live_crypto_order_id
        or activation.mandate_evaluation_id != package.mandate_evaluation_id
    ): reasons.append("package_identity_mismatch")
    if dry_order is not None and (
        dry_order.decision_record_id != package.decision_record_id
        or dry_order.provider != package.provider
        or dry_order.environment != package.environment
        or dry_order.product_id != package.product
        or dry_order.side != package.side
        or (activation is not None and dry_order.exchange_connection_id != activation.exchange_connection_id)
    ): reasons.append("package_identity_mismatch")
    if dry_order is not None and (dry_order.provider_order_id is not None or dry_order.submitted_at is not None): reasons.append("live_submission_evidence_present")
    recon_count = 0 if dry_order is None else int(await db.scalar(select(func.count(LiveReconciliationEvent.id)).where(
        LiveReconciliationEvent.live_crypto_order_id == dry_order.live_crypto_order_id
    )) or 0)
    if recon_count: reasons.append("reconciliation_evidence_present")
    position_count = 0 if dry_order is None else int(await db.scalar(select(func.count(LiveAccountingRecord.id)).where(
        LiveAccountingRecord.live_crypto_order_id == dry_order.live_crypto_order_id
    )) or 0)
    if position_count: reasons.append("position_evidence_present")
    verdict = "PROVEN" if not reasons else ("CONFLICT" if any("contamination" in r or "present" in r or "mismatch" in r for r in reasons) else "NOT_PROVEN")
    return {
        "verdict": verdict, "reason_codes": reasons, "package_id": str(package.package_id),
        "campaign_id": str(package.campaign_id), "campaign_version": package.campaign_version,
        "decision_record_id": str(package.decision_record_id), "mandate_id": None if package.mandate_id is None else str(package.mandate_id),
        "mandate_evaluation_id": None if evaluation is None else str(evaluation.evaluation_id),
        "dry_run_live_crypto_order_id": None if dry_order is None else str(dry_order.live_crypto_order_id),
        "activation_id": None if activation is None else str(activation.activation_id),
        "authority_audit_correlation_id": correlation,
        "human_live_approval_event_used": package.approval_event_id is not None or (activation is not None and activation.approval_event_id is not None),
        "live_submission_record_exists": dry_order is not None and dry_order.status != "DRY_RUN_READY",
        "provider_order_id": None if dry_order is None else dry_order.provider_order_id,
        "position_exists": position_count > 0,
        "reconciliation_count": recon_count,
        "read_only": True,
    }
