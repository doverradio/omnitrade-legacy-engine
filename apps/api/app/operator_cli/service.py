from __future__ import annotations

import asyncio
import ast
from collections import Counter
from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import inspect
import json
import logging
import re
import sys
import textwrap
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
import uuid

from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.models.venue_commissioning_run import VenueCommissioningRun
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.models.validation_run_event import ValidationRunEvent
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle
from app.services.autonomous_cycle.orchestrator import normalize_product_id
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
    MandateVersionModel,
)
from app.services.mandates.lifecycle import (
    _build_version_hash,
    _find_audit_by_idempotency,
    _load_governing_authorized_version,
    apply_mandate_lifecycle_action,
    authorize_mandate_version,
    create_mandate,
    create_mandate_version,
)
from app.services.mandates.validation import (
    validate_autonomy_level,
    validate_mandate_state_transition,
    validate_mandate_version,
)
from app.services.strategies.identity import is_strategy_identity
from app.services.canonical_campaign_binding import (
    CanonicalProvingAccountTransitionRequest,
    CanonicalCampaignBindingRequest,
    CanonicalCampaignStatusTransitionRequest,
    LegacyCampaignTransitionRequest,
    bind_canonical_campaign_runtime as _bind_canonical_campaign_runtime,
    fetch_canonical_campaign_status_transition_audit as _fetch_canonical_campaign_status_transition_audit,
    inspect_canonical_proving_account_transition as _inspect_canonical_proving_account_transition,
    inspect_canonical_campaign_status_transition as _inspect_canonical_campaign_status_transition,
    transition_canonical_proving_account as _transition_canonical_proving_account,
    transition_canonical_campaign_status as _transition_canonical_campaign_status,
    fetch_legacy_campaign_transition_audit as _fetch_legacy_campaign_transition_audit,
    fetch_canonical_campaign_binding_audit as _fetch_canonical_campaign_binding_audit,
    inspect_canonical_campaign_binding as _inspect_canonical_campaign_binding,
    inspect_legacy_campaign_transition as _inspect_legacy_campaign_transition,
    rollback_legacy_campaign_transition as _rollback_legacy_campaign_transition,
    transition_legacy_campaign_to_canonical_successor as _transition_legacy_campaign_to_canonical_successor,
)
from app.services.capital_campaign_orchestration.aggregator_activation import (
    execute_campaign_aggregator_activation as _execute_campaign_aggregator_activation,
    fetch_campaign_aggregator_activation_audit as _fetch_campaign_aggregator_activation_audit,
    inspect_campaign_aggregator_activation as _inspect_campaign_aggregator_activation,
)
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageActivationRequest,
    CanonicalPreviewPackageAuthorizeRequest,
    CanonicalPreviewPackageCreateRequest,
    CanonicalPreviewPackageDryRunRequest,
    CanonicalPreviewPackagePauseRequest,
    CanonicalPreviewPackageRevokeRequest,
    activate_canonical_proving_campaign,
    authorize_canonical_preview_package,
    create_canonical_preview_package,
    get_canonical_preview_package,
    get_canonical_proving_activation_status,
    list_canonical_preview_package_history,
    pause_canonical_proving_activation,
    revoke_canonical_proving_activation,
    run_dry_run_for_canonical_preview_package,
)
from app.services.canonical_paper_cash_causality_audit import (
    CanonicalPaperCashCausalityAuditRequest,
    run_canonical_paper_cash_causality_audit,
)
from app.services.canonical_campaign_authority_audit import (
    CanonicalCampaignAuthorityAuditRequest,
    run_canonical_campaign_authority_audit,
)
from app.services.capital_campaign_orchestration import (
    fetch_campaign_orchestration_history as _fetch_campaign_orchestration_history,
    fetch_campaign_orchestration_readiness as _fetch_campaign_orchestration_readiness,
    fetch_campaign_orchestration_status as _fetch_campaign_orchestration_status,
    run_campaign_orchestration_preview_for_candle,
)
from app.services.capital_campaign_orchestration.service import _eligible_for_orchestration
from app.services.capital_campaign_orchestration.authoritative import (
    _extract_preferred_strategy_identity as _extract_preferred_strategy_identity_hint,
)
from app.services.strategies.identity import build_strategy_identity
from app.services.capital_campaign_domain.service import list_campaign_definitions as _list_campaign_definitions
from app.services.capital_campaign_domain.commissioned_control_plane import (
    backfill_commissioned_ready_metadata,
    get_commissioned_control_plane_status as _get_commissioned_control_plane_status,
    mutate_commissioned_control_plane as _mutate_commissioned_control_plane,
)
from app.services.capital_campaign_domain.commissioned_entry_execution import (
    commission_commissioned_campaign,
    reconcile_commissioned_buy_ownership,
)
from app.services.capital_campaign_domain.activated_commissioned_entry import (
    execute_activated_commissioned_entry as execute_commissioned_entry,
)
from app.services.capital_campaign_domain.commissioned_readiness_preview import (
    generate_commissioned_campaign_preview,
)
from app.services.exchange_connections import refresh_exchange_balances as _refresh_exchange_balances
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignCommissionRequest,
    CommissionedControlPlaneMutationRequest,
    CommissionedEntryExecutionRequest,
    CommissionedOwnershipReconciliationRequest,
    CommissionedReadinessRequest,
)
from app.schemas.live_crypto_orders import LiveCryptoOrderPrepareRequest
from app.services.live_crypto_orders import LiveCryptoOrderService
from app.services.paper.accounting import build_account_snapshot
from app.services.risk import risk_monitor
from app.services.risk.equity_evidence import resolve_equity_risk_evidence
from app.services.risk.risk_context import resolve_effective_risk_policy
from app.services.strategy_outcomes import fetch_strategy_scorecards

logger = logging.getLogger(__name__)


_EXECUTION_FORENSICS_MAX_SINCE_CYCLES = 200
_PROVING_CAP_TARGET_USD = Decimal("5")
_TERMINAL_PACKAGE_STATES = {"COMPLETED", "FAILED_CLOSED", "EXPIRED", "INVALIDATED", "SUPERSEDED"}
_TERMINAL_ACTIVATION_STATES = {"REVOKED", "EXPIRED", "INVALIDATED", "COMPLETED"}
_TERMINAL_LIVE_ORDER_STATES = {"DRY_RUN_READY", "DRY_RUN_BLOCKED", "FILLED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED", "COMPLETED"}
_UNRESOLVED_RECONCILIATION_STATES = {"open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch"}
_KRKN_BTC_INTERVAL = "15m"
_INTERVAL_INGESTION_GRACE_MINUTES = {"15m": 5}
_FIRST_PROFIT_STAGE_ANCHORS: dict[int, float] = {
    1: 75.0,
    2: 99.6,
    3: 99.7,
    4: 99.75,
    5: 99.8,
    6: 99.85,
    7: 99.9,
    8: 99.93,
    9: 99.97,
    10: 100.0,
}
_ORCHESTRATION_READINESS_STATUSES = {"DRAFT", "READY", "ACTIVE", "PAUSED"}
_HISTORICAL_BUY_REPLAY_REQUIRED_GATES = [
    "historical_decision_exists",
    "historical_action_is_buy",
    "source_lineage_complete",
    "strategy_identity_resolved",
    "strategy_authority_compatible",
    "market_data_as_of_time_valid",
    "campaign_definition_compatible",
    "runtime_binding_compatible",
    "product_allowed",
    "provider_allowed",
    "confidence_threshold_passed",
    "lifecycle_allows_open_position",
    "risk_verdict_allows",
    "exact_five_dollar_candidate_possible",
    "fee_adjusted_edge_positive",
    "no_simulated_order_conflict",
]
_BUY_CONFIDENCE_BY_AGGRESSION_MODE: dict[str, Decimal] = {
    "CONSERVATIVE": Decimal("0.70"),
    "BALANCED": Decimal("0.60"),
    "AGGRESSIVE": Decimal("0.50"),
    "MAXIMUM_GOVERNED": Decimal("0.45"),
}
_READY_PACKAGE_STATES = {"READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}
_COMMISSIONED_SUBMISSION_MAY_HAVE_OCCURRED = {
    "BUY_PENDING",
    "BUY_SUBMITTED",
    "BUY_RECONCILIATION_PENDING",
    "RECONCILIATION_REQUIRED",
    "ACTIVE_POSITION",
}
_COMMISSIONED_RECONCILIATION_STATES = {"BUY_RECONCILIATION_PENDING", "RECONCILIATION_REQUIRED", "ACTIVE_POSITION"}
_PROVING_REFRESH_GRACE_SECONDS = 90
_CANONICAL_PROVING_MAX_STATE_TRANSITIONS = 12


def _commission_db_timeout_seconds() -> int:
    return max(1, int(get_settings().operator_db_timeout_seconds))


_commission_stage_sequence_by_key: dict[str, int] = {}


def _log_orchestration_stage(*, operation: str, stage: str, status: str, root_idempotency_key: str, **extra: Any) -> None:
    """Structured log plus unconditional stderr progress line for one stage transition of a
    governed multi-stage orchestration command (canonical-proving-commission, mandate-bootstrap,
    ...). Always writes to stderr directly (not only via `logging`) so operators see forward
    progress even when no logging handler is configured -- the failure mode this exists to
    prevent is the command appearing frozen with zero visible output for its entire runtime.
    Never touches stdout: --json callers must get byte-for-byte the same stdout as before."""
    sequence_key = f"{operation}:{root_idempotency_key}"
    sequence = _commission_stage_sequence_by_key.get(sequence_key, 0) + 1
    _commission_stage_sequence_by_key[sequence_key] = sequence
    detail = " ".join(f"{key}={value}" for key, value in extra.items())
    suffix = f" {detail}" if detail else ""
    logger.info(
        "%s_stage seq=%s stage=%s status=%s root_idempotency_key=%s%s",
        operation.replace("-", "_"),
        sequence,
        stage,
        status,
        root_idempotency_key,
        suffix,
    )
    print(
        f"[{operation}] [{sequence}] stage={stage} status={status} root_idempotency_key={root_idempotency_key}{suffix}",
        file=sys.stderr,
        flush=True,
    )


def _log_commission_stage(*, stage: str, status: str, root_idempotency_key: str, **extra: Any) -> None:
    _log_orchestration_stage(
        operation="canonical-proving-commission",
        stage=stage,
        status=status,
        root_idempotency_key=root_idempotency_key,
        **extra,
    )


async def _await_db_operation(*, stage: str, root_idempotency_key: str, operation, operation_name: str = "canonical-proving-commission", **extra: Any):
    _log_orchestration_stage(operation=operation_name, stage=stage, status="started", root_idempotency_key=root_idempotency_key, **extra)
    try:
        result = await asyncio.wait_for(operation, timeout=_commission_db_timeout_seconds())
    except asyncio.TimeoutError as exc:
        _log_orchestration_stage(operation=operation_name, stage=stage, status="timeout", root_idempotency_key=root_idempotency_key, **extra)
        raise PermissionError(f"database_connection_timeout stage={stage}") from exc
    _log_orchestration_stage(operation=operation_name, stage=stage, status="completed", root_idempotency_key=root_idempotency_key, **extra)
    return result


def _uuid_from_value(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _lineage_ids(source_lineage: Any, *, key: str) -> list[UUID]:
    if not isinstance(source_lineage, dict):
        return []
    raw_values = source_lineage.get(key)
    if not isinstance(raw_values, list):
        return []
    result: list[UUID] = []
    for value in raw_values:
        parsed = _uuid_from_value(value)
        if parsed is not None:
            result.append(parsed)
    return result


def _decision_action(decision: DecisionRecord | None) -> str | None:
    if decision is None:
        return None
    signals = decision.generated_signals if isinstance(decision.generated_signals, list) else []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        action = str(signal.get("action") or "").strip().upper()
        if action:
            return action
    if decision.trade_accepted:
        return "BUY"
    return None


def _commission_phase_idempotency_key(*, root_idempotency_key: str, phase: str, scope: str | None = None) -> str:
    payload = {"root_idempotency_key": root_idempotency_key, "phase": phase, "scope": scope or ""}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _commissioning_identity(*, campaign_id: UUID, version: int, root_idempotency_key: str) -> str:
    payload = {
        "campaign_id": str(campaign_id),
        "version": version,
        "root_idempotency_key": root_idempotency_key,
        "authority": "OPERATOR_COMMISSIONED",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _commissioned_blob_from_definition(definition: CapitalCampaignDefinition | None) -> dict[str, Any]:
    metadata = getattr(definition, "metadata_evidence", {}) if definition is not None else {}
    if not isinstance(metadata, dict):
        return {}
    blob = metadata.get("commissioned_seed_campaign")
    return blob if isinstance(blob, dict) else {}


def _serialize_decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _to_json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return _to_json_compatible(value.value)
    if isinstance(value, BaseModel):
        return _to_json_compatible(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _to_json_compatible(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json_compatible(item) for item in value]

    table = getattr(value, "__table__", None)
    columns = getattr(table, "columns", None)
    if columns is not None:
        return {
            str(column.name): _to_json_compatible(getattr(value, column.name, None))
            for column in columns
        }

    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _normalize_instrument(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _canonical_package_is_forced(package: CanonicalPreviewPackage) -> bool:
    identity = package.market_evidence_identity if isinstance(package.market_evidence_identity, dict) else {}
    return (
        str(identity.get("entry_authority") or "").strip().upper() == "OPERATOR_COMMISSIONED"
        and str(identity.get("entry_reason") or "").strip().upper() == "INITIAL_PROVING_ENTRY"
        and str(identity.get("strategy_override_scope") or "").strip().upper() == "COMMISSIONING_ENTRY_ONLY"
    )


def _preview_reference_price(preview: CryptoOrderPreview) -> Decimal:
    for candidate in (
        getattr(preview, "estimated_average_price", None),
        getattr(preview, "best_ask", None),
        getattr(preview, "best_bid", None),
    ):
        if candidate is not None and Decimal(str(candidate)) > Decimal("0"):
            return Decimal(str(candidate))
    if getattr(preview, "estimated_total_value", None) is not None and getattr(preview, "estimated_base_size", None) is not None:
        base_size = Decimal(str(preview.estimated_base_size))
        if base_size > Decimal("0"):
            return Decimal(str(preview.estimated_total_value)) / base_size
    raise ValueError("reference price unavailable from canonical preview")


def _extract_available_quote_balance(connection: ExchangeConnection) -> Decimal:
    for item in getattr(connection, "balances", []) or []:
        if str(item.get("currency") or "").upper() != "USD":
            continue
        return Decimal(str(item.get("available") or "0"))
    return Decimal("0")


def _approval_is_active(approval_event: LiveApprovalEvent | None, *, now: datetime) -> bool:
    if approval_event is None:
        return False
    if str(approval_event.approval_state or "").strip().lower() != "approved":
        return False
    return approval_event.expires_at is None or approval_event.expires_at > now


def _activation_is_active(activation: CanonicalProvingActivation | None, *, now: datetime) -> bool:
    if activation is None:
        return False
    if str(activation.activation_state or "").strip().upper() != "ACTIVE":
        return False
    return activation.expires_at > now


async def _load_latest_forced_canonical_package(*, db, campaign_id: UUID) -> CanonicalPreviewPackage | None:
    rows = list(
        (
            await db.execute(
                select(CanonicalPreviewPackage)
                .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                .order_by(CanonicalPreviewPackage.generated_at.desc(), CanonicalPreviewPackage.package_id.desc())
            )
        ).scalars().all()
    )
    for item in rows:
        if _canonical_package_is_forced(item):
            return item
    return None


async def _load_latest_approval_for_package(*, db, package: CanonicalPreviewPackage | None) -> LiveApprovalEvent | None:
    if package is None or package.approval_event_id is None:
        return None
    return await db.scalar(
        select(LiveApprovalEvent).where(LiveApprovalEvent.id == package.approval_event_id).limit(1)
    )


async def _load_activation_for_package(*, db, package: CanonicalPreviewPackage | None) -> CanonicalProvingActivation | None:
    if package is None:
        return None
    return await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package.package_id).limit(1)
    )


async def _load_preview_for_package_row(*, db, package: CanonicalPreviewPackage | None) -> CryptoOrderPreview | None:
    if package is None:
        return None
    return await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.crypto_order_preview_id == package.crypto_order_preview_id)
        .limit(1)
    )


async def _load_runtime_campaign_by_identity(*, db, campaign_id: UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))


async def _load_campaign_definition_by_identity(*, db, campaign_id: UUID, version: int) -> CapitalCampaignDefinition | None:
    return await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == version)
        .limit(1)
    )


async def _load_paper_account_by_id(*, db, paper_account_id: UUID) -> PaperAccount | None:
    return await db.scalar(select(PaperAccount).where(PaperAccount.id == paper_account_id).limit(1))


async def _load_profile_by_id(*, db, live_trading_profile_id: UUID) -> LiveTradingProfile | None:
    return await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1))


async def _load_exchange_connection_by_id(*, db, exchange_connection_id: UUID) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.exchange_connection_id == exchange_connection_id)
        .limit(1)
    )


async def _load_active_mandate_for_commissioning(
    *,
    db,
    runtime_campaign_id: int,
    live_trading_profile_id: UUID,
    paper_account_id: UUID,
    provider: str,
    environment: str,
) -> AutonomousCapitalMandate | None:
    normalized_provider = provider.strip().lower()
    normalized_environment = environment.strip().lower()
    rows = list(
        (
            await db.execute(
                select(AutonomousCapitalMandate)
                .where(AutonomousCapitalMandate.live_trading_profile_id == live_trading_profile_id)
                .where(func.lower(AutonomousCapitalMandate.provider) == normalized_provider)
                .where(func.lower(AutonomousCapitalMandate.exchange_environment) == normalized_environment)
                .order_by(AutonomousCapitalMandate.activated_at.desc(), AutonomousCapitalMandate.authorized_at.desc(), AutonomousCapitalMandate.created_at.desc())
            )
        ).scalars().all()
    )
    for mandate in rows:
        if str(mandate.status or "").upper() not in {"ACTIVE", "AUTHORIZED"}:
            continue
        if mandate.capital_campaign_id not in {None, runtime_campaign_id}:
            continue
        if mandate.paper_account_id not in {None, paper_account_id}:
            continue
        return mandate
    return None


async def _diagnose_mandate_resolution_failure(
    *,
    db,
    runtime_campaign_id: int,
    live_trading_profile_id: UUID,
    paper_account_id: UUID,
    provider: str,
    environment: str,
) -> str:
    profile_rows = list(
        (
            await db.execute(
                select(AutonomousCapitalMandate)
                .where(AutonomousCapitalMandate.live_trading_profile_id == live_trading_profile_id)
                .order_by(AutonomousCapitalMandate.activated_at.desc(), AutonomousCapitalMandate.authorized_at.desc(), AutonomousCapitalMandate.created_at.desc())
            )
        ).scalars().all()
    )
    if not profile_rows:
        return "mandate missing"

    normalized_provider = provider.strip().lower()
    normalized_environment = environment.strip().lower()
    scoped_rows = [
        row
        for row in profile_rows
        if str(getattr(row, "provider", "")).strip().lower() == normalized_provider
        and str(getattr(row, "exchange_environment", "")).strip().lower() == normalized_environment
    ]
    if not scoped_rows:
        return "provider/environment mismatch"

    active_rows = [row for row in scoped_rows if str(getattr(row, "status", "")).strip().upper() in {"ACTIVE", "AUTHORIZED"}]
    if not active_rows:
        return "mandate not authorized or inactive"

    identity_rows = [
        row
        for row in active_rows
        if getattr(row, "capital_campaign_id", None) in {None, runtime_campaign_id}
        and getattr(row, "paper_account_id", None) in {None, paper_account_id}
    ]
    if not identity_rows:
        return "campaign/profile/account mismatch"

    return "mandate missing"


async def mandate_identity_diagnosis(
    *,
    campaign_id: UUID,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
) -> dict[str, Any]:
    """Read-only inspection of the exact identities canonical_proving_commission_bundle()
    compares during mandate resolution. Performs SELECT statements only -- no db.add, flush,
    or commit -- and calls the same production lookup/diagnosis helpers the commissioning
    flow uses, so this can never disagree with what a real commissioning run would see."""
    async with AsyncSessionLocal() as db:
        runtime = await _load_runtime_campaign_by_identity(db=db, campaign_id=campaign_id)
        runtime_campaign_id = runtime.id if runtime is not None else None
        profile = await _load_profile_by_id(db=db, live_trading_profile_id=live_trading_profile_id)

        mandate_rows = list(
            (
                await db.execute(
                    select(AutonomousCapitalMandate)
                    .where(AutonomousCapitalMandate.live_trading_profile_id == live_trading_profile_id)
                    .order_by(AutonomousCapitalMandate.activated_at.desc(), AutonomousCapitalMandate.authorized_at.desc(), AutonomousCapitalMandate.created_at.desc())
                )
            ).scalars().all()
        )

        normalized_provider = provider.strip().lower()
        normalized_environment = environment.strip().lower()
        mandates = []
        for row in mandate_rows:
            governing_version = await _load_authorized_mandate_version(db=db, mandate_id=row.mandate_id)
            mandates.append(
                {
                    "mandate_id": str(row.mandate_id),
                    "status": row.status,
                    "provider": row.provider,
                    "exchange_environment": row.exchange_environment,
                    "exchange_connection_id": str(row.exchange_connection_id) if row.exchange_connection_id is not None else None,
                    "capital_campaign_id": row.capital_campaign_id,
                    "paper_account_id": str(row.paper_account_id) if row.paper_account_id is not None else None,
                    "provider_environment_matches": (
                        str(row.provider or "").strip().lower() == normalized_provider
                        and str(row.exchange_environment or "").strip().lower() == normalized_environment
                    ),
                    "status_active": str(row.status or "").strip().upper() in {"ACTIVE", "AUTHORIZED"},
                    "campaign_id_matches": row.capital_campaign_id in {None, runtime_campaign_id},
                    "paper_account_id_matches": row.paper_account_id in {None, paper_account_id},
                    "governing_mandate_version_id": str(governing_version.mandate_version_id) if governing_version is not None else None,
                    "governing_mandate_version_number": governing_version.version_number if governing_version is not None else None,
                    "governing_version_is_authorized": bool(governing_version.is_authorized) if governing_version is not None else None,
                    "governing_version_is_active": bool(governing_version.is_active) if governing_version is not None else None,
                }
            )

        resolved_mandate = await _load_active_mandate_for_commissioning(
            db=db,
            runtime_campaign_id=runtime_campaign_id,
            live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id,
            provider=provider,
            environment=environment,
        )
        diagnosis = (
            None
            if resolved_mandate is not None
            else await _diagnose_mandate_resolution_failure(
                db=db,
                runtime_campaign_id=runtime_campaign_id,
                live_trading_profile_id=live_trading_profile_id,
                paper_account_id=paper_account_id,
                provider=provider,
                environment=environment,
            )
        )

        return {
            "campaign_id": str(campaign_id),
            "campaign_found": runtime is not None,
            "runtime_campaign_id": runtime_campaign_id,
            "paper_account_id": str(paper_account_id),
            "live_trading_profile_id": str(live_trading_profile_id),
            "provider": provider,
            "environment": environment,
            "profile_found": profile is not None,
            "profile_paper_account_id": str(profile.paper_account_id) if profile is not None and profile.paper_account_id is not None else None,
            "profile_paper_account_id_matches": profile is not None and getattr(profile, "paper_account_id", None) == paper_account_id,
            "mandates": mandates,
            "would_resolve_mandate_id": str(resolved_mandate.mandate_id) if resolved_mandate is not None else None,
            "resolution_would_fail_reason": diagnosis,
        }


class MandateBootstrapStageError(RuntimeError):
    """Raised when mandate_bootstrap() fails partway through its governed stage sequence.
    Carries exactly which stage failed and which stages already completed (and therefore
    do not need to be repeated -- rerunning mandate_bootstrap() with the same
    idempotency_key resumes from here via each stage's own idempotency-key dedup) so
    operator-facing output never just says "it failed" without saying where."""

    def __init__(
        self,
        *,
        stage: str,
        completed_stages: list[dict[str, Any]],
        root_idempotency_key: str,
        audit_correlation_id: uuid.UUID,
        original: Exception,
    ) -> None:
        self.stage = stage
        self.completed_stages = completed_stages
        self.root_idempotency_key = root_idempotency_key
        self.audit_correlation_id = audit_correlation_id
        self.original = original
        completed_names = ", ".join(item["stage"] for item in completed_stages) or "none"
        super().__init__(
            f"mandate_bootstrap stopped at stage={stage} (completed: {completed_names}; "
            f"root_idempotency_key={root_idempotency_key}): {original}"
        )


async def mandate_bootstrap(
    *,
    owner_actor_id: str,
    autonomy_level: str,
    provider: str,
    environment: str,
    exchange_connection_id: UUID,
    live_trading_profile_id: UUID,
    paper_account_id: UUID | None,
    capital_campaign_id: int | None,
    mandate_expires_at: datetime | None,
    base_currency: str,
    authorized_capital_usd: Decimal,
    max_order_notional_usd: Decimal,
    max_open_exposure_usd: Decimal,
    max_daily_deployed_usd: Decimal,
    max_daily_realized_loss_usd: Decimal,
    max_campaign_drawdown_usd: Decimal,
    max_consecutive_losses: int,
    position_limit: int,
    price_evidence_max_age_seconds: int,
    max_slippage_bps: Decimal,
    max_fee_bps: Decimal,
    allowed_products: tuple[str, ...],
    allowed_order_sides: tuple[str, ...],
    allowed_strategy_versions: tuple[str, ...],
    approval_policy: str,
    entry_policy: dict[str, Any],
    exit_policy: dict[str, Any],
    cooldown_policy: dict[str, Any],
    operating_schedule: dict[str, Any],
    reconciliation_policy: dict[str, Any],
    kill_switch_policy: dict[str, Any],
    owner_acknowledgements: dict[str, Any],
    authorization_evidence_summary: dict[str, Any],
    authorization_method: str,
    authorization_evidence: dict[str, Any],
    deterministic_explanation: dict[str, Any],
    authorization_expires_at: datetime | None,
    actor: str,
    reason: str,
    idempotency_key: str,
    audit_correlation_id: UUID | None,
    confirm: bool,
) -> dict[str, Any]:
    """Governed orchestration of the existing, unmodified mandate lifecycle: create_mandate()
    -> create_mandate_version() -> apply_mandate_lifecycle_action(SUBMIT_FOR_AUTHORIZATION) ->
    authorize_mandate_version() -> apply_mandate_lifecycle_action(ACTIVATE). These are exactly
    the same service-layer functions app/api/routes/autonomous_capital_mandates.py calls --
    this function reimplements no business logic, validation, or governance rule; it only
    sequences the existing calls and surfaces per-stage progress/failure to the operator.

    Every stage derives its own idempotency key from `idempotency_key` (f"{root}:<stage>")
    and shares one `audit_correlation_id`, so a full rerun with the same `idempotency_key`
    resumes/no-ops at whatever stage already completed instead of creating duplicate
    mandates, versions, authorizations, or audit rows."""
    if not confirm:
        raise PermissionError("confirm=true is required for mandate bootstrap")

    root_idempotency_key = idempotency_key
    correlation_id = audit_correlation_id or uuid.uuid4()
    stages: list[dict[str, Any]] = []
    current_stage = "create_mandate"

    async with AsyncSessionLocal() as db:
        try:
            mandate = await _await_db_operation(
                operation_name="mandate-bootstrap",
                stage=current_stage,
                root_idempotency_key=root_idempotency_key,
                operation=create_mandate(
                    db=db,
                    owner_actor_id=owner_actor_id,
                    autonomy_level=autonomy_level,
                    provider=provider,
                    exchange_environment=environment,
                    exchange_connection_id=exchange_connection_id,
                    live_trading_profile_id=live_trading_profile_id,
                    paper_account_id=paper_account_id,
                    capital_campaign_id=capital_campaign_id,
                    expires_at=mandate_expires_at,
                    actor=actor,
                    idempotency_key=f"{root_idempotency_key}:create-mandate",
                    reason=reason,
                ),
            )
            stages.append({"stage": current_stage, "mandate_id": str(mandate.mandate_id), "status": mandate.status})

            current_stage = "create_mandate_version"
            version = await _await_db_operation(
                operation_name="mandate-bootstrap",
                stage=current_stage,
                root_idempotency_key=root_idempotency_key,
                operation=create_mandate_version(
                    db=db,
                    request=MandateVersionCreateRequest(
                        mandate_id=mandate.mandate_id,
                        actor=actor,
                        base_currency=base_currency,
                        authorized_capital_usd=authorized_capital_usd,
                        max_order_notional_usd=max_order_notional_usd,
                        max_open_exposure_usd=max_open_exposure_usd,
                        max_daily_deployed_usd=max_daily_deployed_usd,
                        max_daily_realized_loss_usd=max_daily_realized_loss_usd,
                        max_campaign_drawdown_usd=max_campaign_drawdown_usd,
                        max_consecutive_losses=max_consecutive_losses,
                        position_limit=position_limit,
                        price_evidence_max_age_seconds=price_evidence_max_age_seconds,
                        max_slippage_bps=max_slippage_bps,
                        max_fee_bps=max_fee_bps,
                        allowed_products=tuple(allowed_products),
                        allowed_order_sides=tuple(allowed_order_sides),
                        allowed_strategy_versions=tuple(allowed_strategy_versions),
                        entry_policy=entry_policy,
                        exit_policy=exit_policy,
                        cooldown_policy=cooldown_policy,
                        operating_schedule=operating_schedule,
                        approval_policy=approval_policy,
                        reconciliation_policy=reconciliation_policy,
                        kill_switch_policy=kill_switch_policy,
                        owner_acknowledgements=owner_acknowledgements,
                        authorization_evidence_summary=authorization_evidence_summary,
                        idempotency_key=f"{root_idempotency_key}:create-version",
                        audit_correlation_id=correlation_id,
                    ),
                ),
            )
            stages.append(
                {
                    "stage": current_stage,
                    "mandate_version_id": str(version.mandate_version_id),
                    "version_number": version.version_number,
                }
            )

            current_stage = "submit_for_authorization"
            submitted = await _await_db_operation(
                operation_name="mandate-bootstrap",
                stage=current_stage,
                root_idempotency_key=root_idempotency_key,
                operation=apply_mandate_lifecycle_action(
                    db=db,
                    request=MandateLifecycleActionRequest(
                        mandate_id=mandate.mandate_id,
                        actor=actor,
                        action="SUBMIT_FOR_AUTHORIZATION",
                        reason=reason,
                        idempotency_key=f"{root_idempotency_key}:submit-for-authorization",
                        audit_correlation_id=correlation_id,
                        software_build_version=None,
                    ),
                ),
            )
            stages.append({"stage": current_stage, "mandate_id": str(submitted.mandate_id), "status": submitted.status})

            current_stage = "authorize_version"
            authorization = await _await_db_operation(
                operation_name="mandate-bootstrap",
                stage=current_stage,
                root_idempotency_key=root_idempotency_key,
                operation=authorize_mandate_version(
                    db=db,
                    request=MandateAuthorizationRequest(
                        mandate_id=mandate.mandate_id,
                        mandate_version_id=version.mandate_version_id,
                        actor=actor,
                        authorization_method=authorization_method,
                        owner_acknowledgements=owner_acknowledgements,
                        authorization_evidence=authorization_evidence,
                        deterministic_explanation=deterministic_explanation,
                        expires_at=authorization_expires_at,
                        idempotency_key=f"{root_idempotency_key}:authorize",
                        audit_correlation_id=correlation_id,
                    ),
                ),
            )
            stages.append(
                {
                    "stage": current_stage,
                    "mandate_authorization_id": str(authorization.mandate_authorization_id),
                    "authorization_state": authorization.authorization_state,
                }
            )

            current_stage = "activate_mandate"
            activated = await _await_db_operation(
                operation_name="mandate-bootstrap",
                stage=current_stage,
                root_idempotency_key=root_idempotency_key,
                operation=apply_mandate_lifecycle_action(
                    db=db,
                    request=MandateLifecycleActionRequest(
                        mandate_id=mandate.mandate_id,
                        actor=actor,
                        action="ACTIVATE",
                        reason=reason,
                        idempotency_key=f"{root_idempotency_key}:activate",
                        audit_correlation_id=correlation_id,
                        software_build_version=None,
                    ),
                ),
            )
            stages.append({"stage": current_stage, "mandate_id": str(activated.mandate_id), "status": activated.status})
        except Exception as exc:
            await db.rollback()
            _log_orchestration_stage(
                operation="mandate-bootstrap",
                stage=current_stage,
                status="failed",
                root_idempotency_key=root_idempotency_key,
                error=str(exc),
            )
            raise MandateBootstrapStageError(
                stage=current_stage,
                completed_stages=stages,
                root_idempotency_key=root_idempotency_key,
                audit_correlation_id=correlation_id,
                original=exc,
            ) from exc

        governing_version = await db.get(AutonomousCapitalMandateVersion, version.mandate_version_id)

    return {
        "mandate_id": str(mandate.mandate_id),
        "mandate_version_id": str(version.mandate_version_id),
        "mandate_version_number": version.version_number,
        "mandate_authorization_id": str(authorization.mandate_authorization_id),
        "status": activated.status,
        "capital_campaign_id": activated.capital_campaign_id,
        "governing_version_is_authorized": bool(governing_version.is_authorized) if governing_version is not None else None,
        "governing_version_is_active": bool(governing_version.is_active) if governing_version is not None else None,
        "audit_correlation_id": str(correlation_id),
        "root_idempotency_key": root_idempotency_key,
        "stages": stages,
    }


_MANDATE_BOOTSTRAP_EXPORT_DEFINITION_NOTES = (
    "capital_campaign_definitions fields above are the campaign's own operational bounds "
    "(position sizing, exposure, drawdown), not mandate-bootstrap risk limits. They use "
    "different units/semantics and are not automatically substitutable for "
    "authorized_capital_usd, max_order_notional_usd, max_open_exposure_usd, "
    "max_campaign_drawdown_usd, or any other mandate-bootstrap risk field."
)


def _decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


# Stage 4: allowed_strategy_versions. Per the Strategy Identity Architecture Review,
# capital_campaigns.strategy_id is not the canonical strategy authority for the modern
# definition-pinned campaign path, and no other campaign-scoped database field can
# authoritatively derive allowed_strategy_versions either -- runtime execution resolves it
# dynamically every cycle by intersecting the mandate version's own allowlist with whichever
# Strategy row is currently globally active (app/services/autonomous_cycle/orchestrator.py
# ::_run_approved_strategy). This field is therefore always OWNER_INPUT_REQUIRED, regardless
# of campaign state -- it is never computed from this function's own findings.
_MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD: dict[str, Any] = {
    "classification": "OWNER_INPUT_REQUIRED",
    "value": None,
    "source": None,
    "notes": (
        "No campaign-scoped database field can authoritatively derive allowed_strategy_versions "
        "(capital_campaigns.strategy_id is not the canonical strategy authority for the modern "
        "definition-pinned campaign path). Runtime execution later intersects the mandate "
        "version's allowed_strategy_versions allowlist with whichever Strategy row is currently "
        "globally active -- see strategy_evidence for informational, non-authoritative candidates "
        "only; none of it may be converted into this value."
    ),
}

_MANDATE_BOOTSTRAP_EXPORT_LEGACY_STRATEGY_REFERENCE_NOTE = (
    "capital_campaigns.strategy_id is a legacy field; the modern canonical execution pipeline "
    "does not use it as strategy authority."
)
_MANDATE_BOOTSTRAP_EXPORT_GLOBAL_ACTIVE_STRATEGY_NOTE = (
    "Strategy.is_active is a system-wide activation flag, not campaign-scoped, and may change "
    "independently of this campaign. Multiple rows (if present) are listed without an implicit "
    "winner -- none is selected as newest, active, or best."
)
_MANDATE_BOOTSTRAP_EXPORT_METADATA_HINT_NOTE = (
    "Unstructured advisory evidence from capital_campaign_definitions.metadata_evidence -- not "
    "authoritative."
)
_MANDATE_BOOTSTRAP_EXPORT_PACKAGE_CONTINUITY_NOTE = (
    "Historical continuity evidence from a prior canonical preview package for this exact "
    "campaign UUID and definition version. Must not auto-populate allowed_strategy_versions."
)
_MANDATE_BOOTSTRAP_EXPORT_STRATEGY_EVIDENCE_NOTE = (
    "All evidence below is informational and non-authoritative. It exists only to help the "
    "owner decide allowed_strategy_versions; none of it is ever converted into that value."
)

# Mirrors canonical_preview_package.py's own package_state lifecycle states that represent a
# genuinely-issued (not failed/expired/superseded) package -- identical to the set
# capital_campaign_orchestration/authoritative.py::_load_campaign_strategy_authority uses for
# continuity evidence, so this export's package selection matches that existing logic exactly.
_MANDATE_BOOTSTRAP_EXPORT_CONTINUITY_PACKAGE_STATES = ("READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED")


def _strategy_safe_summary(strategy: Strategy) -> dict[str, Any]:
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "slug": strategy.slug,
        "module_version": strategy.module_version,
        "is_active": bool(strategy.is_active),
        "canonical_identity": build_strategy_identity(slug=strategy.slug, module_version=strategy.module_version),
    }


_MANDATE_BOOTSTRAP_EXPORT_LIVE_PROFILE_GOVERNANCE_FIELDS = (
    "operating_mode",
    "lifecycle_state",
    "approval_state",
    "live_opt_in",
    "human_approval_recorded",
    "governance_approved",
    "risk_authority_model",
    "autonomous_capital_allocation",
    "autonomous_strategy_evolution",
    "automatic_promotion_enabled",
)


_KNOWN_EXCHANGE_PROVIDERS = ("coinbase_advanced", "kraken_spot")

_MANDATE_BOOTSTRAP_EXPORT_CAPABILITY_FLAGS_NOTES = (
    "trading_enabled/withdrawals_enabled/supports_market_orders/supports_limit_orders are "
    "not derivable from the current exchange_connections schema -- no column or documented "
    "mapping from api_permissions exists for these flags. Reported null rather than guessed."
)


def _parse_exchange_label(label: str | None) -> tuple[str | None, str | None]:
    """Deterministic inverse of canonical_campaign_binding._exchange_label(): "{provider}"
    for production, "{provider}_sandbox" for sandbox. Returns (None, None) if the label is
    absent or does not match this exact, known format -- never guesses at what an
    unrecognized label might mean."""
    if not label:
        return None, None
    normalized = label.strip().lower()
    if normalized.endswith("_sandbox"):
        provider, environment = normalized[: -len("_sandbox")], "sandbox"
    else:
        provider, environment = normalized, "production"
    if provider not in _KNOWN_EXCHANGE_PROVIDERS:
        return None, None
    return provider, environment


def _exchange_connection_candidate_summary(connection: ExchangeConnection) -> dict[str, Any]:
    """Secret-safe summary for candidate listing -- never api keys, secrets, passphrases,
    signatures, tokens, or balances."""
    return {
        "id": str(connection.exchange_connection_id),
        "provider": connection.provider,
        "environment": connection.environment,
        "connection_status": connection.status,
    }


def _live_trading_profile_candidate_summary(profile: LiveTradingProfile) -> dict[str, Any]:
    """Secret-safe summary for candidate listing -- LiveTradingProfile has no
    secret-bearing columns, but this stays deliberately narrower than the full
    governance-field payload emitted only for a uniquely resolved profile."""
    return {
        "id": str(profile.id),
        "operating_mode": profile.operating_mode,
        "lifecycle_state": profile.lifecycle_state,
        "approval_state": profile.approval_state,
    }


# Stage 5: the remaining mandate-bootstrap owner-input manifest. Every one of these
# fields is a genuine mandate_bootstrap()/CLI parameter (apps/api/app/operator_cli/
# main.py's mandate-bootstrap subparser, cross-checked against mandate_bootstrap()'s
# own signature) that Stages 1-4 do not already resolve. None of them varies with
# campaign state -- they are fixed properties of the mandate-bootstrap contract itself,
# not this specific campaign -- so this is a single static manifest reused unchanged
# whether the campaign is found or not (per-call copies are made via
# _static_mandate_input_fields() so no caller can mutate the shared constant).
#
# capital_campaign_id, campaign_uuid, paper_account_id, base_currency,
# paper_account_asset_class, paper_account_is_active, live_trading_profile_id,
# exchange_connection_id, exchange_provider, exchange_environment, and
# allowed_strategy_versions are already covered by Stages 1-4 and are deliberately not
# duplicated here -- mandate_bootstrap()'s own paper_account_id/exchange_connection_id/
# live_trading_profile_id/provider/environment/base_currency parameters are exactly the
# values those existing fields already resolve.
_MANDATE_BOOTSTRAP_EXPORT_STATIC_MANDATE_FIELDS: dict[str, dict[str, Any]] = {
    "owner_actor_id": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No table stores an actor identity equivalent to mandate owner_actor_id for this campaign; must be supplied by the owner.",
    },
    "autonomy_level": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "autonomy_level is stored only on AutonomousCapitalMandate/AutonomousCapitalMandateEvaluation rows, never per-campaign or per-owner -- there is no prior mandate to read it from for a first bootstrap.",
    },
    "authorized_capital_usd": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No campaign-scoped USD-notional capital limit exists outside AutonomousCapitalMandateVersion rows themselves; capital_campaign_definitions' own limits use different units/semantics and are not substitutable.",
    },
    "max_order_notional_usd": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative USD-notional order limit exists anywhere outside mandate version rows themselves.",
    },
    "max_open_exposure_usd": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative USD-notional exposure limit exists anywhere outside mandate version rows themselves.",
    },
    "max_daily_deployed_usd": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative USD-notional daily-deployment limit exists anywhere outside mandate version rows themselves.",
    },
    "max_daily_realized_loss_usd": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative USD-notional daily-loss limit exists anywhere outside mandate version rows themselves.",
    },
    "max_campaign_drawdown_usd": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "capital_campaign_definitions.maximum_drawdown is a related but not substitutable value (different unit/semantics, no existing coercion); no authoritative USD-notional mandate drawdown limit exists elsewhere.",
    },
    "max_consecutive_losses": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative source exists; this is a mandate-specific risk limit decided by the owner.",
    },
    "position_limit": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative source exists; this is a mandate-specific risk limit decided by the owner.",
    },
    "price_evidence_max_age_seconds": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "The CLI has no default and requires this explicitly. Similarly-named app-config settings (e.g. live_crypto_price_max_age_seconds) exist for unrelated dry-run/preview paths and are not wired to this field -- they must not be treated as its source.",
    },
    "max_slippage_bps": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative source exists; this is a mandate-specific risk limit decided by the owner.",
    },
    "max_fee_bps": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative source exists; this is a mandate-specific risk limit decided by the owner.",
    },
    "allowed_products": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "capital_campaign_definitions.allowed_instruments/allowed_asset_classes use a different taxonomy (asset-class/venue/instrument vs. exact product IDs like BTC-USD) with no existing mapping -- not substitutable.",
    },
    "allowed_order_sides": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No model represents this concept at all; must be supplied by the owner.",
    },
    "approval_policy": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No deterministic derivation exists (it is a policy choice, not a mechanical function of autonomy_level -- a LEVEL_2 mandate may still deliberately choose HUMAN_REQUIRED).",
    },
    "entry_policy": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No schema field holds this; must be supplied by the owner as part of --policy-bundle-json.",
    },
    "exit_policy": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No schema field holds this; must be supplied by the owner as part of --policy-bundle-json.",
    },
    "cooldown_policy": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No schema field holds this; must be supplied by the owner as part of --policy-bundle-json.",
    },
    "operating_schedule": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No schema field holds this; must be supplied by the owner as part of --policy-bundle-json.",
    },
    "reconciliation_policy": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No schema field holds this; must be supplied by the owner as part of --policy-bundle-json.",
    },
    "kill_switch_policy": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No schema field holds this; must be supplied by the owner as part of --policy-bundle-json.",
    },
    "owner_acknowledgements": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "This is the record of a genuine, fresh authorization act by the owner; no record can supply it in advance.",
    },
    "authorization_evidence_summary": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "Genuine authorization evidence for this specific mandate; not derivable from any prior mandate, approval, or campaign record.",
    },
    "authorization_evidence": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "Genuine authorization evidence for this specific mandate; not derivable from any prior mandate, approval, or campaign record.",
    },
    "deterministic_explanation": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "Genuine authorization evidence for this specific mandate; not derivable from any prior mandate, approval, or campaign record.",
    },
    "authorization_method": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative source exists; the owner must state how this authorization was obtained.",
    },
    "actor": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": (
            "The CLI offers 'operator:human' as a convenience default matching the "
            "repository-wide convention used across all operator commands -- it is not a "
            "resolved identity for this specific mandate and must be confirmed or "
            "overridden by the actual operator."
        ),
    },
    "reason": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "No authoritative source exists; a free-text operator justification is required.",
    },
    "idempotency_key": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "Required by the current mandate-bootstrap CLI contract with no default; the operator must choose this value.",
    },
    "confirm": {
        "classification": "OWNER_INPUT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "A boolean safety gate requiring explicit operator confirmation; mandate_bootstrap() raises PermissionError unless this is affirmatively supplied.",
    },
    "audit_correlation_id": {
        "classification": "RUNTIME_DERIVED",
        "value": None,
        "source": None,
        "notes": (
            "Dual behavior in the current contract: if --audit-correlation-id is omitted, "
            "mandate_bootstrap() generates a fresh UUID4 internally at execution time "
            "(RUNTIME_DERIVED); if explicitly supplied, that exact operator-provided value "
            "is used instead (operator-supplied). This export does not generate or observe "
            "a value either way -- it only describes this behavior."
        ),
    },
    "mandate_expires_at": {
        "classification": "NOT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "Optional in the current contract (CLI default is omitted/None); a mandate created without this simply has no expiration.",
    },
    "authorization_expires_at": {
        "classification": "NOT_REQUIRED",
        "value": None,
        "source": None,
        "notes": "Optional in the current contract (CLI default is omitted/None); an authorization created without this simply has no expiration.",
    },
}


def _static_mandate_input_fields() -> dict[str, dict[str, Any]]:
    """Fresh, independently-mutable copies of the Stage 5 static manifest -- never the
    shared module-level dicts themselves."""
    return {name: dict(field) for name, field in _MANDATE_BOOTSTRAP_EXPORT_STATIC_MANDATE_FIELDS.items()}


def _owner_input_summary(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Informational only -- computed purely from the already-assembled fields dict, and
    never itself affects executable/overall_status. Counts only OWNER_INPUT_REQUIRED
    fields (DATABASE_DERIVED and NOT_REQUIRED fields are excluded by construction, since
    neither classification is ever OWNER_INPUT_REQUIRED)."""
    owner_required = {name: field for name, field in fields.items() if field.get("classification") == "OWNER_INPUT_REQUIRED"}
    unresolved_fields = sorted(name for name, field in owner_required.items() if field.get("value") is None)
    total_required = len(owner_required)
    unresolved_count = len(unresolved_fields)
    return {
        "total_required": total_required,
        "resolved_count": total_required - unresolved_count,
        "unresolved_count": unresolved_count,
        "unresolved_fields": unresolved_fields,
    }


# Stage 6: the owner decision worksheet. One static, deterministic entry per
# OWNER_INPUT_REQUIRED field, describing HOW to supply it -- never WHAT to supply.
# accepted_values is populated ONLY where an actual repository validator or DB CHECK
# constraint defines a closed set (autonomy_level, approval_policy); every numeric field's
# constraint is copied verbatim from AutonomousCapitalMandateVersion's CheckConstraints
# and/or app/services/mandates/validation.py::validate_mandate_version; every JSON policy
# field's "structure" is exactly what apps/api/app/operator_cli/main.py::
# _parse_mandate_policy_bundle enforces today (a JSON object, no sub-keys) -- nothing here
# is invented. current_value is always None; example_format is placeholder syntax only,
# never a real (or realistic-looking) authorization value.
_MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES: dict[str, dict[str, Any]] = {
    "owner_actor_id": {
        "input_type": "text",
        "accepted_values": None,
        "example_format": "<owner-actor-identifier>",
        "description": "Identifies the owner authorizing this mandate for audit purposes.",
        "source_contract": "AutonomousCapitalMandate.owner_actor_id (Text, no CHECK constraint); mandate_bootstrap() required parameter",
    },
    "autonomy_level": {
        "input_type": "enum",
        "accepted_values": ["LEVEL_0", "LEVEL_1", "LEVEL_2", "LEVEL_3"],
        "example_format": "<one of accepted_values>",
        "description": "The mandate's autonomy tier.",
        "source_contract": "AUTONOMY_LEVELS (app/services/mandates/contracts.py) + CHECK ck_ac_mandates_autonomy_level",
    },
    "authorized_capital_usd": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<positive decimal>",
        "description": "Total USD capital this mandate authorizes.",
        "source_contract": "CHECK ck_ac_mandate_versions_authorized_capital (> 0); validate_mandate_version()",
    },
    "max_order_notional_usd": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<positive decimal>",
        "description": "Maximum USD notional per order; must not exceed authorized_capital_usd.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_order_notional (> 0); validate_mandate_version() (<= authorized_capital_usd)",
    },
    "max_open_exposure_usd": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<positive decimal>",
        "description": "Maximum total open USD exposure; must not exceed authorized_capital_usd.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_open_exposure (> 0); validate_mandate_version() (<= authorized_capital_usd)",
    },
    "max_daily_deployed_usd": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<positive decimal>",
        "description": "Maximum USD deployed in a single day; must not exceed authorized_capital_usd.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_daily_deployed (> 0); validate_mandate_version() (<= authorized_capital_usd)",
    },
    "max_daily_realized_loss_usd": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<non-negative decimal>",
        "description": "Maximum realized loss permitted in a single day.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_daily_loss (>= 0)",
    },
    "max_campaign_drawdown_usd": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<non-negative decimal>",
        "description": "Maximum campaign drawdown in USD.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_drawdown (>= 0)",
    },
    "max_consecutive_losses": {
        "input_type": "integer",
        "accepted_values": None,
        "example_format": "<non-negative integer>",
        "description": "Maximum consecutive losses before this mandate's risk limits engage.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_consecutive_losses (>= 0)",
    },
    "position_limit": {
        "input_type": "integer",
        "accepted_values": None,
        "example_format": "<non-negative integer>",
        "description": "Maximum concurrent open positions.",
        "source_contract": "CHECK ck_ac_mandate_versions_position_limit (>= 0); validate_mandate_version()",
    },
    "price_evidence_max_age_seconds": {
        "input_type": "integer",
        "accepted_values": None,
        "example_format": "<positive integer>",
        "description": "Maximum age, in seconds, of price evidence this mandate treats as fresh.",
        "source_contract": "CHECK ck_ac_mandate_versions_price_freshness (> 0); validate_mandate_version()",
    },
    "max_slippage_bps": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<non-negative decimal>",
        "description": "Maximum allowed slippage, in basis points.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_slippage (>= 0)",
    },
    "max_fee_bps": {
        "input_type": "decimal",
        "accepted_values": None,
        "example_format": "<non-negative decimal>",
        "description": "Maximum allowed fee, in basis points.",
        "source_contract": "CHECK ck_ac_mandate_versions_max_fee (>= 0)",
    },
    "allowed_products": {
        "input_type": "csv_list",
        "accepted_values": None,
        "example_format": "<PRODUCT-1>,<PRODUCT-2>",
        "description": "Comma-separated list of exact product identifiers this mandate may trade; must be non-empty. No fixed product enum is enforced by the current validator.",
        "source_contract": "validate_mandate_version() (empty_allowed_products)",
    },
    "allowed_order_sides": {
        "input_type": "csv_list",
        "accepted_values": None,
        "example_format": "<SIDE-1>,<SIDE-2>",
        "description": "Comma-separated list of order sides this mandate may take; must be non-empty. No fixed side enum is enforced by the current validator.",
        "source_contract": "validate_mandate_version() (empty_allowed_order_sides)",
    },
    "allowed_strategy_versions": {
        "input_type": "csv_list",
        "accepted_values": None,
        "example_format": "<strategy-slug>@<module-version>",
        "description": (
            "Comma-separated list of canonical strategy identities this mandate permits. "
            "Per the Strategy Identity Architecture Review, no campaign-scoped record can "
            "supply this -- see strategy_evidence for informational-only candidates, which "
            "must not be copied into this value even when they agree with each other."
        ),
        "source_contract": "validate_mandate_version() (non-empty; each entry via is_strategy_identity()); app/services/strategies/identity.py (exactly one '@', non-empty slug and version)",
    },
    "approval_policy": {
        "input_type": "enum",
        "accepted_values": ["HUMAN_REQUIRED", "MANDATE_ALLOWED"],
        "example_format": "<one of accepted_values>",
        "description": "Whether this mandate's own policy allows autonomous evaluation to proceed without a human in the loop.",
        "source_contract": "CHECK ck_ac_mandate_versions_approval_policy; validate_mandate_version()",
    },
    "entry_policy": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured entry policy, supplied as part of --policy-bundle-json.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "exit_policy": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured exit policy, supplied as part of --policy-bundle-json.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "cooldown_policy": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured cooldown policy, supplied as part of --policy-bundle-json.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "operating_schedule": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured operating schedule, supplied as part of --policy-bundle-json.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "reconciliation_policy": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured reconciliation policy, supplied as part of --policy-bundle-json.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "kill_switch_policy": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured kill-switch policy, supplied as part of --policy-bundle-json.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "owner_acknowledgements": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured record of the owner's acknowledgements, supplied as part of --policy-bundle-json. A genuine, fresh act by the owner -- never derivable from any record.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "authorization_evidence_summary": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured summary of authorization evidence, supplied as part of --policy-bundle-json. A genuine, fresh act by the owner -- never derivable from any record.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "authorization_evidence": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured authorization evidence, supplied as part of --policy-bundle-json. A genuine, fresh act by the owner -- never derivable from any record.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "deterministic_explanation": {
        "input_type": "json_object",
        "accepted_values": None,
        "example_format": '{"<key>": "<value>"}',
        "description": "Structured deterministic explanation, supplied as part of --policy-bundle-json. A genuine, fresh act by the owner -- never derivable from any record.",
        "source_contract": "_parse_mandate_policy_bundle() (app/operator_cli/main.py) -- requires a JSON object; no sub-keys are enforced by the current parser",
    },
    "authorization_method": {
        "input_type": "text",
        "accepted_values": None,
        "example_format": "<authorization-method-identifier>",
        "description": "Free-text description of how this authorization was obtained. No enum is enforced by the current schema.",
        "source_contract": "AutonomousCapitalMandateAuthorization.authorization_method (Text, no CHECK constraint)",
    },
    "actor": {
        "input_type": "text",
        "accepted_values": None,
        "example_format": "<actor-identifier>",
        "description": (
            "Identifies who is performing this bootstrap action, for audit logging. The "
            "CLI offers 'operator:human' only as a convenience default matching a "
            "repository-wide convention -- it is not a resolved identity and must be "
            "confirmed or overridden."
        ),
        "source_contract": "mandate_bootstrap() required parameter (app/operator_cli/service.py)",
    },
    "reason": {
        "input_type": "text",
        "accepted_values": None,
        "example_format": "<free-text-justification>",
        "description": "Free-text justification recorded in the audit log.",
        "source_contract": "mandate_bootstrap() required parameter (app/operator_cli/service.py); apply_mandate_lifecycle_action() reason",
    },
    "idempotency_key": {
        "input_type": "text",
        "accepted_values": None,
        "example_format": "<unique-key-string>",
        "description": "Operator-chosen idempotency key; each mandate-bootstrap stage derives its own sub-key from this root value.",
        "source_contract": "mandate_bootstrap() required parameter; --idempotency-key CLI flag (required=True)",
    },
    "confirm": {
        "input_type": "boolean",
        "accepted_values": None,
        "example_format": "<true|false>",
        "description": "Must be explicitly affirmed or mandate_bootstrap() raises PermissionError; there is no default that authorizes execution.",
        "source_contract": "mandate_bootstrap(): 'if not confirm: raise PermissionError(...)' (app/operator_cli/service.py)",
    },
}


def _owner_decision_worksheet(fields: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One deterministic entry per OWNER_INPUT_REQUIRED field, sorted by field name.
    Never fills, recommends, or infers a value -- current_value is always None, and
    accepted_values/example_format only ever restate what a real validator or CHECK
    constraint already enforces."""
    worksheet: list[dict[str, Any]] = []
    for name in sorted(fields.keys()):
        field = fields[name]
        if field.get("classification") != "OWNER_INPUT_REQUIRED":
            continue
        entry_spec = _MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES.get(name)
        if entry_spec is None:
            continue
        worksheet.append(
            {
                "field": name,
                "classification": field["classification"],
                "current_value": None,
                "required": True,
                "input_type": entry_spec["input_type"],
                "accepted_values": list(entry_spec["accepted_values"]) if entry_spec["accepted_values"] is not None else None,
                "example_format": entry_spec["example_format"],
                "description": entry_spec["description"],
                "source_contract": entry_spec["source_contract"],
            }
        )
    return worksheet


def _worksheet_summary(worksheet: list[dict[str, Any]]) -> dict[str, Any]:
    """Informational only, derived purely from the worksheet's own contents -- never
    hardcoded, never affects executable/overall_status. structured_json_count +
    numeric_count + text_count + boolean_count always equals field_count exactly (a
    complete partition by fundamental data shape); "enum" input_type entries fold into
    text_count there, since enum values are still string input to the CLI --
    enum_constrained_count is a separate, orthogonal tally of which fields (of any
    input_type) have a closed value set defined by a real validator/CHECK constraint."""
    numeric_types = {"decimal", "integer"}
    text_types = {"text", "csv_list", "enum"}
    return {
        "field_count": len(worksheet),
        "enum_constrained_count": sum(1 for entry in worksheet if entry["accepted_values"]),
        "structured_json_count": sum(1 for entry in worksheet if entry["input_type"] == "json_object"),
        "numeric_count": sum(1 for entry in worksheet if entry["input_type"] in numeric_types),
        "text_count": sum(1 for entry in worksheet if entry["input_type"] in text_types),
        "boolean_count": sum(1 for entry in worksheet if entry["input_type"] == "boolean"),
    }


async def mandate_bootstrap_export(*, capital_campaign_id: int) -> dict[str, Any]:
    """Stages 1-6 of the read-only mandate-bootstrap export design: resolves
    CapitalCampaign identity, (if pinned) CapitalCampaignDefinition evidence, the
    campaign's PaperAccount, LiveTradingProfile candidates strictly scoped to
    LiveTradingProfile.paper_account_id == CapitalCampaign.paper_account_id,
    ExchangeConnection candidates resolved from the campaign's own exchange label
    (provider+environment together, never provider alone) gated on the live trading
    profile having already resolved uniquely, and non-authoritative strategy evidence,
    for one capital_campaign_id. Performs SELECT statements only -- no db.add, flush,
    or commit, no lifecycle action, no authorization, no mandate creation. Never
    reuses another mandate's, another campaign's, or a conversational value -- every
    field is either read fresh from this campaign's own records or explicitly marked
    unresolved.

    allowed_strategy_versions is always OWNER_INPUT_REQUIRED (per the Strategy Identity
    Architecture Review: capital_campaigns.strategy_id is not the canonical strategy
    authority for the modern definition-pinned campaign path, and no other
    campaign-scoped field can derive it either). strategy_evidence surfaces up to four
    informational, non-authoritative candidate signals -- a legacy campaign.strategy_id
    reference, the currently globally-active Strategy row(s), an optional
    capital_campaign_definitions.metadata_evidence hint, and canonical preview package
    continuity evidence for this exact campaign+version -- none of which is ever
    converted into the allowed_strategy_versions value.

    Stage 5 adds the remaining mandate-bootstrap owner-input manifest: every
    mandate_bootstrap()/CLI parameter Stages 1-4 do not already resolve (risk limits,
    allowed_products/order_sides, policies, authorization evidence, actor/reason/
    idempotency_key/confirm, audit_correlation_id, expiry fields). None of these is ever
    filled from campaign-definition limits, strategy evidence, or any other record --
    they are always OWNER_INPUT_REQUIRED (or RUNTIME_DERIVED/NOT_REQUIRED where the
    existing contract genuinely allows it). owner_input_summary is a purely informational
    tally computed from the fields dict; it never affects executable or overall_status.

    Stage 6 adds owner_decision_worksheet: one deterministic entry per OWNER_INPUT_REQUIRED
    field describing HOW to supply it (input_type, accepted_values, example_format,
    description, source_contract) -- never WHAT to supply. current_value is always None.
    accepted_values is populated only where a real validator or DB CHECK constraint defines
    a closed set; every other constraint (numeric bounds, JSON-object shape, non-empty
    lists) is copied verbatim from validate_mandate_version()/the model's CheckConstraints/
    _parse_mandate_policy_bundle(), never invented. worksheet_summary tallies the worksheet
    by input_type, derived purely from its contents. Neither key ever affects executable or
    overall_status."""
    async with AsyncSessionLocal() as db:
        campaign = await db.get(CapitalCampaign, capital_campaign_id)

        if campaign is None:
            not_found_fields: dict[str, dict[str, Any]] = {
                "capital_campaign_id": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaigns.id",
                    "notes": "No capital_campaigns row exists for this id.",
                },
                "campaign_uuid": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaigns.uuid",
                    "notes": "Campaign not found.",
                },
                "paper_account_id": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaigns.paper_account_id",
                    "notes": "Campaign not found.",
                },
                "base_currency": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaign_definitions.base_currency",
                    "notes": "Campaign not found.",
                },
                "paper_account_asset_class": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "paper_accounts.asset_class",
                    "notes": "Campaign not found.",
                },
                "paper_account_is_active": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "paper_accounts.is_active",
                    "notes": "Campaign not found.",
                },
                "live_trading_profile_id": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "live_trading_profiles.id WHERE paper_account_id = capital_campaigns.paper_account_id",
                    "notes": "Campaign not found.",
                },
                "exchange_connection_id": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "exchange_connections WHERE provider = ? AND environment = ?",
                    "notes": "Campaign not found.",
                },
                "exchange_provider": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaigns.exchange (parsed)",
                    "notes": "Campaign not found.",
                },
                "exchange_environment": {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaigns.exchange (parsed)",
                    "notes": "Campaign not found.",
                },
                "allowed_strategy_versions": dict(_MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD),
                **_static_mandate_input_fields(),
            }
            not_found_worksheet = _owner_decision_worksheet(not_found_fields)
            return {
                "capital_campaign_id": capital_campaign_id,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "overall_status": "BLOCKED",
                "executable": False,
                "campaign": {"found": False},
                "definition": None,
                "paper_account": None,
                "live_trading_profile": None,
                "live_trading_profile_candidates": [],
                "exchange_connection": None,
                "exchange_connection_candidates": [],
                "strategy_evidence": None,
                "fields": not_found_fields,
                "owner_input_summary": _owner_input_summary(not_found_fields),
                "owner_decision_worksheet": not_found_worksheet,
                "worksheet_summary": _worksheet_summary(not_found_worksheet),
            }

        has_definition_pin = campaign.definition_campaign_id is not None and campaign.definition_version is not None

        definition_payload: dict[str, Any] | None = None
        definition_metadata_evidence: dict[str, Any] | None = None
        base_currency_field: dict[str, Any]
        if not has_definition_pin:
            base_currency_field = {
                "classification": "MISSING",
                "value": None,
                "source": "capital_campaign_definitions.base_currency",
                "notes": "Campaign has no definition_campaign_id/definition_version pin.",
            }
        else:
            definition = await _load_campaign_definition_by_identity(
                db=db, campaign_id=campaign.definition_campaign_id, version=campaign.definition_version
            )
            if definition is None:
                definition_payload = {
                    "found": False,
                    "notes": "definition_campaign_id/definition_version is pinned but no matching capital_campaign_definitions row exists.",
                }
                base_currency_field = {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaign_definitions.base_currency",
                    "notes": "Pinned definition row not found (dangling pin).",
                }
            else:
                definition_payload = {
                    "found": True,
                    "status": definition.status,
                    "base_currency": definition.base_currency,
                    "allowed_asset_classes": list(definition.allowed_asset_classes),
                    "allowed_venues": list(definition.allowed_venues),
                    "allowed_instruments": list(definition.allowed_instruments),
                    "maximum_open_positions": definition.maximum_open_positions,
                    "maximum_position_size": _decimal_str(definition.maximum_position_size),
                    "maximum_total_exposure": _decimal_str(definition.maximum_total_exposure),
                    "maximum_drawdown": _decimal_str(definition.maximum_drawdown),
                    "informational_only": True,
                    "notes": _MANDATE_BOOTSTRAP_EXPORT_DEFINITION_NOTES,
                }
                base_currency_field = {
                    "classification": "DATABASE_DERIVED",
                    "value": definition.base_currency,
                    "source": "capital_campaign_definitions.base_currency",
                    "notes": None,
                }
                definition_metadata_evidence = dict(definition.metadata_evidence or {})

        # Paper account: loaded strictly from this campaign's own paper_account_id FK --
        # never from another campaign's or mandate's paper account.
        paper_account_payload: dict[str, Any] | None = None
        paper_account_asset_class_field: dict[str, Any]
        paper_account_is_active_field: dict[str, Any]
        if campaign.paper_account_id is None:
            paper_account_asset_class_field = {
                "classification": "MISSING",
                "value": None,
                "source": "paper_accounts.asset_class",
                "notes": "capital_campaigns.paper_account_id is not set for this campaign.",
            }
            paper_account_is_active_field = {
                "classification": "MISSING",
                "value": None,
                "source": "paper_accounts.is_active",
                "notes": "capital_campaigns.paper_account_id is not set for this campaign.",
            }
        else:
            paper_account = await db.get(PaperAccount, campaign.paper_account_id)
            if paper_account is None:
                paper_account_payload = {
                    "found": False,
                    "notes": "capital_campaigns.paper_account_id is set but no matching paper_accounts row exists.",
                }
                paper_account_asset_class_field = {
                    "classification": "MISSING",
                    "value": None,
                    "source": "paper_accounts.asset_class",
                    "notes": "Referenced paper_accounts row not found.",
                }
                paper_account_is_active_field = {
                    "classification": "MISSING",
                    "value": None,
                    "source": "paper_accounts.is_active",
                    "notes": "Referenced paper_accounts row not found.",
                }
            else:
                # Deliberately excludes starting_balance, current_cash_balance, and
                # owner_user_id -- financial/identity data this export must never print.
                # name is excluded too: no existing operator diagnostic exposes
                # PaperAccount.name today, so this export does not become the first.
                paper_account_payload = {
                    "found": True,
                    "id": str(paper_account.id),
                    "asset_class": paper_account.asset_class,
                    "is_active": bool(paper_account.is_active),
                }
                paper_account_asset_class_field = {
                    "classification": "DATABASE_DERIVED",
                    "value": paper_account.asset_class,
                    "source": "paper_accounts.asset_class",
                    "notes": None,
                }
                paper_account_is_active_field = {
                    "classification": "DATABASE_DERIVED",
                    "value": bool(paper_account.is_active),
                    "source": "paper_accounts.is_active",
                    "notes": None,
                }

        # Live trading profile: candidates are resolved strictly by
        # LiveTradingProfile.paper_account_id == CapitalCampaign.paper_account_id --
        # never by provider, environment, Campaign 1, another mandate, or any other
        # inferred link. Ordered by primary key for a deterministic, non-preferential
        # listing; never "most recently created wins."
        live_trading_profile_payload: dict[str, Any] | None = None
        live_trading_profile_candidates: list[dict[str, Any]] = []
        if campaign.paper_account_id is None:
            live_trading_profile_id_field = {
                "classification": "MISSING",
                "value": None,
                "source": "live_trading_profiles.id WHERE paper_account_id = capital_campaigns.paper_account_id",
                "notes": "capital_campaigns.paper_account_id is not set for this campaign.",
            }
        else:
            candidate_rows = list(
                (
                    await db.scalars(
                        select(LiveTradingProfile)
                        .where(LiveTradingProfile.paper_account_id == campaign.paper_account_id)
                        .order_by(LiveTradingProfile.id)
                    )
                )
            )
            live_trading_profile_candidates = [_live_trading_profile_candidate_summary(row) for row in candidate_rows]

            if len(candidate_rows) == 0:
                live_trading_profile_id_field = {
                    "classification": "MISSING",
                    "value": None,
                    "source": "live_trading_profiles.id WHERE paper_account_id = capital_campaigns.paper_account_id",
                    "notes": "No live_trading_profiles row references this campaign's paper_account_id.",
                }
            elif len(candidate_rows) == 1:
                profile = candidate_rows[0]
                live_trading_profile_payload = {
                    "found": True,
                    "id": str(profile.id),
                    "paper_account_id": str(profile.paper_account_id),
                    **{field: getattr(profile, field) for field in _MANDATE_BOOTSTRAP_EXPORT_LIVE_PROFILE_GOVERNANCE_FIELDS},
                }
                live_trading_profile_id_field = {
                    "classification": "DATABASE_DERIVED",
                    "value": str(profile.id),
                    "source": "live_trading_profiles.id WHERE paper_account_id = capital_campaigns.paper_account_id",
                    "notes": None,
                }
            else:
                live_trading_profile_id_field = {
                    "classification": "CONFLICTING",
                    "value": None,
                    "source": "live_trading_profiles.id WHERE paper_account_id = capital_campaigns.paper_account_id",
                    "notes": f"{len(candidate_rows)} live_trading_profiles rows share this campaign's paper_account_id; cannot resolve unambiguously.",
                }

        # Exchange connection: resolved from the campaign's own raw exchange label
        # (provider AND environment together, via the exact deterministic inverse of
        # canonical_campaign_binding._exchange_label -- never provider alone), and gated
        # on the live trading profile having already resolved uniquely. If the profile is
        # MISSING or CONFLICTING, exchange-connection resolution is not attempted at all:
        # resolving it from the label in isolation would mean trusting that label without
        # the corroborating live-trading identity this stage requires.
        exchange_connection_payload: dict[str, Any] | None = None
        exchange_connection_candidates: list[dict[str, Any]] = []
        parsed_provider, parsed_environment = _parse_exchange_label(campaign.exchange)

        if parsed_provider is None:
            exchange_provider_field = {
                "classification": "MISSING",
                "value": None,
                "source": "capital_campaigns.exchange (parsed)",
                "notes": "capital_campaigns.exchange is unset or does not match the known {provider}/{provider}_sandbox label format.",
            }
            exchange_environment_field = {
                "classification": "MISSING",
                "value": None,
                "source": "capital_campaigns.exchange (parsed)",
                "notes": "capital_campaigns.exchange is unset or does not match the known {provider}/{provider}_sandbox label format.",
            }
        else:
            exchange_provider_field = {
                "classification": "DATABASE_DERIVED",
                "value": parsed_provider,
                "source": "capital_campaigns.exchange (parsed)",
                "notes": None,
            }
            exchange_environment_field = {
                "classification": "DATABASE_DERIVED",
                "value": parsed_environment,
                "source": "capital_campaigns.exchange (parsed)",
                "notes": None,
            }

        live_profile_uniquely_resolved = live_trading_profile_id_field["classification"] == "DATABASE_DERIVED"

        if parsed_provider is None or not live_profile_uniquely_resolved:
            exchange_connection_id_field = {
                "classification": "MISSING",
                "value": None,
                "source": "exchange_connections WHERE provider = ? AND environment = ?",
                "notes": (
                    "Exchange connection resolution requires both a parseable capital_campaigns.exchange "
                    "label and a uniquely resolved live_trading_profile_id; "
                    + (
                        "the exchange label did not parse."
                        if parsed_provider is None
                        else "live_trading_profile_id is not uniquely resolved."
                    )
                ),
            }
        else:
            connection_rows = list(
                (
                    await db.scalars(
                        select(ExchangeConnection)
                        .where(ExchangeConnection.provider == parsed_provider)
                        .where(ExchangeConnection.environment == parsed_environment)
                        .order_by(ExchangeConnection.exchange_connection_id)
                    )
                )
            )
            exchange_connection_candidates = [_exchange_connection_candidate_summary(row) for row in connection_rows]

            if len(connection_rows) == 0:
                exchange_connection_id_field = {
                    "classification": "MISSING",
                    "value": None,
                    "source": "exchange_connections WHERE provider = ? AND environment = ?",
                    "notes": "No exchange_connections row matches this campaign's parsed provider/environment.",
                }
            elif len(connection_rows) == 1:
                connection = connection_rows[0]
                # Secret-safe only: never credentials_encrypted, api_key_masked,
                # api_secret_masked, passphrase_configured, balances, total_equity_usd,
                # account_status, or last_api_error.
                exchange_connection_payload = {
                    "found": True,
                    "id": str(connection.exchange_connection_id),
                    "provider": connection.provider,
                    "environment": connection.environment,
                    "connection_status": connection.status,
                    "authentication_state": bool(connection.credentials_valid),
                    "capability_profile": list(connection.api_permissions),
                    "trading_enabled": None,
                    "withdrawals_enabled": None,
                    "supports_market_orders": None,
                    "supports_limit_orders": None,
                    "notes": _MANDATE_BOOTSTRAP_EXPORT_CAPABILITY_FLAGS_NOTES,
                }
                exchange_connection_id_field = {
                    "classification": "DATABASE_DERIVED",
                    "value": str(connection.exchange_connection_id),
                    "source": "exchange_connections WHERE provider = ? AND environment = ?",
                    "notes": None,
                }
            else:
                exchange_connection_id_field = {
                    "classification": "CONFLICTING",
                    "value": None,
                    "source": "exchange_connections WHERE provider = ? AND environment = ?",
                    "notes": f"{len(connection_rows)} exchange_connections rows match provider={parsed_provider!r} environment={parsed_environment!r}; cannot resolve unambiguously.",
                }

        # Strategy evidence: informational and non-authoritative only. allowed_strategy_versions
        # always stays OWNER_INPUT_REQUIRED (see the constant above) regardless of what is found
        # here -- none of these four sources is ever converted into that field's value.

        # 1. Legacy campaign strategy reference (capital_campaigns.strategy_id). Present only if
        # the column is set; the modern canonical execution pipeline does not consult it.
        legacy_campaign_strategy_reference: dict[str, Any] | None = None
        if campaign.strategy_id is not None:
            legacy_strategy = await db.get(Strategy, campaign.strategy_id)
            if legacy_strategy is None:
                legacy_campaign_strategy_reference = {
                    "source": "legacy_campaign_strategy_reference",
                    "found": False,
                    "notes": (
                        "capital_campaigns.strategy_id is set but no matching strategies row exists. "
                        + _MANDATE_BOOTSTRAP_EXPORT_LEGACY_STRATEGY_REFERENCE_NOTE
                    ),
                }
            else:
                legacy_campaign_strategy_reference = {
                    "source": "legacy_campaign_strategy_reference",
                    "found": True,
                    **_strategy_safe_summary(legacy_strategy),
                    "notes": _MANDATE_BOOTSTRAP_EXPORT_LEGACY_STRATEGY_REFERENCE_NOTE,
                }

        # 2. Globally active strategies (Strategy.is_active = true). System-wide, never
        # campaign-scoped. Ordered by primary key -- deterministic, never "newest".
        active_strategy_rows = list(
            (
                await db.scalars(
                    select(Strategy).where(Strategy.is_active.is_(True)).order_by(Strategy.id)
                )
            )
        )
        global_active_strategy = {
            "source": "global_active_strategy",
            "items": [_strategy_safe_summary(row) for row in active_strategy_rows],
            "notes": _MANDATE_BOOTSTRAP_EXPORT_GLOBAL_ACTIVE_STRATEGY_NOTE,
        }

        # 3. Campaign definition metadata hint. Reuses the exact same key-recognition logic
        # capital_campaign_orchestration/authoritative.py already uses (canonical_strategy_identity
        # / selected_strategy_identity / strategy_identity, top-level or nested under "strategy")
        # so this never invents a new, divergent notion of what counts as a hint, and never
        # exposes any other metadata_evidence content.
        campaign_definition_metadata_hint: dict[str, Any] | None = None
        if definition_metadata_evidence is not None:
            preferred_identity = _extract_preferred_strategy_identity_hint(definition_metadata_evidence)
            if preferred_identity:
                campaign_definition_metadata_hint = {
                    "source": "campaign_definition_metadata_hint",
                    "preferred_strategy_identity": preferred_identity,
                    "notes": _MANDATE_BOOTSTRAP_EXPORT_METADATA_HINT_NOTE,
                }

        # 4. Canonical preview package continuity evidence. Only considered for this exact
        # campaign UUID + definition version, using the identical package_state set and
        # ordering capital_campaign_orchestration/authoritative.py::_load_campaign_strategy_authority
        # already uses for continuity evidence -- not a newly invented selection rule.
        canonical_preview_package_continuity: dict[str, Any] | None = None
        if has_definition_pin:
            try:
                continuity_package = await db.scalar(
                    select(CanonicalPreviewPackage)
                    .where(CanonicalPreviewPackage.campaign_id == campaign.definition_campaign_id)
                    .where(CanonicalPreviewPackage.campaign_version == campaign.definition_version)
                    .where(CanonicalPreviewPackage.package_state.in_(_MANDATE_BOOTSTRAP_EXPORT_CONTINUITY_PACKAGE_STATES))
                    .order_by(desc(CanonicalPreviewPackage.updated_at), desc(CanonicalPreviewPackage.generated_at))
                    .limit(1)
                )
            except Exception:
                continuity_package = None

            if continuity_package is not None:
                package_strategy = await db.get(Strategy, continuity_package.strategy_id)
                canonical_preview_package_continuity = {
                    "source": "canonical_preview_package_continuity",
                    "package_id": str(continuity_package.package_id),
                    "strategy_id": str(continuity_package.strategy_id),
                    "strategy_version": continuity_package.strategy_version,
                    "canonical_identity": (
                        build_strategy_identity(slug=package_strategy.slug, module_version=package_strategy.module_version)
                        if package_strategy is not None
                        else None
                    ),
                    "package_state": continuity_package.package_state,
                    "notes": _MANDATE_BOOTSTRAP_EXPORT_PACKAGE_CONTINUITY_NOTE,
                }

        strategy_evidence = {
            "informational_only": True,
            "notes": _MANDATE_BOOTSTRAP_EXPORT_STRATEGY_EVIDENCE_NOTE,
            "legacy_campaign_strategy_reference": legacy_campaign_strategy_reference,
            "global_active_strategy": global_active_strategy,
            "campaign_definition_metadata_hint": campaign_definition_metadata_hint,
            "canonical_preview_package_continuity": canonical_preview_package_continuity,
        }

        resolved_fields: dict[str, dict[str, Any]] = {
            "capital_campaign_id": {
                "classification": "DATABASE_DERIVED",
                "value": campaign.id,
                "source": "capital_campaigns.id",
                "notes": None,
            },
            "campaign_uuid": {
                "classification": "DATABASE_DERIVED",
                "value": str(campaign.uuid),
                "source": "capital_campaigns.uuid",
                "notes": None,
            },
            "paper_account_id": (
                {
                    "classification": "DATABASE_DERIVED",
                    "value": str(campaign.paper_account_id),
                    "source": "capital_campaigns.paper_account_id",
                    "notes": None,
                }
                if campaign.paper_account_id is not None
                else {
                    "classification": "MISSING",
                    "value": None,
                    "source": "capital_campaigns.paper_account_id",
                    "notes": "capital_campaigns.paper_account_id is not set for this campaign.",
                }
            ),
            "base_currency": base_currency_field,
            "paper_account_asset_class": paper_account_asset_class_field,
            "paper_account_is_active": paper_account_is_active_field,
            "live_trading_profile_id": live_trading_profile_id_field,
            "exchange_connection_id": exchange_connection_id_field,
            "exchange_provider": exchange_provider_field,
            "exchange_environment": exchange_environment_field,
            "allowed_strategy_versions": dict(_MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD),
            **_static_mandate_input_fields(),
        }
        resolved_worksheet = _owner_decision_worksheet(resolved_fields)

        return {
            "capital_campaign_id": capital_campaign_id,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "overall_status": "BLOCKED",
            "executable": False,
            "campaign": {
                "found": True,
                "id": campaign.id,
                "uuid": str(campaign.uuid),
                "status": campaign.status,
                "name": campaign.name,
                "exchange_label_raw": campaign.exchange,
                "paper_account_id": str(campaign.paper_account_id) if campaign.paper_account_id is not None else None,
                "strategy_id": str(campaign.strategy_id) if campaign.strategy_id is not None else None,
                "definition_campaign_id": str(campaign.definition_campaign_id) if campaign.definition_campaign_id is not None else None,
                "definition_version": campaign.definition_version,
                "has_definition_pin": has_definition_pin,
            },
            "definition": definition_payload,
            "paper_account": paper_account_payload,
            "live_trading_profile": live_trading_profile_payload,
            "live_trading_profile_candidates": live_trading_profile_candidates,
            "exchange_connection": exchange_connection_payload,
            "exchange_connection_candidates": exchange_connection_candidates,
            "strategy_evidence": strategy_evidence,
            "fields": resolved_fields,
            "owner_input_summary": _owner_input_summary(resolved_fields),
            "owner_decision_worksheet": resolved_worksheet,
            "worksheet_summary": _worksheet_summary(resolved_worksheet),
        }


# The exact set of mandate_bootstrap()/mandate-bootstrap-export fields whose value is
# resolved from this campaign's own database records and therefore may never be
# overridden by owner-supplied JSON. Two request-side names (provider/environment) are
# spelled differently in mandate_bootstrap_export's `fields` dict (exchange_provider/
# exchange_environment) -- both spellings are rejected if an owner document supplies them.
_MANDATE_BOOTSTRAP_SESSION_DATABASE_FIELD_MAP: dict[str, str] = {
    "capital_campaign_id": "capital_campaign_id",
    "campaign_uuid": "campaign_uuid",
    "paper_account_id": "paper_account_id",
    "base_currency": "base_currency",
    "live_trading_profile_id": "live_trading_profile_id",
    "exchange_connection_id": "exchange_connection_id",
    "provider": "exchange_provider",
    "environment": "exchange_environment",
}

_MANDATE_BOOTSTRAP_SESSION_FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "provider",
        "exchange_provider",
        "environment",
        "exchange_environment",
        "exchange_connection_id",
        "live_trading_profile_id",
        "paper_account_id",
        "capital_campaign_id",
        "base_currency",
        "campaign_uuid",
    }
)

_MANDATE_BOOTSTRAP_SESSION_OPTIONAL_FIELD_NAMES: frozenset[str] = frozenset(
    {"mandate_expires_at", "authorization_expires_at", "audit_correlation_id"}
)

_MANDATE_BOOTSTRAP_SESSION_VERSION_VALIDATION_FIELDS: frozenset[str] = frozenset(
    {
        "authorized_capital_usd",
        "max_order_notional_usd",
        "max_open_exposure_usd",
        "max_daily_deployed_usd",
        "position_limit",
        "price_evidence_max_age_seconds",
        "allowed_products",
        "allowed_order_sides",
        "allowed_strategy_versions",
        "approval_policy",
    }
)

_MANDATE_BOOTSTRAP_SESSION_NONNEGATIVE_DECIMAL_FIELDS: tuple[str, ...] = (
    "max_daily_realized_loss_usd",
    "max_campaign_drawdown_usd",
    "max_slippage_bps",
    "max_fee_bps",
)


def _coerce_worksheet_field_value(
    *, input_type: str, raw: Any, accepted_values: list[str] | None
) -> tuple[Any, str | None]:
    """Coerces one owner-supplied JSON value per its `_MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES`
    input_type, returning (value, None) on success or (None, error_code) on failure. Purely a
    JSON-shape/type check -- never a business-rule check (those belong to validate_mandate_version()/
    validate_autonomy_level(), reused as-is in mandate_bootstrap_session_validate())."""
    if input_type == "text":
        if not isinstance(raw, str) or not raw.strip():
            return None, "invalid_text"
        return raw, None
    if input_type == "enum":
        if not isinstance(raw, str):
            return None, "invalid_enum_value"
        if accepted_values is not None and raw not in accepted_values:
            return None, "invalid_enum_value"
        return raw, None
    if input_type == "decimal":
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            return None, "invalid_decimal"
        try:
            return Decimal(str(raw)), None
        except Exception:
            return None, "invalid_decimal"
    if input_type == "integer":
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None, "invalid_integer"
        return raw, None
    if input_type == "csv_list":
        if not isinstance(raw, list):
            return None, "invalid_list"
        if not raw:
            return None, "empty_list"
        if any(not isinstance(item, str) or not item.strip() for item in raw):
            return None, "invalid_list_item"
        return list(raw), None
    if input_type == "json_object":
        if not isinstance(raw, dict):
            return None, "invalid_json_object"
        return raw, None
    return None, "unknown_input_type"


def _coerce_optional_timestamp(raw: Any) -> tuple[datetime | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "invalid_timestamp"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid_timestamp"
    if parsed.tzinfo is None:
        return None, "timestamp_not_timezone_aware"
    return parsed, None


def _coerce_optional_uuid(raw: Any) -> tuple[UUID | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "invalid_uuid"
    try:
        return UUID(raw), None
    except ValueError:
        return None, "invalid_uuid"


def _mandate_bootstrap_session_serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def mandate_bootstrap_session_validate(*, capital_campaign_id: int, owner_input: dict[str, Any]) -> dict[str, Any]:
    """Read-only mandate-bootstrap session validator. Reuses mandate_bootstrap_export()'s
    existing read-only resolution (never re-derives database identity itself), merges the
    owner-supplied JSON document with the campaign's DATABASE_DERIVED fields (owner input can
    never override provider/environment/exchange_connection_id/live_trading_profile_id/
    paper_account_id/capital_campaign_id/base_currency/campaign_uuid -- any attempt fails
    closed with OWNER_INPUT_ATTEMPTED_DATABASE_OVERRIDE), and validates the merged candidate
    against the REAL mandate_bootstrap() contract using the REAL validate_mandate_version()/
    validate_autonomy_level()/is_strategy_identity() validators -- never a reimplementation of
    their rules. Performs no db.add/flush/commit, no lifecycle action, no authorization, and
    never calls mandate_bootstrap() itself; `confirm` is always forced to False in the returned
    candidate and session_status is never a stand-in for `executable=true`. If the owner
    document itself supplies confirm=true, that is rejected as an attempted execution gate
    bypass, not silently dropped."""
    export = await mandate_bootstrap_export(capital_campaign_id=capital_campaign_id)
    fields = export["fields"]

    resolved_database_inputs: dict[str, Any] = {}
    database_identity_errors: list[str] = []
    for request_key, fields_key in _MANDATE_BOOTSTRAP_SESSION_DATABASE_FIELD_MAP.items():
        entry = fields[fields_key]
        resolved_database_inputs[request_key] = {
            "classification": entry["classification"],
            "value": entry["value"],
        }
        if entry["classification"] != "DATABASE_DERIVED":
            database_identity_errors.append(request_key)

    worksheet_field_names = set(_MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES.keys())
    required_field_names = sorted(worksheet_field_names - {"confirm"})
    known_field_names = (
        set(required_field_names)
        | _MANDATE_BOOTSTRAP_SESSION_OPTIONAL_FIELD_NAMES
        | _MANDATE_BOOTSTRAP_SESSION_FORBIDDEN_FIELD_NAMES
        | {"confirm"}
    )

    missing_fields = sorted(name for name in required_field_names if name not in owner_input)

    field_errors: list[dict[str, str]] = []

    forbidden_override_fields = sorted(
        name for name in owner_input if name in _MANDATE_BOOTSTRAP_SESSION_FORBIDDEN_FIELD_NAMES
    )
    for name in forbidden_override_fields:
        field_errors.append({"field": name, "error": "OWNER_INPUT_ATTEMPTED_DATABASE_OVERRIDE"})

    confirm_rejected = "confirm" in owner_input and owner_input["confirm"] is True
    if confirm_rejected:
        field_errors.append({"field": "confirm", "error": "OWNER_INPUT_ATTEMPTED_EXECUTION_CONFIRM"})

    unexpected_fields = sorted(name for name in owner_input if name not in known_field_names)
    if "confirm" in owner_input and not confirm_rejected:
        unexpected_fields = sorted(unexpected_fields + ["confirm"])

    coerced: dict[str, Any] = {}
    for name in required_field_names:
        if name not in owner_input or name in _MANDATE_BOOTSTRAP_SESSION_FORBIDDEN_FIELD_NAMES:
            continue
        entry = _MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES[name]
        value, error = _coerce_worksheet_field_value(
            input_type=entry["input_type"], raw=owner_input[name], accepted_values=entry["accepted_values"]
        )
        if error is not None:
            field_errors.append({"field": name, "error": error})
            continue
        if name == "allowed_strategy_versions" and any(not is_strategy_identity(item) for item in value):
            field_errors.append({"field": name, "error": "invalid_strategy_identity"})
            continue
        coerced[name] = value

    optional_coerced: dict[str, Any] = {}
    for name in _MANDATE_BOOTSTRAP_SESSION_OPTIONAL_FIELD_NAMES:
        if name not in owner_input:
            continue
        if name == "audit_correlation_id":
            value, error = _coerce_optional_uuid(owner_input[name])
        else:
            value, error = _coerce_optional_timestamp(owner_input[name])
        if error is not None:
            field_errors.append({"field": name, "error": error})
            continue
        if value is not None:
            optional_coerced[name] = value

    fields_with_errors = {entry["field"] for entry in field_errors}

    cross_field_errors: list[str] = []
    if _MANDATE_BOOTSTRAP_SESSION_VERSION_VALIDATION_FIELDS.issubset(coerced.keys()) and not (
        _MANDATE_BOOTSTRAP_SESSION_VERSION_VALIDATION_FIELDS & fields_with_errors
    ):
        synthetic_version = MandateVersionModel(
            mandate_version_id=uuid.uuid4(),
            mandate_id=uuid.uuid4(),
            version_number=1,
            base_currency=resolved_database_inputs["base_currency"]["value"] or "",
            authorized_capital_usd=coerced["authorized_capital_usd"],
            max_order_notional_usd=coerced["max_order_notional_usd"],
            max_open_exposure_usd=coerced["max_open_exposure_usd"],
            max_daily_deployed_usd=coerced["max_daily_deployed_usd"],
            max_daily_realized_loss_usd=coerced.get("max_daily_realized_loss_usd", Decimal("0")),
            max_campaign_drawdown_usd=coerced.get("max_campaign_drawdown_usd", Decimal("0")),
            max_consecutive_losses=coerced.get("max_consecutive_losses", 0),
            position_limit=coerced["position_limit"],
            price_evidence_max_age_seconds=coerced["price_evidence_max_age_seconds"],
            max_slippage_bps=coerced.get("max_slippage_bps", Decimal("0")),
            max_fee_bps=coerced.get("max_fee_bps", Decimal("0")),
            allowed_products=tuple(coerced["allowed_products"]),
            allowed_order_sides=tuple(coerced["allowed_order_sides"]),
            allowed_strategy_versions=tuple(coerced["allowed_strategy_versions"]),
            approval_policy=coerced["approval_policy"],
            is_authorized=False,
            is_active=False,
        )
        version_result = validate_mandate_version(synthetic_version)
        if not version_result.valid:
            cross_field_errors.append(version_result.reason)

    if "autonomy_level" in coerced and "autonomy_level" not in fields_with_errors:
        autonomy_result = validate_autonomy_level(coerced["autonomy_level"])
        if not autonomy_result.valid:
            cross_field_errors.append(autonomy_result.reason)

    for name in _MANDATE_BOOTSTRAP_SESSION_NONNEGATIVE_DECIMAL_FIELDS:
        if name in coerced and name not in fields_with_errors and coerced[name] < 0:
            cross_field_errors.append(f"invalid_{name}")
    if (
        "max_consecutive_losses" in coerced
        and "max_consecutive_losses" not in fields_with_errors
        and coerced["max_consecutive_losses"] < 0
    ):
        cross_field_errors.append("invalid_max_consecutive_losses")

    cross_field_errors = sorted(set(cross_field_errors))

    for name in database_identity_errors:
        field_errors.append({"field": name, "error": "database_identity_unresolved"})

    field_errors = sorted(field_errors, key=lambda entry: (entry["field"], entry["error"]))

    valid = not (
        missing_fields
        or unexpected_fields
        or forbidden_override_fields
        or field_errors
        or cross_field_errors
    )
    session_status = "COMPLETE_FOR_OWNER_REVIEW" if valid else "INVALID"

    def _resolved_db_value(request_key: str) -> Any:
        entry = resolved_database_inputs[request_key]
        return entry["value"] if entry["classification"] == "DATABASE_DERIVED" else None

    s = _mandate_bootstrap_session_serialize
    candidate_mandate_bootstrap_request = {
        "owner_actor_id": s(coerced.get("owner_actor_id")),
        "autonomy_level": s(coerced.get("autonomy_level")),
        "provider": _resolved_db_value("provider"),
        "environment": _resolved_db_value("environment"),
        "exchange_connection_id": _resolved_db_value("exchange_connection_id"),
        "live_trading_profile_id": _resolved_db_value("live_trading_profile_id"),
        "paper_account_id": _resolved_db_value("paper_account_id"),
        "capital_campaign_id": _resolved_db_value("capital_campaign_id"),
        "mandate_expires_at": s(optional_coerced.get("mandate_expires_at")),
        "base_currency": _resolved_db_value("base_currency"),
        "authorized_capital_usd": s(coerced.get("authorized_capital_usd")),
        "max_order_notional_usd": s(coerced.get("max_order_notional_usd")),
        "max_open_exposure_usd": s(coerced.get("max_open_exposure_usd")),
        "max_daily_deployed_usd": s(coerced.get("max_daily_deployed_usd")),
        "max_daily_realized_loss_usd": s(coerced.get("max_daily_realized_loss_usd")),
        "max_campaign_drawdown_usd": s(coerced.get("max_campaign_drawdown_usd")),
        "max_consecutive_losses": coerced.get("max_consecutive_losses"),
        "position_limit": coerced.get("position_limit"),
        "price_evidence_max_age_seconds": coerced.get("price_evidence_max_age_seconds"),
        "max_slippage_bps": s(coerced.get("max_slippage_bps")),
        "max_fee_bps": s(coerced.get("max_fee_bps")),
        "allowed_products": coerced.get("allowed_products"),
        "allowed_order_sides": coerced.get("allowed_order_sides"),
        "allowed_strategy_versions": coerced.get("allowed_strategy_versions"),
        "approval_policy": s(coerced.get("approval_policy")),
        "entry_policy": coerced.get("entry_policy"),
        "exit_policy": coerced.get("exit_policy"),
        "cooldown_policy": coerced.get("cooldown_policy"),
        "operating_schedule": coerced.get("operating_schedule"),
        "reconciliation_policy": coerced.get("reconciliation_policy"),
        "kill_switch_policy": coerced.get("kill_switch_policy"),
        "owner_acknowledgements": coerced.get("owner_acknowledgements"),
        "authorization_evidence_summary": coerced.get("authorization_evidence_summary"),
        "authorization_method": s(coerced.get("authorization_method")),
        "authorization_evidence": coerced.get("authorization_evidence"),
        "deterministic_explanation": coerced.get("deterministic_explanation"),
        "authorization_expires_at": s(optional_coerced.get("authorization_expires_at")),
        "actor": s(coerced.get("actor")),
        "reason": s(coerced.get("reason")),
        "idempotency_key": s(coerced.get("idempotency_key")),
        "audit_correlation_id": s(optional_coerced.get("audit_correlation_id")),
        "confirm": False,
    }

    owner_selected_allowed_strategy_versions = coerced.get("allowed_strategy_versions") or []
    strategy_evidence = export["strategy_evidence"] or {}
    candidate_identities: set[str] = set()
    legacy_ref = strategy_evidence.get("legacy_campaign_strategy_reference")
    if legacy_ref and legacy_ref.get("canonical_identity"):
        candidate_identities.add(legacy_ref["canonical_identity"])
    for item in (strategy_evidence.get("global_active_strategy") or {}).get("items", []) or []:
        if item.get("canonical_identity"):
            candidate_identities.add(item["canonical_identity"])
    hint = strategy_evidence.get("campaign_definition_metadata_hint")
    if hint and hint.get("preferred_strategy_identity"):
        candidate_identities.add(hint["preferred_strategy_identity"])
    continuity = strategy_evidence.get("canonical_preview_package_continuity")
    if continuity and continuity.get("canonical_identity"):
        candidate_identities.add(continuity["canonical_identity"])

    evidence_matches_owner_selection = (
        bool(set(owner_selected_allowed_strategy_versions) & candidate_identities)
        if owner_selected_allowed_strategy_versions
        else False
    )

    campaign_payload = export["campaign"]
    source_identity = {
        "capital_campaign_id": export["capital_campaign_id"],
        "campaign_uuid": fields["campaign_uuid"]["value"],
        "definition_campaign_id": campaign_payload.get("definition_campaign_id") if campaign_payload.get("found") else None,
        "definition_version": campaign_payload.get("definition_version") if campaign_payload.get("found") else None,
        "export_resolved_at": export["resolved_at"],
    }

    return {
        "session_status": session_status,
        "resolved_database_inputs": resolved_database_inputs,
        "owner_inputs": owner_input,
        "candidate_mandate_bootstrap_request": candidate_mandate_bootstrap_request,
        "validation": {
            "valid": valid,
            "missing_fields": missing_fields,
            "unexpected_fields": unexpected_fields,
            "forbidden_override_fields": forbidden_override_fields,
            "field_errors": field_errors,
            "cross_field_errors": cross_field_errors,
        },
        "source_identity": source_identity,
        "strategy_review": {
            "owner_selected_allowed_strategy_versions": sorted(owner_selected_allowed_strategy_versions),
            "informational_strategy_evidence": strategy_evidence,
            "evidence_matches_owner_selection": evidence_matches_owner_selection,
        },
    }


_MANDATE_GOVERNANCE_AUDIT_FORBIDDEN_BARE_CALLS: frozenset[str] = frozenset(
    {
        "create_mandate",
        "create_mandate_version",
        "authorize_mandate_version",
        "apply_mandate_lifecycle_action",
        "mandate_bootstrap",
    }
)
_MANDATE_GOVERNANCE_AUDIT_FORBIDDEN_ATTRIBUTE_CALLS: frozenset[str] = frozenset({"add", "commit", "flush", "delete"})


def _mandate_governance_audit_scan_calls(fn: Any) -> list[str]:
    """Parses fn's own source (inspect.getsource) as a real Python AST and returns the
    sorted names of any forbidden write/lifecycle calls actually present as ast.Call nodes
    in its code. Deliberately AST-based rather than a text/regex scan: a regex would false-
    positive on a docstring merely mentioning "mandate_bootstrap()" in prose, where an AST
    walk only ever matches genuine call sites, however deeply nested (e.g. inside another
    call's keyword argument)."""
    source = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _MANDATE_GOVERNANCE_AUDIT_FORBIDDEN_BARE_CALLS:
            found.add(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _MANDATE_GOVERNANCE_AUDIT_FORBIDDEN_ATTRIBUTE_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id == "db"
        ):
            # Deliberately scoped to the `db` session variable specifically -- a bare
            # attribute-name match (e.g. func.attr == "add") would also catch unrelated,
            # perfectly legitimate calls like set.add()/dict.update() that happen to share
            # a method name with a real SQLAlchemy session mutation.
            found.add(f"db.{func.attr}")
    return sorted(found)


def _mandate_governance_audit_strip_docstring(source: str) -> str:
    """Removes the function's own leading docstring before pattern-matching its code, so a
    prose description can never be mistaken for (or coincidentally launder) the real code
    pattern being checked for."""
    return re.sub(r'""".*?"""', "", source, count=1, flags=re.DOTALL)


async def mandate_governance_readiness_audit(*, capital_campaign_id: int) -> dict[str, Any]:
    """Read-only inspection of the mandate-bootstrap pipeline's write-safety -- exists
    solely to answer whether Stage 9 (mandate creation) is safe to allow. Never performs a
    write itself: every database read is delegated to mandate_bootstrap_export()/
    mandate_bootstrap_session_validate() (already proven write-free by their own Stage 1-7
    tests), and every safety property is checked against the real running code via
    inspect.getsource()/inspect.signature()/ast (never a hand-written description of what
    the code is assumed to do). Creates zero rows, calls no lifecycle method, and never
    authorizes, activates, or triggers Stage 9 itself -- overall_status is a report, not a
    gate; a human operator still decides whether to proceed."""
    from app.services.mandates import validation as _validation_module
    from app.services.strategies import identity as _identity_module

    inspection_timestamp = datetime.now(timezone.utc).isoformat()
    commands_inspected: list[str] = []
    write_paths: list[dict[str, Any]] = []
    blocked_write_paths: list[dict[str, Any]] = []
    warnings: list[str] = []

    # --- Items 1/2/3/15: the two read-only commands must contain zero forbidden write or
    # lifecycle calls anywhere in their own code.
    for name, fn in (
        ("mandate_bootstrap_export", mandate_bootstrap_export),
        ("mandate_bootstrap_session_validate", mandate_bootstrap_session_validate),
    ):
        commands_inspected.append(name)
        found_calls = _mandate_governance_audit_scan_calls(fn)
        if found_calls:
            write_paths.append({"function": name, "forbidden_calls_found": found_calls})
        else:
            blocked_write_paths.append(
                {
                    "function": name,
                    "status": "no write or lifecycle calls found anywhere in this function's own AST",
                    "checked_calls": sorted(
                        _MANDATE_GOVERNANCE_AUDIT_FORBIDDEN_BARE_CALLS
                        | {f"db.{attr}" for attr in _MANDATE_GOVERNANCE_AUDIT_FORBIDDEN_ATTRIBUTE_CALLS}
                    ),
                }
            )

    # mandate_bootstrap() is the repository's one legitimate write path (Stage 9's future
    # target) -- it must still exist, but must remain gated behind confirm=True.
    commands_inspected.append("mandate_bootstrap")
    bootstrap_source = _mandate_governance_audit_strip_docstring(
        textwrap.dedent(inspect.getsource(mandate_bootstrap))
    )
    bootstrap_gated = bool(re.search(r"if\s+not\s+confirm\s*:\s*\n\s*raise\s+PermissionError", bootstrap_source))
    if bootstrap_gated:
        blocked_write_paths.append(
            {
                "function": "mandate_bootstrap",
                "status": "write path exists but is gated behind confirm=True (PermissionError otherwise)",
            }
        )
    else:
        write_paths.append({"function": "mandate_bootstrap", "forbidden_calls_found": ["confirm_gate_not_found"]})

    session_validate_params = set(inspect.signature(mandate_bootstrap_session_validate).parameters.keys())
    no_own_db_session = "db" not in session_validate_params and "session" not in session_validate_params

    session_validate_source = _mandate_governance_audit_strip_docstring(
        textwrap.dedent(inspect.getsource(mandate_bootstrap_session_validate))
    )

    # --- Item 4: owner input can never overwrite a DATABASE_DERIVED field.
    forbidden_field_names = _MANDATE_BOOTSTRAP_SESSION_FORBIDDEN_FIELD_NAMES
    database_field_map = _MANDATE_BOOTSTRAP_SESSION_DATABASE_FIELD_MAP
    forbidden_covers_database_fields = set(database_field_map.keys()) | set(database_field_map.values())
    forbidden_field_map_consistent = forbidden_covers_database_fields.issubset(forbidden_field_names)

    worksheet_field_names = set(_MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES.keys())
    required_field_names = sorted(worksheet_field_names - {"confirm"})
    no_owner_forbidden_overlap = not (set(required_field_names) & forbidden_field_names)

    probe_override_input = {name: "__governance_audit_probe__" for name in forbidden_field_names}
    override_probe = await mandate_bootstrap_session_validate(
        capital_campaign_id=capital_campaign_id, owner_input=probe_override_input
    )
    override_probe_blocked = (
        set(override_probe["validation"]["forbidden_override_fields"]) == set(forbidden_field_names)
        and override_probe["session_status"] == "INVALID"
    )

    # --- Items 6/7: no hidden defaults for OWNER_INPUT_REQUIRED fields; every one of them
    # must still require explicit owner input (proven statically and by an empty-document probe).
    hidden_default_fields: list[str] = []
    for field_name, entry in _MANDATE_BOOTSTRAP_EXPORT_STATIC_MANDATE_FIELDS.items():
        if entry["classification"] == "OWNER_INPUT_REQUIRED" and (entry["value"] is not None or entry["source"] is not None):
            hidden_default_fields.append(field_name)
    strategy_field = _MANDATE_BOOTSTRAP_EXPORT_ALLOWED_STRATEGY_VERSIONS_FIELD
    if strategy_field["classification"] == "OWNER_INPUT_REQUIRED" and (
        strategy_field["value"] is not None or strategy_field["source"] is not None
    ):
        hidden_default_fields.append("allowed_strategy_versions")

    empty_document_probe = await mandate_bootstrap_session_validate(
        capital_campaign_id=capital_campaign_id, owner_input={}
    )
    every_required_field_still_required = (
        empty_document_probe["validation"]["missing_fields"] == required_field_names
        and empty_document_probe["session_status"] == "INVALID"
    )

    # --- Item 5: confirm cannot become true.
    confirm_excluded_from_required = "confirm" not in required_field_names
    confirm_hardcoded_false = bool(re.search(r'"confirm"\s*:\s*False', session_validate_source))

    session_validate_parser_options: list[str] | None = None
    try:
        import argparse

        import app.operator_cli.main as _main_module

        parser = _main_module._build_parser()
        subparsers_action = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )
        session_validate_parser_options = [
            option
            for action in subparsers_action.choices["mandate-bootstrap-session-validate"]._actions
            for option in action.option_strings
        ]
    except Exception as exc:  # pragma: no cover - defensive only, never expected
        warnings.append(f"could not introspect CLI parser for mandate-bootstrap-session-validate: {exc}")
    # Fail closed: if the parser could not be introspected, this check must not silently pass.
    no_confirm_cli_flag = session_validate_parser_options is not None and "--confirm" not in session_validate_parser_options

    confirm_probe = await mandate_bootstrap_session_validate(
        capital_campaign_id=capital_campaign_id, owner_input={"confirm": True}
    )
    confirm_probe_blocked = (
        {"field": "confirm", "error": "OWNER_INPUT_ATTEMPTED_EXECUTION_CONFIRM"}
        in confirm_probe["validation"]["field_errors"]
        and confirm_probe["candidate_mandate_bootstrap_request"]["confirm"] is False
        and confirm_probe["session_status"] == "INVALID"
    )

    # --- Item 8: every validation failure blocks execution (session_status strictly follows valid).
    session_status_follows_valid = bool(
        re.search(
            r'session_status\s*=\s*"COMPLETE_FOR_OWNER_REVIEW"\s+if\s+valid\s+else\s+"INVALID"',
            session_validate_source,
        )
    )

    # --- Item 9: every validator used is the repository's authoritative one -- compared by
    # identity against the module each is actually defined in, so a local shadowing
    # reimplementation (not merely a same-named duplicate) would be caught.
    validators_verified = [
        {
            "name": "validate_mandate_version",
            "source_module": _validation_module.__name__,
            "is_authoritative": validate_mandate_version is _validation_module.validate_mandate_version,
        },
        {
            "name": "validate_autonomy_level",
            "source_module": _validation_module.__name__,
            "is_authoritative": validate_autonomy_level is _validation_module.validate_autonomy_level,
        },
        {
            "name": "is_strategy_identity",
            "source_module": _identity_module.__name__,
            "is_authoritative": is_strategy_identity is _identity_module.is_strategy_identity,
        },
    ]
    all_validators_authoritative = all(entry["is_authoritative"] for entry in validators_verified)

    # --- Items 10/11: strategy evidence stays informational-only and is never converted
    # into owner authorization; evidence_matches_owner_selection is computed strictly after
    # -- and independent of -- the validity determination (proven by source position).
    strategy_field_always_owner_required = strategy_field["classification"] == "OWNER_INPUT_REQUIRED"
    valid_assignment_index = session_validate_source.find("valid = not (")
    evidence_match_index = session_validate_source.find("evidence_matches_owner_selection = (")
    evidence_match_computed_after_validity = (
        valid_assignment_index != -1 and evidence_match_index != -1 and evidence_match_index > valid_assignment_index
    )

    export_probe = await mandate_bootstrap_export(capital_campaign_id=capital_campaign_id)
    strategy_evidence_informational = (
        export_probe["strategy_evidence"] is None or export_probe["strategy_evidence"].get("informational_only") is True
    )
    if not export_probe["campaign"].get("found", False):
        warnings.append(
            f"capital_campaign_id={capital_campaign_id} was not found; identity-chain-specific "
            "probes above reflect the not-found path, not a live resolved campaign."
        )

    # --- Item 12: runtime-derived fields are never minted by this validator -- it only
    # ever reads what the owner supplied, or leaves the field None.
    no_audit_correlation_id_minted = (
        'audit_correlation_id"] = uuid.uuid4()' not in session_validate_source
        and "audit_correlation_id = uuid.uuid4()" not in session_validate_source
    )

    # --- Item 13: no hidden mutable global state -- the field-classification sets this
    # function depends on are frozensets (immutable), and the function never assigns into
    # any module-level worksheet/static-field dict.
    all_constants_immutable = all(
        isinstance(value, frozenset)
        for value in (
            _MANDATE_BOOTSTRAP_SESSION_FORBIDDEN_FIELD_NAMES,
            _MANDATE_BOOTSTRAP_SESSION_OPTIONAL_FIELD_NAMES,
            _MANDATE_BOOTSTRAP_SESSION_VERSION_VALIDATION_FIELDS,
        )
    )
    no_module_state_mutation = not re.search(
        r"_MANDATE_BOOTSTRAP_EXPORT_(WORKSHEET_ENTRIES|STATIC_MANDATE_FIELDS)\s*\[[^\]]*\]\s*=(?!=)",
        session_validate_source,
    )

    # --- Item 14: no side effects -- no audit-log rows, no orchestration-stage logging calls.
    no_audit_logging_calls = not any(
        pattern in session_validate_source
        for pattern in ("AuditLog(", "_log_orchestration_stage(", "_log_commission_stage(")
    )

    owner_boundaries: dict[str, Any] = {
        "forbidden_field_map_consistent_with_database_fields": forbidden_field_map_consistent,
        "no_overlap_between_required_and_forbidden_fields": no_owner_forbidden_overlap,
        "override_probe_blocked_all_forbidden_fields": override_probe_blocked,
        "no_hidden_default_values_for_owner_required_fields": not hidden_default_fields,
        "hidden_default_fields_found": hidden_default_fields,
        "every_required_field_still_required_probe": every_required_field_still_required,
    }
    runtime_boundaries: dict[str, Any] = {
        "audit_correlation_id_never_minted": no_audit_correlation_id_minted,
        "session_validator_has_no_own_db_session_parameter": no_own_db_session,
        "field_classification_constants_are_immutable": all_constants_immutable,
        "no_module_level_state_mutation_in_source": no_module_state_mutation,
        "no_audit_logging_side_effects_in_validator": no_audit_logging_calls,
    }
    strategy_boundaries: dict[str, Any] = {
        "allowed_strategy_versions_always_owner_input_required": strategy_field_always_owner_required,
        "strategy_evidence_marked_informational_only": strategy_evidence_informational,
        "evidence_match_flag_computed_after_and_independent_of_validity": evidence_match_computed_after_validity,
    }
    authorization_boundaries: dict[str, Any] = {
        "confirm_excluded_from_owner_required_fields": confirm_excluded_from_required,
        "confirm_hardcoded_false_in_candidate": confirm_hardcoded_false,
        "no_confirm_cli_flag_registered": no_confirm_cli_flag,
        "confirm_true_probe_blocked": confirm_probe_blocked,
        "session_status_strictly_follows_valid": session_status_follows_valid,
        "all_validators_are_repository_authoritative": all_validators_authoritative,
        "mandate_bootstrap_write_path_gated_behind_confirm": bootstrap_gated,
    }

    pass_fail_checks: dict[str, bool] = {
        **{k: v for k, v in owner_boundaries.items() if isinstance(v, bool)},
        **{k: v for k, v in runtime_boundaries.items() if isinstance(v, bool)},
        **{k: v for k, v in strategy_boundaries.items() if isinstance(v, bool)},
        **{k: v for k, v in authorization_boundaries.items() if isinstance(v, bool)},
    }
    overall_pass = not write_paths and all(pass_fail_checks.values())
    overall_status = "READY_FOR_STAGE9" if overall_pass else "NOT_READY"

    recommendations: list[str] = [
        "This audit is a report, not a gate: it does not itself authorize, trigger, or "
        "block Stage 9 -- a human operator must still decide whether to proceed.",
        "Re-run this audit after any change to the mandate-bootstrap pipeline in "
        "app/operator_cli/service.py or to app/services/mandates/validation.py before "
        "trusting a prior result.",
    ]
    if not overall_pass:
        recommendations.append("Resolve every failing check below before considering Stage 9.")
        recommendations.extend(
            sorted(f"failing_check:{name}" for name, passed in pass_fail_checks.items() if not passed)
        )
        if write_paths:
            recommendations.extend(
                sorted(f"unexpected_write_path:{entry['function']}" for entry in write_paths)
            )

    return {
        "overall_status": overall_status,
        "repository_safe_for_stage9": overall_pass,
        "inspection_timestamp": inspection_timestamp,
        "commands_inspected": commands_inspected,
        "validators_verified": validators_verified,
        "write_paths": write_paths,
        "blocked_write_paths": blocked_write_paths,
        "owner_boundaries": owner_boundaries,
        "runtime_boundaries": runtime_boundaries,
        "strategy_boundaries": strategy_boundaries,
        "authorization_boundaries": authorization_boundaries,
        "warnings": warnings,
        "recommendations": recommendations,
        "next_stage": (
            "Stage 9 (mandate creation) is never triggered by this audit regardless of "
            "overall_status; it requires separate, explicit human authorization."
        ),
    }


_MANDATE_BOOTSTRAP_CREATE_TRANSACTION_MODEL: dict[str, Any] = {
    "classification": "SEPARATE_IDEMPOTENT_TRANSACTIONS",
    "description": (
        "create_mandate() and create_mandate_version() each commit their own transaction "
        "(db.add + db.flush + AuditLog row + db.commit) -- these are two separate database "
        "transactions, NOT one atomic transaction spanning both writes. A process "
        "interruption between the two commits leaves a real, durable, partial state "
        "(mandate row exists, no version row yet) rather than rolling back to nothing. "
        "Recovery is deterministic, not atomic: rerunning mandate-bootstrap-create with the "
        "same owner-input document resumes via each function's own idempotency-key lookup, "
        "creating only the missing version and never a duplicate mandate. This model is "
        "preserved as-is (not merged into one transaction) because create_mandate() offers "
        "no commit=False option and is shared by other callers -- changing that is out of "
        "this stage's narrow scope. The requirement met here is deterministic recovery and "
        "honest reporting, not atomicity."
    ),
}


def _mandate_bootstrap_create_transaction_model() -> dict[str, Any]:
    return dict(_MANDATE_BOOTSTRAP_CREATE_TRANSACTION_MODEL)


def _mandate_bootstrap_create_mandate_mismatches(
    *, existing: AutonomousCapitalMandate, candidate: dict[str, Any]
) -> list[str]:
    """Compares an already-created mandate's real, committed identity against the CURRENT
    call's candidate request. A mismatch here means the same idempotency_key is being
    reused either with materially different owner input, or because the campaign's own
    underlying database identity (provider/exchange/profile/account/campaign) drifted
    since the mandate was created -- both cases must fail closed, never silently resume
    against the old row as if nothing changed."""
    candidate_expires_at = _parse_datetime(candidate.get("mandate_expires_at"))
    checks: list[tuple[str, Any, Any]] = [
        ("owner_actor_id", existing.owner_actor_id, candidate.get("owner_actor_id")),
        ("autonomy_level", existing.autonomy_level, candidate.get("autonomy_level")),
        ("provider", existing.provider, candidate.get("provider")),
        ("exchange_environment", existing.exchange_environment, candidate.get("environment")),
        ("exchange_connection_id", str(existing.exchange_connection_id), candidate.get("exchange_connection_id")),
        ("live_trading_profile_id", str(existing.live_trading_profile_id), candidate.get("live_trading_profile_id")),
        (
            "paper_account_id",
            str(existing.paper_account_id) if existing.paper_account_id is not None else None,
            candidate.get("paper_account_id"),
        ),
        ("capital_campaign_id", existing.capital_campaign_id, candidate.get("capital_campaign_id")),
        ("mandate_expires_at", existing.expires_at, candidate_expires_at),
    ]
    return [name for name, existing_value, candidate_value in checks if existing_value != candidate_value]


def _mandate_bootstrap_create_conflict_payload(
    *,
    reason: str,
    root_idempotency_key: str,
    mandate_id: uuid.UUID | None,
    mandate_version_id: uuid.UUID | None,
    database_identity: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "overall_status": "CONFLICT",
        "mandate_id": str(mandate_id) if mandate_id is not None else None,
        "mandate_version_id": str(mandate_version_id) if mandate_version_id is not None else None,
        "database_identity": database_identity,
        "audit_summary": {"writes_performed": False},
        "write_summary": {"mandate_created": False, "mandate_version_created": False},
        "transaction_model": _mandate_bootstrap_create_transaction_model(),
        "conflict": {"reason": reason, "idempotency_key": root_idempotency_key, **detail},
        "next_required_action": (
            "This idempotency_key was already used with materially different mandate or "
            "version input (or the underlying campaign identity has since drifted). Choose "
            "a new idempotency_key for a genuinely new mandate, or resupply the exact "
            "original owner-input document to resume safely. Zero new writes were "
            "performed by this call."
        ),
    }


async def mandate_bootstrap_create(*, capital_campaign_id: int, owner_input: dict[str, Any]) -> dict[str, Any]:
    """Stage 9A/9A.1: crosses the write boundary for the first time, and stops exactly
    there. Reuses mandate_bootstrap_export()/mandate_bootstrap_session_validate() for all
    identity resolution and validation (never re-derives or re-validates anything), and on
    success reuses the exact same create_mandate()/create_mandate_version() functions and
    MandateVersionCreateRequest contract mandate_bootstrap() itself already calls for its
    first two stages -- this is a deliberately truncated prefix of that existing, already
    production-validated write path, not a new one. Never calls
    apply_mandate_lifecycle_action(), authorize_mandate_version(), or mandate_bootstrap()
    itself: the created mandate is left in its default DRAFT status with an unauthorized,
    inactive initial version.

    Recovery/idempotency (Stage 9A.1): create_mandate()/create_mandate_version() are each
    already idempotent via their own idempotency_key-keyed AuditLog lookup, so a rerun with
    the same owner-input document after a process interruption safely creates only
    whichever of the two rows is still missing, never a duplicate. Before resuming onto an
    existing mandate (or existing version), this function additionally verifies the
    existing row's real, committed identity/economic terms still match the CURRENT
    candidate request -- reusing create_mandate_version()'s own version_hash fingerprint
    (via lifecycle._build_version_hash(), not a reimplementation) for the version side. Any
    mismatch fails closed as overall_status=CONFLICT with zero new writes, rather than
    silently resuming against stale or divergent data."""
    export = await mandate_bootstrap_export(capital_campaign_id=capital_campaign_id)
    session = await mandate_bootstrap_session_validate(capital_campaign_id=capital_campaign_id, owner_input=owner_input)

    if session["session_status"] != "COMPLETE_FOR_OWNER_REVIEW":
        return {
            "overall_status": "FAILED_VALIDATION",
            "mandate_id": None,
            "mandate_version_id": None,
            "database_identity": session["resolved_database_inputs"],
            "audit_summary": {"writes_performed": False},
            "write_summary": {"mandate_created": False, "mandate_version_created": False},
            "transaction_model": _mandate_bootstrap_create_transaction_model(),
            "validation": session["validation"],
            "next_required_action": (
                "Resolve the validation failures reported under validation (missing_fields/"
                "unexpected_fields/forbidden_override_fields/field_errors/cross_field_errors) "
                "and retry mandate-bootstrap-create. Zero writes were performed."
            ),
        }

    candidate = session["candidate_mandate_bootstrap_request"]
    root_idempotency_key = candidate["idempotency_key"]
    actor = candidate["actor"]
    mandate_idempotency_key = f"{root_idempotency_key}:create-mandate"
    version_idempotency_key = f"{root_idempotency_key}:create-version"

    async with AsyncSessionLocal() as db:
        existing_mandate: AutonomousCapitalMandate | None = None
        existing_mandate_audit = await _find_audit_by_idempotency(
            db=db,
            entity_type="autonomous_capital_mandate",
            action="MANDATE_CREATED",
            idempotency_key=mandate_idempotency_key,
        )
        if existing_mandate_audit is not None and existing_mandate_audit.entity_id is not None:
            existing_mandate = await db.get(AutonomousCapitalMandate, existing_mandate_audit.entity_id)

        if existing_mandate is not None:
            mismatched_fields = _mandate_bootstrap_create_mandate_mismatches(existing=existing_mandate, candidate=candidate)
            if mismatched_fields:
                return _mandate_bootstrap_create_conflict_payload(
                    reason="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_MANDATE_INPUT",
                    root_idempotency_key=root_idempotency_key,
                    mandate_id=existing_mandate.mandate_id,
                    mandate_version_id=None,
                    database_identity=session["resolved_database_inputs"],
                    detail={"mismatched_fields": sorted(mismatched_fields)},
                )

        mandate = await create_mandate(
            db=db,
            owner_actor_id=candidate["owner_actor_id"],
            autonomy_level=candidate["autonomy_level"],
            provider=candidate["provider"],
            exchange_environment=candidate["environment"],
            exchange_connection_id=UUID(candidate["exchange_connection_id"]),
            live_trading_profile_id=UUID(candidate["live_trading_profile_id"]),
            paper_account_id=UUID(candidate["paper_account_id"]) if candidate["paper_account_id"] else None,
            capital_campaign_id=candidate["capital_campaign_id"],
            expires_at=_parse_datetime(candidate["mandate_expires_at"]),
            actor=actor,
            idempotency_key=mandate_idempotency_key,
            reason=candidate["reason"],
        )

        version_request = MandateVersionCreateRequest(
            mandate_id=mandate.mandate_id,
            actor=actor,
            base_currency=candidate["base_currency"],
            authorized_capital_usd=Decimal(candidate["authorized_capital_usd"]),
            max_order_notional_usd=Decimal(candidate["max_order_notional_usd"]),
            max_open_exposure_usd=Decimal(candidate["max_open_exposure_usd"]),
            max_daily_deployed_usd=Decimal(candidate["max_daily_deployed_usd"]),
            max_daily_realized_loss_usd=Decimal(candidate["max_daily_realized_loss_usd"]),
            max_campaign_drawdown_usd=Decimal(candidate["max_campaign_drawdown_usd"]),
            max_consecutive_losses=candidate["max_consecutive_losses"],
            position_limit=candidate["position_limit"],
            price_evidence_max_age_seconds=candidate["price_evidence_max_age_seconds"],
            max_slippage_bps=Decimal(candidate["max_slippage_bps"]),
            max_fee_bps=Decimal(candidate["max_fee_bps"]),
            allowed_products=tuple(candidate["allowed_products"]),
            allowed_order_sides=tuple(candidate["allowed_order_sides"]),
            allowed_strategy_versions=tuple(candidate["allowed_strategy_versions"]),
            entry_policy=candidate["entry_policy"],
            exit_policy=candidate["exit_policy"],
            cooldown_policy=candidate["cooldown_policy"],
            operating_schedule=candidate["operating_schedule"],
            approval_policy=candidate["approval_policy"],
            reconciliation_policy=candidate["reconciliation_policy"],
            kill_switch_policy=candidate["kill_switch_policy"],
            owner_acknowledgements=candidate["owner_acknowledgements"],
            authorization_evidence_summary=candidate["authorization_evidence_summary"],
            idempotency_key=version_idempotency_key,
            audit_correlation_id=UUID(candidate["audit_correlation_id"]) if candidate["audit_correlation_id"] else None,
        )

        existing_version_audit = await _find_audit_by_idempotency(
            db=db,
            entity_type="autonomous_capital_mandate",
            action="MANDATE_VERSION_CREATED",
            idempotency_key=version_idempotency_key,
        )
        existing_version: AutonomousCapitalMandateVersion | None = None
        if existing_version_audit is not None and existing_version_audit.after_state:
            version_id_raw = existing_version_audit.after_state.get("mandate_version_id")
            if isinstance(version_id_raw, str):
                try:
                    existing_version = await db.get(AutonomousCapitalMandateVersion, uuid.UUID(version_id_raw))
                except ValueError:
                    existing_version = None

        if existing_version is not None:
            probe_hash = _build_version_hash(request=version_request, version_number=existing_version.version_number)
            if probe_hash != existing_version.version_hash:
                return _mandate_bootstrap_create_conflict_payload(
                    reason="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_VERSION_INPUT",
                    root_idempotency_key=root_idempotency_key,
                    mandate_id=mandate.mandate_id,
                    mandate_version_id=existing_version.mandate_version_id,
                    database_identity=session["resolved_database_inputs"],
                    detail={},
                )

        version = await create_mandate_version(db=db, request=version_request)

    return {
        "overall_status": "CREATED",
        "mandate_id": str(mandate.mandate_id),
        "mandate_version_id": str(version.mandate_version_id),
        "database_identity": session["resolved_database_inputs"],
        "audit_summary": {
            "writes_performed": True,
            "actor": actor,
            "reason": candidate["reason"],
            "root_idempotency_key": root_idempotency_key,
            "audit_actions": ["MANDATE_CREATED", "MANDATE_VERSION_CREATED"],
        },
        "write_summary": {
            "mandate_created": True,
            "mandate_version_created": True,
            "mandate_status": mandate.status,
            "mandate_version_number": version.version_number,
            "mandate_version_is_authorized": bool(version.is_authorized),
            "mandate_version_is_active": bool(version.is_active),
        },
        "transaction_model": _mandate_bootstrap_create_transaction_model(),
        "next_required_action": (
            "This mandate and its initial version exist in an unauthorized, inactive state "
            "only (mandate.status remains DRAFT). No lifecycle action, authorization, "
            "activation, or trading has occurred. A human operator must separately and "
            "explicitly submit this version for authorization and activate it -- this "
            "command never does so automatically."
        ),
    }


async def _mandate_bootstrap_create_all_audits_by_idempotency(
    *, db: Any, entity_type: str, action: str, idempotency_key: str
) -> list[AuditLog]:
    """Mirrors _find_audit_by_idempotency()'s exact query shape (same entity_type/action
    filter, same recency ordering) but returns EVERY matching row instead of only the
    first. _find_audit_by_idempotency() cannot be reused as-is for this: it deliberately
    stops at the first match, which is exactly right for idempotent-resume but would
    silently hide a second, conflicting audit row filed under the same idempotency_key
    (e.g. a race between two concurrent creation attempts) -- the one scenario this
    read-only status command exists to surface."""
    records = list(
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.action == action)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(200)
        )
    )
    return [record for record in records if (record.after_state or {}).get("idempotency_key") == idempotency_key]


def _mandate_bootstrap_create_serialize_audit_event(row: AuditLog) -> dict[str, Any]:
    return {
        "audit_id": row.id,
        "actor": row.actor,
        "action": row.action,
        "created_at": row.created_at.isoformat(),
        "entity_id": str(row.entity_id) if row.entity_id is not None else None,
    }


def _mandate_bootstrap_create_identity_drift(
    *, mandate: AutonomousCapitalMandate, export_fields: dict[str, dict[str, Any]]
) -> list[str]:
    """Compares a real, already-created mandate's committed identity against what
    mandate_bootstrap_export() resolves for this campaign RIGHT NOW. Only ever compares a
    field the export currently classifies DATABASE_DERIVED -- a currently MISSING/
    CONFLICTING classification means the campaign's live identity cannot be confirmed
    either way today, which is reported separately as a warning, not asserted as drift."""
    comparisons = [
        ("provider", mandate.provider, export_fields["exchange_provider"]),
        ("exchange_environment", mandate.exchange_environment, export_fields["exchange_environment"]),
        ("exchange_connection_id", str(mandate.exchange_connection_id), export_fields["exchange_connection_id"]),
        ("live_trading_profile_id", str(mandate.live_trading_profile_id), export_fields["live_trading_profile_id"]),
        (
            "paper_account_id",
            str(mandate.paper_account_id) if mandate.paper_account_id is not None else None,
            export_fields["paper_account_id"],
        ),
        ("capital_campaign_id", mandate.capital_campaign_id, export_fields["capital_campaign_id"]),
    ]
    drifted: list[str] = []
    for field_name, mandate_value, export_field in comparisons:
        if export_field["classification"] != "DATABASE_DERIVED":
            continue
        if export_field["value"] != mandate_value:
            drifted.append(
                f"{field_name}: mandate has {mandate_value!r}, campaign currently resolves {export_field['value']!r}"
            )
    return drifted


async def mandate_bootstrap_create_status(*, capital_campaign_id: int, idempotency_key: str) -> dict[str, Any]:
    """Stage 9A.1 Part 1: read-only inspection of mandate-bootstrap-create's real,
    committed database state for one capital_campaign_id + root idempotency_key. Performs
    SELECT-only reads (db.get()/db.scalars()) plus one reused call into
    mandate_bootstrap_export() for identity-drift comparison -- no db.add/commit/flush/
    delete, no lifecycle call, ever. Exists to make the creation boundary observable and
    recoverable in production: distinguishing "nothing created yet" from "mandate created,
    version still missing" (safely repairable by rerunning mandate-bootstrap-create with
    the same owner-input document) from genuine conflicts or broken audit trails that must
    never be silently resumed through."""
    mandate_idempotency_key = f"{idempotency_key}:create-mandate"
    version_idempotency_key = f"{idempotency_key}:create-version"

    conflicts: list[str] = []
    warnings: list[str] = []

    async with AsyncSessionLocal() as db:
        mandate_audit_rows = await _mandate_bootstrap_create_all_audits_by_idempotency(
            db=db, entity_type="autonomous_capital_mandate", action="MANDATE_CREATED", idempotency_key=mandate_idempotency_key
        )
        version_audit_rows = await _mandate_bootstrap_create_all_audits_by_idempotency(
            db=db,
            entity_type="autonomous_capital_mandate",
            action="MANDATE_VERSION_CREATED",
            idempotency_key=version_idempotency_key,
        )

        duplicate_key_conflict = False
        mandate_entity_ids = sorted({str(row.entity_id) for row in mandate_audit_rows if row.entity_id is not None})
        if len(mandate_entity_ids) > 1:
            duplicate_key_conflict = True
            conflicts.append(
                f"{len(mandate_entity_ids)} distinct mandates ({', '.join(mandate_entity_ids)}) share "
                f"idempotency_key {mandate_idempotency_key!r} -- concurrent creation race or manual tampering."
            )

        mandate_audit_event = _mandate_bootstrap_create_serialize_audit_event(mandate_audit_rows[0]) if mandate_audit_rows else None
        version_audit_event = _mandate_bootstrap_create_serialize_audit_event(version_audit_rows[0]) if version_audit_rows else None

        audit_broken_reference = False
        mandate: AutonomousCapitalMandate | None = None
        if mandate_audit_rows and mandate_audit_rows[0].entity_id is not None:
            mandate = await db.get(AutonomousCapitalMandate, mandate_audit_rows[0].entity_id)
            if mandate is None:
                audit_broken_reference = True
                conflicts.append("mandate audit record references a mandate row that no longer exists.")

        version: AutonomousCapitalMandateVersion | None = None
        if version_audit_rows:
            version_id_raw = (version_audit_rows[0].after_state or {}).get("mandate_version_id")
            if isinstance(version_id_raw, str):
                try:
                    version = await db.get(AutonomousCapitalMandateVersion, uuid.UUID(version_id_raw))
                except ValueError:
                    version = None
            if version is None:
                audit_broken_reference = True
                conflicts.append("mandate version audit record references a version row that no longer exists.")
            elif mandate is not None and version.mandate_id != mandate.mandate_id:
                audit_broken_reference = True
                conflicts.append("mandate version row's mandate_id does not match the audited mandate.")

        identity_drift_conflict = False
        if mandate is not None:
            export = await mandate_bootstrap_export(capital_campaign_id=capital_campaign_id)
            if not export["campaign"].get("found", False):
                warnings.append(
                    f"capital_campaign_id={capital_campaign_id} no longer resolves; cannot confirm "
                    "continued identity coherence against a campaign that may have been deleted."
                )
            else:
                drift = _mandate_bootstrap_create_identity_drift(mandate=mandate, export_fields=export["fields"])
                if drift:
                    identity_drift_conflict = True
                    conflicts.extend(f"campaign identity drift -- {item}" for item in drift)

        audit_coherent = not audit_broken_reference
        identity_coherent = not identity_drift_conflict

    if duplicate_key_conflict or identity_drift_conflict:
        overall_status = "CONFLICT"
    elif audit_broken_reference:
        overall_status = "INCOHERENT"
    elif mandate is None:
        overall_status = "NOT_STARTED"
    elif version is None:
        overall_status = "PARTIAL_RECOVERABLE"
    else:
        overall_status = "COMPLETE_DRAFT"
        if mandate.status != "DRAFT":
            warnings.append(
                f"mandate.status has progressed to {mandate.status!r}; this command reports only on "
                "creation-stage integrity, not on subsequent lifecycle state."
            )

    creation_complete = overall_status == "COMPLETE_DRAFT"
    recovery_required = overall_status == "PARTIAL_RECOVERABLE"

    next_safe_action = {
        "NOT_STARTED": "Run mandate-bootstrap-create to begin creation.",
        "PARTIAL_RECOVERABLE": (
            "Rerun mandate-bootstrap-create with the exact same owner-input document to "
            "safely create only the missing initial version; create_mandate()'s own "
            "idempotency lookup guarantees no duplicate mandate is created."
        ),
        "COMPLETE_DRAFT": (
            "Creation is complete. No action required from this command; authorization and "
            "activation are separate stages requiring explicit human action, not yet "
            "implemented by any command in this pipeline."
        ),
        "CONFLICT": (
            "Do not rerun mandate-bootstrap-create under this idempotency_key. Investigate "
            "the reported conflicts before choosing a new idempotency_key or taking any "
            "further action."
        ),
        "INCOHERENT": (
            "Do not take any further action. The audit trail itself is broken or "
            "self-contradictory; investigate the underlying database state directly."
        ),
    }[overall_status]

    return {
        "overall_status": overall_status,
        "capital_campaign_id": capital_campaign_id,
        "idempotency_key": idempotency_key,
        "mandate_id": str(mandate.mandate_id) if mandate is not None else None,
        "mandate_version_id": str(version.mandate_version_id) if version is not None else None,
        "mandate_status": mandate.status if mandate is not None else None,
        "mandate_version_number": version.version_number if version is not None else None,
        "mandate_audit_event": mandate_audit_event,
        "mandate_version_audit_event": version_audit_event,
        "identity_coherent": identity_coherent,
        "audit_coherent": audit_coherent,
        "creation_complete": creation_complete,
        "recovery_required": recovery_required,
        "transaction_model": _mandate_bootstrap_create_transaction_model(),
        "conflicts": conflicts,
        "warnings": warnings,
        "next_safe_action": next_safe_action,
    }


async def mandate_bootstrap_commission(*, capital_campaign_id: int, owner_input: dict[str, Any]) -> dict[str, Any]:
    """Stage 9A.2: orchestrates the existing, already-governed Stage 6-9A.1 pipeline to
    commission exactly one DRAFT mandate -- reuses every step as-is and adds no new
    validation, resolution, or write logic of its own. Sequence: (1)
    mandate_governance_readiness_audit() -- if not READY_FOR_STAGE9, aborts with zero
    writes; (2) mandate_bootstrap_create() -- itself reusing
    mandate_bootstrap_export()/mandate_bootstrap_session_validate() and only writing if
    validation succeeds, with its own idempotency-conflict guard from Stage 9A.1; (3) on a
    genuine CREATED result, mandate_bootstrap_create_status() to independently re-verify
    the just-created state is coherent from the database's own perspective, not merely
    trusted from the write call's in-memory return value. Never calls
    apply_mandate_lifecycle_action(), authorize_mandate_version(), mandate_bootstrap(), or
    any order-execution/autonomous-cycle function -- this function references none of
    them."""
    audit = await mandate_governance_readiness_audit(capital_campaign_id=capital_campaign_id)
    transaction_model = _mandate_bootstrap_create_transaction_model()

    if audit["overall_status"] != "READY_FOR_STAGE9":
        return {
            "overall_status": "ABORTED_NOT_READY",
            "mandate_id": None,
            "mandate_version_id": None,
            "audit_summary": {
                "governance_audit_status": audit["overall_status"],
                "write_paths": audit["write_paths"],
                "writes_performed": False,
            },
            "integrity_status": None,
            "transaction_model": transaction_model,
            "current_state": None,
            "next_required_action": (
                "mandate-governance-readiness-audit reported NOT_READY for this campaign's "
                "pipeline. Resolve the reported write_paths/failing checks (see "
                "mandate-governance-readiness-audit's own output) before attempting "
                "commissioning again. Zero writes were performed."
            ),
        }

    creation = await mandate_bootstrap_create(capital_campaign_id=capital_campaign_id, owner_input=owner_input)

    if creation["overall_status"] != "CREATED":
        return {
            "overall_status": creation["overall_status"],
            "mandate_id": creation["mandate_id"],
            "mandate_version_id": creation["mandate_version_id"],
            "audit_summary": {
                "governance_audit_status": audit["overall_status"],
                **creation["audit_summary"],
            },
            "integrity_status": None,
            "transaction_model": creation["transaction_model"],
            "current_state": None,
            "next_required_action": creation["next_required_action"],
        }

    root_idempotency_key = creation["audit_summary"]["root_idempotency_key"]
    status = await mandate_bootstrap_create_status(
        capital_campaign_id=capital_campaign_id, idempotency_key=root_idempotency_key
    )

    current_state = {
        "mandate_status": creation["write_summary"]["mandate_status"],
        "is_authorized": creation["write_summary"]["mandate_version_is_authorized"],
        "is_active": creation["write_summary"]["mandate_version_is_active"],
    }

    if status["overall_status"] == "COMPLETE_DRAFT":
        overall_status = "COMMISSIONED"
        next_required_action = (
            "Exactly one production DRAFT mandate and its initial version now exist, "
            "independently re-verified coherent via mandate-bootstrap-create-status. "
            "Status remains DRAFT/unauthorized/inactive. A human operator must separately "
            "and explicitly authorize and activate this mandate -- no command in this "
            "pipeline does so automatically, and none was called here."
        )
    else:
        overall_status = "COMMISSIONED_INTEGRITY_WARNING"
        next_required_action = (
            f"Creation succeeded but the independent coherence re-check reported "
            f"{status['overall_status']} rather than COMPLETE_DRAFT. Do not proceed to "
            f"authorization. Investigate via mandate-bootstrap-create-status "
            f"(conflicts={status['conflicts']!r}) before any further action."
        )

    return {
        "overall_status": overall_status,
        "mandate_id": creation["mandate_id"],
        "mandate_version_id": creation["mandate_version_id"],
        "audit_summary": {
            "governance_audit_status": audit["overall_status"],
            **creation["audit_summary"],
        },
        "integrity_status": status["overall_status"],
        "transaction_model": creation["transaction_model"],
        "current_state": current_state,
        "next_required_action": next_required_action,
    }


# --- mandate-lifecycle command family -----------------------------------------------
# Distinct from mandate-bootstrap-*: these commands act on a mandate that already
# exists (created by mandate-bootstrap-create/-commission), advancing it through
# SUBMIT_FOR_AUTHORIZATION -> authorize -> ACTIVATE. "bootstrap" in this codebase means
# creation only, ending at DRAFT -- these commands intentionally live under a separate
# name so that boundary stays legible from the command name alone.

_MANDATE_LIFECYCLE_AUTHORIZE_REQUIRED_FIELDS: tuple[str, ...] = (
    "actor",
    "reason",
    "authorization_method",
    "owner_acknowledgements",
    "authorization_evidence",
    "deterministic_explanation",
    "idempotency_key",
)


def _mandate_lifecycle_coerce_owner_input(owner_input: dict[str, Any]) -> dict[str, Any]:
    """Validates and coerces the owner-input document for mandate_lifecycle_authorize()
    using the exact same per-field type-checking helper (_coerce_worksheet_field_value)
    and the exact same worksheet entries Stage 7's session validator already defined for
    these identical field names -- not a new, duplicated set of rules for the same
    concepts."""
    missing_fields = sorted(name for name in _MANDATE_LIFECYCLE_AUTHORIZE_REQUIRED_FIELDS if name not in owner_input)
    field_errors: list[dict[str, str]] = []
    coerced: dict[str, Any] = {}
    for name in _MANDATE_LIFECYCLE_AUTHORIZE_REQUIRED_FIELDS:
        if name not in owner_input:
            continue
        entry = _MANDATE_BOOTSTRAP_EXPORT_WORKSHEET_ENTRIES[name]
        value, error = _coerce_worksheet_field_value(
            input_type=entry["input_type"], raw=owner_input[name], accepted_values=entry["accepted_values"]
        )
        if error is not None:
            field_errors.append({"field": name, "error": error})
            continue
        coerced[name] = value

    optional_coerced: dict[str, Any] = {}
    if "authorization_expires_at" in owner_input:
        value, error = _coerce_optional_timestamp(owner_input["authorization_expires_at"])
        if error is not None:
            field_errors.append({"field": "authorization_expires_at", "error": error})
        elif value is not None:
            optional_coerced["authorization_expires_at"] = value
    if "audit_correlation_id" in owner_input:
        value, error = _coerce_optional_uuid(owner_input["audit_correlation_id"])
        if error is not None:
            field_errors.append({"field": "audit_correlation_id", "error": error})
        elif value is not None:
            optional_coerced["audit_correlation_id"] = value

    known_names = set(_MANDATE_LIFECYCLE_AUTHORIZE_REQUIRED_FIELDS) | {"authorization_expires_at", "audit_correlation_id"}
    unexpected_fields = sorted(name for name in owner_input if name not in known_names)
    field_errors = sorted(field_errors, key=lambda entry: (entry["field"], entry["error"]))

    return {
        "coerced": coerced,
        "optional_coerced": optional_coerced,
        "missing_fields": missing_fields,
        "field_errors": field_errors,
        "unexpected_fields": unexpected_fields,
    }


def _mandate_lifecycle_not_found_payload(*, mandate_id: UUID, mandate_version_id: UUID, reason: str) -> dict[str, Any]:
    return {
        "overall_status": "FAILED_VALIDATION",
        "mandate_id": str(mandate_id),
        "mandate_version_id": str(mandate_version_id),
        "mandate_authorization_id": None,
        "mandate_status": None,
        "write_summary": {"submitted_for_authorization": False, "authorized": False},
        "validation": {"field_errors": [{"field": "mandate_id", "error": reason}], "missing_fields": [], "unexpected_fields": []},
        "next_required_action": "Verify --mandate-id and --mandate-version-id. Zero writes were performed.",
    }


async def mandate_lifecycle_authorize(
    *, mandate_id: UUID, mandate_version_id: UUID, owner_input: dict[str, Any]
) -> dict[str, Any]:
    """mandate-lifecycle family: submits an existing DRAFT mandate for authorization (if
    not already submitted) and records a genuine authorization event against a specific
    existing version. Reuses apply_mandate_lifecycle_action()/authorize_mandate_version()
    exactly as mandate_bootstrap() itself does for these same two stages -- no new
    lifecycle rule, transition, or validator is introduced; mandate.status transition
    validity is enforced entirely by those existing functions and by
    validate_mandate_state_transition(). Never activates, never calls mandate_bootstrap(),
    order-execution, or autonomous-cycle functions. Idempotent on the owner-supplied
    idempotency_key; reusing that key with materially different authorization evidence
    fails closed as overall_status=CONFLICT with zero new writes -- the same governance
    rule Stage 9A.1 already applies to mandate creation, applied one stage later."""
    validation = _mandate_lifecycle_coerce_owner_input(owner_input)
    if validation["missing_fields"] or validation["field_errors"] or validation["unexpected_fields"]:
        return {
            "overall_status": "FAILED_VALIDATION",
            "mandate_id": str(mandate_id),
            "mandate_version_id": str(mandate_version_id),
            "mandate_authorization_id": None,
            "mandate_status": None,
            "write_summary": {"submitted_for_authorization": False, "authorized": False},
            "validation": {
                "missing_fields": validation["missing_fields"],
                "field_errors": validation["field_errors"],
                "unexpected_fields": validation["unexpected_fields"],
            },
            "next_required_action": (
                "Resolve the validation failures reported under validation and retry "
                "mandate-lifecycle-authorize. Zero writes were performed."
            ),
        }

    coerced = validation["coerced"]
    optional_coerced = validation["optional_coerced"]
    root_idempotency_key = coerced["idempotency_key"]
    actor = coerced["actor"]

    async with AsyncSessionLocal() as db:
        mandate = await db.get(AutonomousCapitalMandate, mandate_id)
        if mandate is None:
            return _mandate_lifecycle_not_found_payload(
                mandate_id=mandate_id, mandate_version_id=mandate_version_id, reason="mandate_not_found"
            )

        version = await db.get(AutonomousCapitalMandateVersion, mandate_version_id)
        if version is None or version.mandate_id != mandate.mandate_id:
            return _mandate_lifecycle_not_found_payload(
                mandate_id=mandate_id,
                mandate_version_id=mandate_version_id,
                reason="mandate_version_not_found_or_mismatched",
            )

        # AUTHORIZED is included alongside the two pre-authorization states so that a
        # rerun with the exact same idempotency_key (already-authorized, no-op resume)
        # is never rejected by this pre-check before authorize_mandate_version()'s own
        # idempotency lookup even gets a chance to run -- authorize_mandate_version()
        # itself already accepts AUTHORIZED for exactly this reason.
        if mandate.status not in {"DRAFT", "PENDING_AUTHORIZATION", "AUTHORIZED"}:
            return {
                "overall_status": "FAILED_VALIDATION",
                "mandate_id": str(mandate_id),
                "mandate_version_id": str(mandate_version_id),
                "mandate_authorization_id": None,
                "mandate_status": mandate.status,
                "write_summary": {"submitted_for_authorization": False, "authorized": False},
                "validation": {
                    "field_errors": [{"field": "mandate_id", "error": "mandate_not_in_authorizable_state"}],
                    "missing_fields": [],
                    "unexpected_fields": [],
                },
                "next_required_action": (
                    f"mandate.status is {mandate.status!r}; mandate-lifecycle-authorize only "
                    "operates on a mandate in DRAFT or PENDING_AUTHORIZATION. Zero writes were performed."
                ),
            }

        authorize_idempotency_key = f"{root_idempotency_key}:authorize"
        existing_authorization = await db.scalar(
            select(AutonomousCapitalMandateAuthorization)
            .where(AutonomousCapitalMandateAuthorization.idempotency_key == authorize_idempotency_key)
            .limit(1)
        )
        if existing_authorization is not None:
            mismatched_fields = sorted(
                field_name
                for field_name, existing_value in (
                    ("authorization_method", existing_authorization.authorization_method),
                    ("owner_acknowledgements", existing_authorization.owner_acknowledgements),
                    ("authorization_evidence", existing_authorization.authorization_evidence),
                    ("deterministic_explanation", existing_authorization.deterministic_explanation),
                )
                if existing_value != coerced[field_name]
            )
            if mismatched_fields:
                return {
                    "overall_status": "CONFLICT",
                    "mandate_id": str(mandate_id),
                    "mandate_version_id": str(mandate_version_id),
                    "mandate_authorization_id": str(existing_authorization.mandate_authorization_id),
                    "mandate_status": mandate.status,
                    "write_summary": {"submitted_for_authorization": False, "authorized": False},
                    "conflict": {
                        "reason": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_AUTHORIZATION_EVIDENCE",
                        "idempotency_key": root_idempotency_key,
                        "mismatched_fields": mismatched_fields,
                    },
                    "next_required_action": (
                        "This idempotency_key was already used to record different authorization "
                        "evidence. Choose a new idempotency_key, or resupply the exact original "
                        "owner-input document. Zero new writes were performed."
                    ),
                }

        submitted = False
        if mandate.status == "DRAFT":
            mandate = await apply_mandate_lifecycle_action(
                db=db,
                request=MandateLifecycleActionRequest(
                    mandate_id=mandate.mandate_id,
                    actor=actor,
                    action="SUBMIT_FOR_AUTHORIZATION",
                    reason=coerced["reason"],
                    idempotency_key=f"{root_idempotency_key}:submit-for-authorization",
                    audit_correlation_id=optional_coerced.get("audit_correlation_id"),
                ),
            )
            submitted = True

        authorization = await authorize_mandate_version(
            db=db,
            request=MandateAuthorizationRequest(
                mandate_id=mandate.mandate_id,
                mandate_version_id=version.mandate_version_id,
                actor=actor,
                authorization_method=coerced["authorization_method"],
                owner_acknowledgements=coerced["owner_acknowledgements"],
                authorization_evidence=coerced["authorization_evidence"],
                deterministic_explanation=coerced["deterministic_explanation"],
                expires_at=optional_coerced.get("authorization_expires_at"),
                idempotency_key=authorize_idempotency_key,
                audit_correlation_id=optional_coerced.get("audit_correlation_id"),
            ),
        )

    return {
        "overall_status": "AUTHORIZED",
        "mandate_id": str(mandate_id),
        "mandate_version_id": str(mandate_version_id),
        "mandate_authorization_id": str(authorization.mandate_authorization_id),
        "mandate_status": mandate.status,
        "write_summary": {
            "submitted_for_authorization": submitted,
            "authorized": True,
            "authorization_state": authorization.authorization_state,
        },
        "next_required_action": (
            "This mandate version is now authorized. It remains inactive until a separate, "
            "explicit mandate-lifecycle-activate call. No activation, order execution, or "
            "autonomous cycle has occurred."
        ),
    }


async def mandate_lifecycle_status(*, mandate_id: UUID) -> dict[str, Any]:
    """mandate-lifecycle family: read-only. Independently reports the current lifecycle,
    authorization, and activation state of an existing mandate, re-derived from the
    database -- the standing verification tool for mandate_lifecycle_authorize()/
    mandate_lifecycle_activate(), the same role mandate_bootstrap_create_status() already
    plays for the creation phase. Reuses _mandate_bootstrap_create_identity_drift() as-is
    for the campaign-identity coherence check. Performs zero writes."""
    conflicts: list[str] = []
    warnings: list[str] = []

    async with AsyncSessionLocal() as db:
        mandate = await db.get(AutonomousCapitalMandate, mandate_id)
        if mandate is None:
            return {
                "overall_status": "NOT_FOUND",
                "mandate_id": str(mandate_id),
                "mandate_status": None,
                "mandate_version_id": None,
                "mandate_version_number": None,
                "is_authorized": None,
                "is_active": None,
                "latest_authorization_state": None,
                "identity_coherent": None,
                "audit_coherent": None,
                "conflicts": [],
                "warnings": ["mandate not found for this mandate_id"],
            }

        versions = list(
            await db.scalars(
                select(AutonomousCapitalMandateVersion)
                .where(AutonomousCapitalMandateVersion.mandate_id == mandate_id)
                .order_by(AutonomousCapitalMandateVersion.version_number.desc())
            )
        )
        governing_version = next((item for item in versions if item.is_active), None)
        version = governing_version or (versions[0] if versions else None)

        audit_coherent = True
        if version is not None and version.mandate_id != mandate.mandate_id:
            audit_coherent = False
            conflicts.append("version.mandate_id does not match mandate_id")

        authorizations = list(
            await db.scalars(
                select(AutonomousCapitalMandateAuthorization)
                .where(AutonomousCapitalMandateAuthorization.mandate_id == mandate_id)
                .order_by(AutonomousCapitalMandateAuthorization.recorded_at.desc())
            )
        )
        latest_authorization = authorizations[0] if authorizations else None
        if (
            latest_authorization is not None
            and version is not None
            and latest_authorization.mandate_version_id != version.mandate_version_id
        ):
            warnings.append("latest authorization references a different version than the current governing/latest version")

        identity_coherent = True
        if mandate.capital_campaign_id is not None:
            export = await mandate_bootstrap_export(capital_campaign_id=mandate.capital_campaign_id)
            if not export["campaign"].get("found", False):
                warnings.append(
                    f"capital_campaign_id={mandate.capital_campaign_id} no longer resolves; cannot "
                    "confirm continued identity coherence against a campaign that may have been deleted."
                )
            else:
                drift = _mandate_bootstrap_create_identity_drift(mandate=mandate, export_fields=export["fields"])
                if drift:
                    identity_coherent = False
                    conflicts.extend(f"campaign identity drift -- {item}" for item in drift)

        overall_status = "CONFLICT" if conflicts else "OK"

        return {
            "overall_status": overall_status,
            "mandate_id": str(mandate_id),
            "mandate_status": mandate.status,
            "mandate_version_id": str(version.mandate_version_id) if version is not None else None,
            "mandate_version_number": version.version_number if version is not None else None,
            "is_authorized": bool(version.is_authorized) if version is not None else None,
            "is_active": bool(version.is_active) if version is not None else None,
            "latest_authorization_state": latest_authorization.authorization_state if latest_authorization is not None else None,
            "identity_coherent": identity_coherent,
            "audit_coherent": audit_coherent,
            "conflicts": conflicts,
            "warnings": warnings,
        }


async def mandate_lifecycle_activate(*, mandate_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    """mandate-lifecycle family: activates an existing, already-authorized mandate.
    Reuses apply_mandate_lifecycle_action(action=ACTIVATE) and
    _load_governing_authorized_version() exactly as mandate_bootstrap() and
    apply_mandate_lifecycle_action() itself already use them for this stage -- no new
    transition rule is introduced. Never authorizes, never creates, never touches order
    execution or the autonomous cycle."""
    async with AsyncSessionLocal() as db:
        mandate = await db.get(AutonomousCapitalMandate, mandate_id)
        if mandate is None:
            return {
                "overall_status": "FAILED_VALIDATION",
                "mandate_id": str(mandate_id),
                "mandate_status": None,
                "governing_mandate_version_id": None,
                "validation": {"field_errors": [{"field": "mandate_id", "error": "mandate_not_found"}]},
                "next_required_action": "No mandate exists for this mandate_id. Zero writes were performed.",
            }

        activate_idempotency_key = f"{idempotency_key}:activate"
        already_activated = await _find_audit_by_idempotency(
            db=db, entity_type="autonomous_capital_mandate", action="MANDATE_ACTIVATE", idempotency_key=activate_idempotency_key
        )
        # A matching prior activation is a no-op resume -- apply_mandate_lifecycle_action()
        # will itself short-circuit on this same idempotency_key regardless of the
        # mandate's current status (already ACTIVE), so the transition pre-check below
        # must not reject that legitimate resume before the real function even sees it.
        if already_activated is None:
            transition = validate_mandate_state_transition(from_status=mandate.status, to_status="ACTIVE")
            if not transition.valid:
                return {
                    "overall_status": "FAILED_VALIDATION",
                    "mandate_id": str(mandate_id),
                    "mandate_status": mandate.status,
                    "governing_mandate_version_id": None,
                    "validation": {
                        "field_errors": [
                            {"field": "mandate_id", "error": transition.reason or "invalid_mandate_state_transition"}
                        ]
                    },
                    "next_required_action": (
                        f"mandate.status is {mandate.status!r}; the transition to ACTIVE is not valid "
                        "from this state. Zero writes were performed."
                    ),
                }

        governing_version = await _load_governing_authorized_version(db=db, mandate_id=mandate.mandate_id)
        if governing_version is None:
            return {
                "overall_status": "FAILED_VALIDATION",
                "mandate_id": str(mandate_id),
                "mandate_status": mandate.status,
                "governing_mandate_version_id": None,
                "validation": {"field_errors": [{"field": "mandate_id", "error": "no_authorized_mandate_version"}]},
                "next_required_action": (
                    "No authorized mandate version exists for this mandate. Run "
                    "mandate-lifecycle-authorize first. Zero writes were performed."
                ),
            }

        mandate = await apply_mandate_lifecycle_action(
            db=db,
            request=MandateLifecycleActionRequest(
                mandate_id=mandate.mandate_id,
                actor=actor,
                action="ACTIVATE",
                reason=reason,
                idempotency_key=activate_idempotency_key,
            ),
        )

        return {
            "overall_status": "ACTIVE",
            "mandate_id": str(mandate_id),
            "mandate_status": mandate.status,
            "governing_mandate_version_id": str(governing_version.mandate_version_id),
            "next_required_action": (
                "This mandate is now ACTIVE. Order execution and autonomous evaluation are "
                "governed by separate, existing pipelines (e.g. canonical-proving-commission) "
                "-- this command does not itself trigger any of them."
            ),
        }


async def mandate_lifecycle_commission(
    *,
    capital_campaign_id: int,
    mandate_id: UUID,
    action: str,
    mandate_version_id: UUID | None = None,
    owner_input: dict[str, Any] | None = None,
    actor: str | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """mandate-lifecycle family: orchestrates mandate_governance_readiness_audit() ->
    mandate_lifecycle_authorize()/mandate_lifecycle_activate() (selected by `action`) ->
    mandate_lifecycle_status(), mirroring mandate_bootstrap_commission()'s own
    audit-then-write-then-independently-reverify shape exactly. Adds no new validation,
    resolution, or write logic of its own -- pure orchestration of the three functions
    above."""
    if action not in {"AUTHORIZE", "ACTIVATE"}:
        raise ValueError("action must be AUTHORIZE or ACTIVATE")

    audit = await mandate_governance_readiness_audit(capital_campaign_id=capital_campaign_id)
    if audit["overall_status"] != "READY_FOR_STAGE9":
        return {
            "overall_status": "ABORTED_NOT_READY",
            "mandate_id": str(mandate_id),
            "action": action,
            "audit_summary": {"governance_audit_status": audit["overall_status"], "write_paths": audit["write_paths"]},
            "lifecycle_status": None,
            "next_required_action": (
                "mandate-governance-readiness-audit reported NOT_READY. Resolve the reported "
                "issues before attempting this lifecycle action again. Zero writes were performed."
            ),
        }

    if action == "AUTHORIZE":
        if mandate_version_id is None or owner_input is None:
            raise ValueError("mandate_version_id and owner_input are required for action=AUTHORIZE")
        write_result = await mandate_lifecycle_authorize(
            mandate_id=mandate_id, mandate_version_id=mandate_version_id, owner_input=owner_input
        )
        success_status = "AUTHORIZED"
    else:
        if actor is None or reason is None or idempotency_key is None:
            raise ValueError("actor, reason, and idempotency_key are required for action=ACTIVATE")
        write_result = await mandate_lifecycle_activate(
            mandate_id=mandate_id, actor=actor, reason=reason, idempotency_key=idempotency_key
        )
        success_status = "ACTIVE"

    status = await mandate_lifecycle_status(mandate_id=mandate_id)

    if write_result["overall_status"] != success_status:
        return {
            "overall_status": write_result["overall_status"],
            "mandate_id": str(mandate_id),
            "action": action,
            "audit_summary": {"governance_audit_status": audit["overall_status"]},
            "write_result": write_result,
            "lifecycle_status": status,
            "next_required_action": write_result["next_required_action"],
        }

    overall_status = "COMMISSIONED" if status["overall_status"] == "OK" else "COMMISSIONED_INTEGRITY_WARNING"

    return {
        "overall_status": overall_status,
        "mandate_id": str(mandate_id),
        "action": action,
        "audit_summary": {"governance_audit_status": audit["overall_status"]},
        "write_result": write_result,
        "lifecycle_status": status,
        "next_required_action": write_result["next_required_action"],
    }


async def _resolve_exchange_connection_for_commissioning(
    *,
    db,
    preview_exchange_connection_id: UUID,
    provider: str,
    environment: str,
) -> tuple[ExchangeConnection | None, str | None]:
    normalized_provider = provider.strip().lower()
    normalized_environment = environment.strip().lower()

    by_preview = await _load_exchange_connection_by_id(db=db, exchange_connection_id=preview_exchange_connection_id)
    if by_preview is not None:
        matches_provider = str(getattr(by_preview, "provider", "")).strip().lower() == normalized_provider
        matches_environment = str(getattr(by_preview, "environment", "")).strip().lower() == normalized_environment
        if not (matches_provider and matches_environment):
            return None, "provider/environment mismatch"
        return by_preview, None

    scoped = list(
        (
            await db.execute(
                select(ExchangeConnection)
                .where(func.lower(ExchangeConnection.provider) == normalized_provider)
                .where(func.lower(ExchangeConnection.environment) == normalized_environment)
                .order_by(ExchangeConnection.created_at.desc(), ExchangeConnection.exchange_connection_id.desc())
                .limit(2)
            )
        ).scalars().all()
    )
    if not scoped:
        return None, "exchange connection missing"
    if len(scoped) > 1:
        return None, "exchange connection identity mismatch"
    return scoped[0], None


async def _load_authorized_mandate_version(*, db, mandate_id: UUID) -> AutonomousCapitalMandateVersion | None:
    authorized = await db.scalar(
        select(AutonomousCapitalMandateAuthorization)
        .where(AutonomousCapitalMandateAuthorization.mandate_id == mandate_id)
        .where(AutonomousCapitalMandateAuthorization.authorization_state == "AUTHORIZED")
        .where(AutonomousCapitalMandateAuthorization.revoked_at.is_(None))
        .order_by(AutonomousCapitalMandateAuthorization.recorded_at.desc())
        .limit(1)
    )
    if authorized is not None:
        version = await db.scalar(
            select(AutonomousCapitalMandateVersion)
            .where(AutonomousCapitalMandateVersion.mandate_version_id == authorized.mandate_version_id)
            .limit(1)
        )
        if version is not None:
            return version
    return await db.scalar(
        select(AutonomousCapitalMandateVersion)
        .where(AutonomousCapitalMandateVersion.mandate_id == mandate_id)
        .order_by(AutonomousCapitalMandateVersion.version_number.desc())
        .limit(1)
    )


async def _load_asset_for_product_symbol(*, db, product: str, provider: str) -> Asset | None:
    base_symbol = _normalize_instrument(product).split("-", 1)[0]
    return await db.scalar(
        select(Asset)
        .where(Asset.symbol == base_symbol)
        .where(Asset.exchange == provider)
        .where(Asset.asset_class == "crypto")
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc())
        .limit(1)
    )


def _package_requires_refresh(
    *,
    package: CanonicalPreviewPackage | None,
    preview: CryptoOrderPreview | None,
    approval_event: LiveApprovalEvent | None,
    activation: CanonicalProvingActivation | None,
    now: datetime,
) -> bool:
    if package is None or preview is None:
        return True
    if package.package_state in _TERMINAL_PACKAGE_STATES:
        return True
    if package.dry_run_live_crypto_order_id is not None and package.package_state in {"AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}:
        # Never supersede a post-dry-run package identity from commission resume flow.
        return False
    if package.preview_expires_at <= now + timedelta(seconds=_PROVING_REFRESH_GRACE_SECONDS):
        return True
    if package.package_state == "AUTHORIZED":
        return not _approval_is_active(approval_event, now=now)
    return False


def _commissioning_status_summary(*, blob: dict[str, Any]) -> dict[str, Any]:
    ownership = blob.get("ownership_reconciliation") if isinstance(blob.get("ownership_reconciliation"), dict) else {}
    authority_metadata = blob.get("authority_metadata") if isinstance(blob.get("authority_metadata"), dict) else {}
    return {
        "state": str(blob.get("state") or "DRAFT"),
        "commissioning": blob.get("commissioning") if isinstance(blob.get("commissioning"), dict) else {},
        "entry_execution": blob.get("entry_execution") if isinstance(blob.get("entry_execution"), dict) else {},
        "ownership_reconciliation": ownership,
        "autonomous_lifecycle_owner": (
            str(blob.get("state") or "").upper() == "ACTIVE_POSITION"
            and bool(ownership.get("position_identity"))
            and str(authority_metadata.get("lifecycle_authority") or "").strip().upper() == "OMNITRADE_AUTONOMOUS"
        ),
    }


def _build_commissioned_readiness_request(
    *,
    campaign_id: UUID,
    version: int,
    live_trading_profile_id: UUID,
    paper_account_id: UUID,
    provider: str,
    environment: str,
    product: str,
    requested_quote_amount: Decimal,
    idempotency_key: str,
    approval_event: LiveApprovalEvent,
    definition: CapitalCampaignDefinition,
    connection: ExchangeConnection,
    preview: CryptoOrderPreview,
    mandate: AutonomousCapitalMandate,
    mandate_version: AutonomousCapitalMandateVersion,
) -> CommissionedReadinessRequest:
    observed_at = getattr(connection, "last_verified_at", None) or preview.created_at
    balance_observed_at = getattr(connection, "last_successful_sync_at", None) or observed_at
    heartbeat_observed_at = getattr(connection, "last_heartbeat_at", None) or observed_at
    price_observed_at = preview.created_at
    reference_price = _preview_reference_price(preview)
    estimated_fee = Decimal(str(getattr(preview, "estimated_fee", None) or "0.01"))
    estimated_slippage = Decimal(str(getattr(preview, "estimated_slippage", None) or "0.01"))
    return CommissionedReadinessRequest(
        campaign_id=campaign_id,
        version=version,
        provider=provider,
        environment=environment,
        instrument=product,
        requested_quote_amount=requested_quote_amount,
        quote_currency="USD",
        idempotency_key=idempotency_key,
        live_trading_profile_id=live_trading_profile_id,
        account_id=paper_account_id,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        expected_mandate_version_number=mandate_version.version_number,
        expected_risk_policy_id=definition.risk_policy_id,
        expected_risk_policy_version=definition.risk_policy_version,
        approval_checkpoint_type="bounded_proving_entry",
        authorization_expires_at=approval_event.expires_at,
        provider_capability_evidence={"supported": bool(connection.credentials_valid), "observed_at": observed_at.isoformat(), "source": "exchange_connection"},
        connectivity_evidence={"reachable": str(connection.status or "").lower() == "connected", "observed_at": heartbeat_observed_at.isoformat(), "source": "exchange_connection"},
        balance_evidence={"available_quote_balance": _serialize_decimal_str(_extract_available_quote_balance(connection)), "observed_at": balance_observed_at.isoformat(), "source": "exchange_connection"},
        market_data_evidence={"observed_at": price_observed_at.isoformat(), "max_age_seconds": get_settings().live_crypto_price_max_age_seconds, "source": "canonical_preview"},
        price_evidence={"reference_price": _serialize_decimal_str(reference_price), "observed_at": price_observed_at.isoformat(), "max_age_seconds": get_settings().live_crypto_price_max_age_seconds, "source": "canonical_preview"},
        minimum_order_evidence={
            "minimum_quote_amount": _serialize_decimal_str(Decimal("5")),
            "minimum_base_quantity": _serialize_decimal_str(Decimal(str(mandate_version.entry_policy.get("minimum_base_quantity") or getattr(mandate_version, "min_base_quantity", None) or Decimal("0.00000001")))),
            "observed_at": observed_at.isoformat(),
            "source": "mandate_version",
        },
        fee_slippage_evidence={
            "estimated_entry_fee": _serialize_decimal_str(estimated_fee),
            "estimated_future_exit_fee": _serialize_decimal_str(estimated_fee),
            "estimated_slippage": _serialize_decimal_str(estimated_slippage),
            "source": "canonical_preview",
        },
        runtime_readiness_evidence={"ready": True, "observed_at": observed_at.isoformat(), "source": "canonical_preview_commission_command"},
        reconciliation_evidence={},
        manual_review_evidence={"required": False},
    )


def _strategy_identity(decision: DecisionRecord | None) -> tuple[str | None, str | None]:
    if decision is None:
        return None, None
    strategies = decision.supporting_strategies if isinstance(decision.supporting_strategies, list) else []
    for item in strategies:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("strategy_identity") or "").strip() or None
        version = str(item.get("strategy_version") or "").strip() or None
        if identity or version:
            return identity, version
    return None, None


def _strategy_fee_edge(decision: DecisionRecord | None) -> Decimal | None:
    if decision is None:
        return None
    strategies = decision.supporting_strategies if isinstance(decision.supporting_strategies, list) else []
    for item in strategies:
        if not isinstance(item, dict):
            continue
        gross = item.get("expected_gross_edge")
        fees = item.get("expected_fees")
        slippage = item.get("expected_slippage")
        if gross is None or fees is None or slippage is None:
            continue
        return Decimal(str(gross)) - Decimal(str(fees)) - Decimal(str(slippage))
    return None


def _as_of_market_data_valid(*, decision: DecisionRecord | None, snapshot: DecisionSnapshot | None) -> bool | None:
    if decision is None or snapshot is None:
        return None
    decision_ts = decision.timestamp if decision.timestamp.tzinfo is not None else decision.timestamp.replace(tzinfo=timezone.utc)
    snapshot_ts = snapshot.timestamp if snapshot.timestamp.tzinfo is not None else snapshot.timestamp.replace(tzinfo=timezone.utc)
    return snapshot_ts <= decision_ts


def _risk_verdict_from_event(event: RiskEvent | None) -> str | None:
    if event is None:
        return None
    action_taken = str(event.action_taken or "").strip().lower()
    if "veto" in action_taken or "reject" in action_taken or "block" in action_taken:
        return "VETO"
    if "allow" in action_taken or "pass" in action_taken or "approve" in action_taken:
        return "ALLOW"
    return None


def _strategy_authority_compatibility(*, definition: CapitalCampaignDefinition | None, strategy_identity: str | None) -> tuple[str, Any, Any]:
    if strategy_identity is None:
        return "UNKNOWN", "strategy identity known", None
    if definition is None or not isinstance(definition.metadata_evidence, dict):
        return "UNKNOWN", "no explicit strategy allowlist configured", None
    metadata = definition.metadata_evidence
    allowlist_keys = (
        "authorized_strategy_identities",
        "allowed_strategy_identities",
        "strategy_allowlist",
        "executable_strategy_identities",
    )
    allowlist: list[str] | None = None
    for key in allowlist_keys:
        value = metadata.get(key)
        if isinstance(value, list):
            allowlist = [str(item or "").strip() for item in value if str(item or "").strip()]
            break
    if allowlist is None:
        return "UNKNOWN", "no explicit strategy allowlist configured", None
    return ("PASSED" if strategy_identity in allowlist else "FAILED"), allowlist, strategy_identity


def _primary_blocker_from_gates(gates: dict[str, str]) -> str:
    if gates.get("historical_decision_exists") == "FAILED":
        return "DECISION_RECORD_MISSING"
    if gates.get("historical_action_is_buy") == "FAILED":
        return "DECISION_NOT_BUY"
    if gates.get("source_lineage_complete") == "FAILED":
        return "SOURCE_LINEAGE_INCOMPLETE"
    if gates.get("strategy_identity_resolved") == "FAILED":
        return "STRATEGY_IDENTITY_UNRESOLVED"
    if gates.get("strategy_authority_compatible") == "FAILED":
        return "STRATEGY_NOT_AUTHORIZED"
    if gates.get("market_data_as_of_time_valid") == "FAILED":
        return "MARKET_DATA_INVALID_AS_OF_TIME"
    if gates.get("campaign_definition_compatible") == "FAILED":
        return "CAMPAIGN_INCOMPATIBLE"
    if gates.get("runtime_binding_compatible") == "FAILED":
        return "RUNTIME_BINDING_INCOMPATIBLE"
    if gates.get("product_allowed") == "FAILED":
        return "PRODUCT_NOT_ALLOWED"
    if gates.get("provider_allowed") == "FAILED":
        return "PROVIDER_NOT_ALLOWED"
    if gates.get("confidence_threshold_passed") == "FAILED":
        return "CONFIDENCE_BELOW_THRESHOLD"
    if gates.get("lifecycle_allows_open_position") == "FAILED":
        return "LIFECYCLE_BLOCKED"
    if gates.get("risk_verdict_allows") == "FAILED":
        return "RISK_REJECTED"
    if gates.get("exact_five_dollar_candidate_possible") == "FAILED":
        return "FIVE_DOLLAR_SIZE_NOT_FEASIBLE"
    if gates.get("fee_adjusted_edge_positive") == "FAILED":
        return "FEE_ADJUSTED_EDGE_NOT_POSITIVE"
    if gates.get("no_simulated_order_conflict") == "FAILED":
        return "CONFLICTING_SIMULATED_STATE"
    if gates.get("canonical_package_eligibility_reached") == "PASSED":
        return "READY_PACKAGE_ELIGIBLE"
    return "OTHER:insufficient_evidence_for_deterministic_blocker"


def _gate_row(name: str, state: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"name": name, "state": state, "expected": expected, "actual": actual}


def _gate_from_bool(value: bool | None) -> str:
    if value is None:
        return "UNKNOWN"
    return "PASSED" if value else "FAILED"


def _historical_buy_campaign_replay_audit_payload(
    *,
    decision_id: UUID,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    signal: Signal | None,
    risk_event: RiskEvent | None,
    definition: CapitalCampaignDefinition | None,
    latest_version: int | None,
    runtime: CapitalCampaign | None,
    paper_account: PaperAccount | None,
    profile: LiveTradingProfile | None,
    open_live_order_count: int,
    matching_sell_decision: DecisionRecord | None,
    matching_sell_decision_id: UUID | None,
) -> dict[str, Any]:
    historical_action = _decision_action(decision)
    strategy_identity, strategy_version = _strategy_identity(decision)
    historical_confidence = None if decision is None or decision.confidence is None else Decimal(str(decision.confidence))
    confidence_threshold = None
    if definition is not None:
        confidence_threshold = _BUY_CONFIDENCE_BY_AGGRESSION_MODE.get(str(definition.aggression_mode or "").strip().upper())

    signal_lineage = _lineage_ids(None if decision is None else decision.source_lineage, key="signals")
    risk_lineage = _lineage_ids(None if decision is None else decision.source_lineage, key="risk_events")
    trade_lineage = _lineage_ids(None if decision is None else decision.source_lineage, key="trades")

    source_lineage_complete: bool | None = None
    if decision is not None:
        source_lineage_complete = bool(signal_lineage) and strategy_identity is not None

    strategy_authority_state, strategy_authority_expected, strategy_authority_actual = _strategy_authority_compatibility(
        definition=definition,
        strategy_identity=strategy_identity,
    )

    market_data_as_of_valid = _as_of_market_data_valid(decision=decision, snapshot=snapshot)
    decision_exists = decision is not None
    action_is_buy = None if decision is None else (historical_action == "BUY")

    campaign_definition_compatible: bool | None = None
    if definition is not None:
        campaign_definition_compatible = (latest_version is None) or (int(campaign_version) == int(latest_version))

    runtime_binding_compatible: bool | None = None
    if runtime is not None:
        runtime_binding_compatible = (
            int(runtime.id) == int(runtime_campaign_id)
            and str(runtime.uuid) == str(campaign_id)
            and int(runtime.definition_version) == int(campaign_version)
            and str(runtime.paper_account_id) == str(paper_account_id)
        )
        if profile is not None:
            runtime_binding_compatible = runtime_binding_compatible and (str(profile.paper_account_id) == str(paper_account_id))

    normalized_product = normalize_product_id(product_id)
    normalized_provider = str(provider or "").strip().lower()
    product_allowed: bool | None = None
    provider_allowed: bool | None = None
    if definition is not None:
        allowed_products = {str(item or "").strip().upper() for item in (definition.allowed_instruments or []) if str(item or "").strip()}
        allowed_providers = {str(item or "").strip().lower() for item in (definition.allowed_venues or []) if str(item or "").strip()}
        product_allowed = normalized_product in allowed_products
        provider_allowed = normalized_provider in allowed_providers

    confidence_threshold_passed: bool | None = None
    if confidence_threshold is not None and historical_confidence is not None:
        confidence_threshold_passed = historical_confidence >= confidence_threshold

    lifecycle_allows_open_position: bool | None = None
    if decision is not None:
        if decision.trade_accepted:
            lifecycle_allows_open_position = True
        elif decision.trade_rejected_reason:
            lifecycle_allows_open_position = False

    risk_verdict = _risk_verdict_from_event(risk_event)
    risk_verdict_allows: bool | None = None
    if risk_verdict is not None:
        risk_verdict_allows = risk_verdict == "ALLOW"

    exact_five_dollar_candidate_possible: bool | None = None
    if definition is not None:
        exact_five_dollar_candidate_possible = (
            int(definition.maximum_open_positions) == 1
            and Decimal(str(definition.minimum_position_size)) == Decimal("5")
            and Decimal(str(definition.maximum_position_size)) == Decimal("5")
            and Decimal(str(definition.maximum_total_exposure)) == Decimal("5")
        )

    fee_adjusted_edge = _strategy_fee_edge(decision)
    fee_adjusted_edge_positive: bool | None = None
    if fee_adjusted_edge is not None:
        fee_adjusted_edge_positive = fee_adjusted_edge > Decimal("0")

    no_simulated_order_conflict = open_live_order_count == 0

    gates = [
        _gate_row("historical_decision_exists", _gate_from_bool(decision_exists), str(decision_id), "found" if decision_exists else "missing"),
        _gate_row("historical_action_is_buy", _gate_from_bool(action_is_buy), "BUY-equivalent", historical_action),
        _gate_row("source_lineage_complete", _gate_from_bool(source_lineage_complete), "signal lineage + strategy identity", {
            "signal_ids": [str(item) for item in signal_lineage],
            "risk_event_ids": [str(item) for item in risk_lineage],
            "trade_ids": [str(item) for item in trade_lineage],
            "strategy_identity": strategy_identity,
        }),
        _gate_row("strategy_identity_resolved", _gate_from_bool(strategy_identity is not None if decision is not None else None), "non-empty strategy identity", strategy_identity),
        _gate_row("strategy_authority_compatible", strategy_authority_state, strategy_authority_expected, strategy_authority_actual),
        _gate_row("market_data_as_of_time_valid", _gate_from_bool(market_data_as_of_valid), "snapshot.timestamp <= decision.timestamp", {
            "decision_timestamp": None if decision is None else decision.timestamp.isoformat(),
            "snapshot_timestamp": None if snapshot is None else snapshot.timestamp.isoformat(),
        }),
        _gate_row("campaign_definition_compatible", _gate_from_bool(campaign_definition_compatible), {
            "campaign_id": str(campaign_id),
            "campaign_version": int(campaign_version),
            "latest_version": latest_version,
        }, None if definition is None else {"definition_version": int(definition.version)}),
        _gate_row("runtime_binding_compatible", _gate_from_bool(runtime_binding_compatible), {
            "runtime_campaign_id": int(runtime_campaign_id),
            "campaign_id": str(campaign_id),
            "campaign_version": int(campaign_version),
            "paper_account_id": str(paper_account_id),
            "live_trading_profile_id": str(live_trading_profile_id),
        }, None if runtime is None else {
            "runtime_campaign_id": int(runtime.id),
            "runtime_uuid": str(runtime.uuid),
            "runtime_definition_version": int(runtime.definition_version),
            "runtime_paper_account_id": str(runtime.paper_account_id),
            "profile_paper_account_id": None if profile is None else str(profile.paper_account_id),
        }),
        _gate_row("product_allowed", _gate_from_bool(product_allowed), normalized_product, None if definition is None else list(definition.allowed_instruments or [])),
        _gate_row("provider_allowed", _gate_from_bool(provider_allowed), normalized_provider, None if definition is None else list(definition.allowed_venues or [])),
        _gate_row("confidence_threshold_passed", _gate_from_bool(confidence_threshold_passed), None if confidence_threshold is None else format(confidence_threshold, "f"), None if historical_confidence is None else format(historical_confidence, "f")),
        _gate_row("lifecycle_allows_open_position", _gate_from_bool(lifecycle_allows_open_position), "decision accepted or non-blocked lifecycle", None if decision is None else {
            "trade_accepted": bool(decision.trade_accepted),
            "trade_rejected_reason": decision.trade_rejected_reason,
        }),
        _gate_row("risk_verdict_allows", _gate_from_bool(risk_verdict_allows), "ALLOW", None if risk_event is None else {
            "risk_event_id": str(risk_event.id),
            "action_taken": risk_event.action_taken,
            "event_type": risk_event.event_type,
            "derived_verdict": risk_verdict,
        }),
        _gate_row("exact_five_dollar_candidate_possible", _gate_from_bool(exact_five_dollar_candidate_possible), {
            "maximum_open_positions": 1,
            "minimum_position_size": "5",
            "maximum_position_size": "5",
            "maximum_total_exposure": "5",
        }, None if definition is None else {
            "maximum_open_positions": int(definition.maximum_open_positions),
            "minimum_position_size": format(Decimal(str(definition.minimum_position_size)), "f"),
            "maximum_position_size": format(Decimal(str(definition.maximum_position_size)), "f"),
            "maximum_total_exposure": format(Decimal(str(definition.maximum_total_exposure)), "f"),
        }),
        _gate_row("fee_adjusted_edge_positive", _gate_from_bool(fee_adjusted_edge_positive), "expected_gross_edge - expected_fees - expected_slippage > 0", None if fee_adjusted_edge is None else format(fee_adjusted_edge, "f")),
        _gate_row("no_simulated_order_conflict", _gate_from_bool(no_simulated_order_conflict), 0, int(open_live_order_count)),
    ]

    gate_states = {item["name"]: item["state"] for item in gates}
    canonical_package_state = "PASSED"
    for gate_name in _HISTORICAL_BUY_REPLAY_REQUIRED_GATES:
        state = gate_states.get(gate_name, "UNKNOWN")
        if state == "FAILED":
            canonical_package_state = "FAILED"
            break
        if state in {"UNKNOWN", "NOT_APPLICABLE"} and canonical_package_state == "PASSED":
            canonical_package_state = "UNKNOWN"
    gates.append(
        _gate_row(
            "canonical_package_eligibility_reached",
            canonical_package_state,
            "all required campaign gates are PASSED in read-only simulation",
            {name: gate_states.get(name, "UNKNOWN") for name in _HISTORICAL_BUY_REPLAY_REQUIRED_GATES},
        )
    )
    gate_states["canonical_package_eligibility_reached"] = canonical_package_state

    if gate_states["historical_decision_exists"] != "PASSED" or gate_states["historical_action_is_buy"] != "PASSED":
        campaign_replay_outcome = "REJECTED_BEFORE_CANDIDATE"
    elif gate_states["risk_verdict_allows"] == "FAILED":
        campaign_replay_outcome = "RISK_REJECTED"
    elif gate_states["canonical_package_eligibility_reached"] == "PASSED":
        campaign_replay_outcome = "READY_PACKAGE_ELIGIBLE"
    elif gate_states["exact_five_dollar_candidate_possible"] == "PASSED":
        campaign_replay_outcome = "EXACT_5_DOLLAR_OPPORTUNITY"
    elif gate_states["confidence_threshold_passed"] == "PASSED" and gate_states["lifecycle_allows_open_position"] == "PASSED":
        campaign_replay_outcome = "EXECUTABLE_CAMPAIGN_CANDIDATE"
    else:
        campaign_replay_outcome = "REJECTED_BEFORE_CANDIDATE"

    matching_sell_payload = {
        "supplied": matching_sell_decision_id is not None,
        "decision_id": None if matching_sell_decision_id is None else str(matching_sell_decision_id),
        "exists": matching_sell_decision is not None,
        "chronologically_after_buy": None,
        "asset_compatible": None,
        "strategy_lineage_compatible": None,
        "feasible_closing_action": None,
        "known_historical_gross_profit": None,
        "known_fees": None,
        "known_historical_net_profit": None,
    }
    if matching_sell_decision_id is not None and matching_sell_decision is not None and decision is not None:
        sell_action = _decision_action(matching_sell_decision)
        matching_sell_payload["chronologically_after_buy"] = matching_sell_decision.timestamp >= decision.timestamp
        matching_sell_payload["asset_compatible"] = matching_sell_decision.asset == decision.asset
        sell_strategy_identity, _ = _strategy_identity(matching_sell_decision)
        matching_sell_payload["strategy_lineage_compatible"] = (sell_strategy_identity == strategy_identity)
        matching_sell_payload["feasible_closing_action"] = sell_action == "SELL"
        pnl_payload = matching_sell_decision.pnl if isinstance(matching_sell_decision.pnl, dict) else {}
        if pnl_payload:
            gross = pnl_payload.get("gross_profit")
            fees = pnl_payload.get("fees")
            net = pnl_payload.get("net_profit")
            matching_sell_payload["known_historical_gross_profit"] = None if gross is None else str(gross)
            matching_sell_payload["known_fees"] = None if fees is None else str(fees)
            matching_sell_payload["known_historical_net_profit"] = None if net is None else str(net)

    return {
        "historical_record": {
            "decision_id": str(decision_id),
            "decision_exists": decision is not None,
            "timestamp": None if decision is None else decision.timestamp.isoformat(),
            "action": historical_action,
            "confidence": None if historical_confidence is None else format(historical_confidence, "f"),
            "trade_accepted": None if decision is None else bool(decision.trade_accepted),
            "trade_rejected_reason": None if decision is None else decision.trade_rejected_reason,
            "strategy_identity": strategy_identity,
            "strategy_version": strategy_version,
            "signal_id": None if signal is None else str(signal.id),
            "signal_action": None if signal is None else signal.action,
            "signal_status": None if signal is None else signal.status,
            "risk_event_id": None if risk_event is None else str(risk_event.id),
            "risk_verdict": risk_verdict,
            "source_lineage": None if decision is None else decision.source_lineage,
            "snapshot": None if snapshot is None else {
                "timestamp": snapshot.timestamp.isoformat(),
                "asset": snapshot.asset,
                "timeframe": snapshot.timeframe,
            },
            "execution_evidence": None if decision is None else decision.execution_details,
            "pnl_evidence": None if decision is None else decision.pnl,
        },
        "as_of_time_replay": {
            "deterministic_replay": True,
            "anti_hindsight": {
                "disallow_future_candles": True,
                "disallow_future_prices": True,
                "disallow_future_account_state": True,
                "future_evidence_only_for_observed_outcome": True,
            },
            "as_of_timestamp": None if decision is None else decision.timestamp.isoformat(),
            "market_data_as_of_time_valid": gate_states.get("market_data_as_of_time_valid"),
            "replay_validity": "PASSED" if gate_states.get("market_data_as_of_time_valid") == "PASSED" else gate_states.get("market_data_as_of_time_valid"),
        },
        "current_campaign_simulation": {
            "campaign_id": str(campaign_id),
            "campaign_version": int(campaign_version),
            "runtime_campaign_id": int(runtime_campaign_id),
            "paper_account_id": str(paper_account_id),
            "live_trading_profile_id": str(live_trading_profile_id),
            "provider": normalized_provider,
            "environment": str(environment or "").strip().lower(),
            "product": normalized_product,
            "historical_buy_timestamp": None if decision is None else decision.timestamp.isoformat(),
            "historical_confidence": None if historical_confidence is None else format(historical_confidence, "f"),
            "historical_risk_verdict": risk_verdict,
            "confidence_threshold": None if confidence_threshold is None else format(confidence_threshold, "f"),
            "current_campaign_accepts_buy": gate_states.get("canonical_package_eligibility_reached") == "PASSED",
            "simulated_amount": "5" if gate_states.get("exact_five_dollar_candidate_possible") == "PASSED" else None,
            "package_eligibility_verdict": gate_states.get("canonical_package_eligibility_reached"),
            "campaign_replay_outcome": campaign_replay_outcome,
            "gates": gates,
        },
        "observed_later_outcome": {
            "matching_sell": matching_sell_payload,
        },
        "primary_blocker": _primary_blocker_from_gates(gate_states),
        "gates": gates,
        "invariants": {
            "read_only": True,
            "no_database_writes": True,
            "no_package_creation": True,
            "no_package_authorization": True,
            "no_production_dry_run": True,
            "no_activation": True,
            "no_provider_order_calls": True,
            "no_reconciliation_mutation": True,
            "no_capital_reservation": True,
            "no_capital_movement": True,
            "no_writes_confirmed": True,
        },
    }


async def historical_buy_campaign_replay_audit(
    *,
    decision_id: UUID,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    matching_sell_decision_id: UUID | None = None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        decision = await db.get(DecisionRecord, decision_id)
        snapshot = None if decision is None else await db.get(DecisionSnapshot, decision_id)

        signal = None
        risk_event = None
        if decision is not None:
            signal_ids = _lineage_ids(decision.source_lineage, key="signals")
            risk_ids = _lineage_ids(decision.source_lineage, key="risk_events")
            if signal_ids:
                signal = await db.get(Signal, signal_ids[0])
            if risk_ids:
                risk_event = await db.get(RiskEvent, risk_ids[0])

        definition = await db.scalar(
            select(CapitalCampaignDefinition)
            .where(CapitalCampaignDefinition.campaign_id == campaign_id)
            .where(CapitalCampaignDefinition.version == campaign_version)
            .limit(1)
        )
        latest_version = await db.scalar(
            select(func.max(CapitalCampaignDefinition.version)).where(CapitalCampaignDefinition.campaign_id == campaign_id)
        )
        runtime = await db.scalar(
            select(CapitalCampaign)
            .where(CapitalCampaign.id == runtime_campaign_id)
            .limit(1)
        )
        paper_account = await db.get(PaperAccount, paper_account_id)
        profile = await db.get(LiveTradingProfile, live_trading_profile_id)
        open_live_order_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == provider)
                    .where(LiveCryptoOrder.environment == environment)
                    .where(LiveCryptoOrder.product_id == product_id)
                    .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                )
            )
            or 0
        )

        matching_sell_decision = None
        if matching_sell_decision_id is not None:
            matching_sell_decision = await db.get(DecisionRecord, matching_sell_decision_id)

    return _historical_buy_campaign_replay_audit_payload(
        decision_id=decision_id,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        runtime_campaign_id=runtime_campaign_id,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
        decision=decision,
        snapshot=snapshot,
        signal=signal,
        risk_event=risk_event,
        definition=definition,
        latest_version=None if latest_version is None else int(latest_version),
        runtime=runtime,
        paper_account=paper_account,
        profile=profile,
        open_live_order_count=open_live_order_count,
        matching_sell_decision=matching_sell_decision,
        matching_sell_decision_id=matching_sell_decision_id,
    )


def _classify_buy_blocker(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if "confidence" in normalized:
        return "confidence"
    if "risk" in normalized or "veto" in normalized or "reject" in normalized:
        return "Risk"
    if "fee" in normalized or "net_edge" in normalized or "slippage" in normalized:
        return "fees"
    if (
        "campaign" in normalized
        or "provider_product" in normalized
        or "runtime_campaign" in normalized
        or "product_not_allowed" in normalized
    ):
        return "campaign eligibility"
    if "exposure" in normalized or "allocation" in normalized or "deployed" in normalized:
        return "exposure"
    if "maximum_open_positions" in normalized or "position_limit" in normalized:
        return "position limits"
    if "existing_position" in normalized or "hold_position" in normalized or "position_below" in normalized:
        return "existing position"
    if "package" in normalized or "preview" in normalized or "eligible" in normalized:
        return "package eligibility"
    return "other"


def _extract_cycle_blocker_reason(cycle: AutonomousCycleRun) -> str:
    context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    composition = context.get("authoritative_composition") if isinstance(context.get("authoritative_composition"), dict) else {}
    selected = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}

    selected_reason = str(selected.get("reason") or "").strip()
    if selected_reason:
        return selected_reason

    failure_reason = str(cycle.failure_reason or "").strip()
    if failure_reason:
        return failure_reason

    for code in cycle.deterministic_explanation or []:
        token = str(code or "").strip()
        if token.startswith("CHECK_FAILED:"):
            return token.split(":", 1)[1]

    return "package_not_created"


async def _resolve_canonical_proving_campaign_identity(*, db: Any) -> tuple[UUID | None, int | None, str]:
    activation = await db.scalar(
        select(CanonicalProvingActivation)
        .order_by(desc(CanonicalProvingActivation.activated_at), desc(CanonicalProvingActivation.created_at))
        .limit(1)
    )
    if activation is not None:
        return activation.campaign_id, int(activation.campaign_version), "canonical_proving_activation"

    definition = await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.maximum_open_positions == 1)
        .where(CapitalCampaignDefinition.minimum_position_size == Decimal("5"))
        .where(CapitalCampaignDefinition.maximum_position_size == Decimal("5"))
        .where(CapitalCampaignDefinition.maximum_total_exposure == Decimal("5"))
        .where(CapitalCampaignDefinition.aggression_mode == "MAXIMUM_GOVERNED")
        .order_by(desc(CapitalCampaignDefinition.updated_at), desc(CapitalCampaignDefinition.version))
        .limit(1)
    )
    if definition is not None:
        return definition.campaign_id, int(definition.version), "capital_campaign_definition"

    return None, None, "not_found"


async def buy_opportunity_diagnostic() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=24)

    async with AsyncSessionLocal() as db:
        campaign_id, campaign_version, identity_source = await _resolve_canonical_proving_campaign_identity(db=db)
        if campaign_id is None or campaign_version is None:
            return {
                "window": {"hours": 24, "start": window_start.isoformat(), "end": now.isoformat()},
                "canonical_proving_campaign": None,
                "totals": {
                    "strategy_evaluations": 0,
                    "buy_opportunities": 0,
                    "sell_opportunities": 0,
                    "hold_decisions": 0,
                    "ready_packages": 0,
                },
                "buy_blockers": [],
                "no_buy_opportunities": True,
                "summary": {
                    "buy_opportunities": 0,
                    "ready_packages": 0,
                    "primary_blocker": "none",
                },
                "identity_source": identity_source,
                "invariants": {
                    "read_only": True,
                    "no_database_writes": True,
                    "no_package_creation": True,
                    "no_order_submission": True,
                },
            }

        cycles = list(
            (
                await db.execute(
                    select(AutonomousCycleRun)
                    .where(AutonomousCycleRun.cycle_kind == "campaign")
                    .where(AutonomousCycleRun.capital_campaign_id == campaign_id)
                    .where(AutonomousCycleRun.capital_campaign_version == campaign_version)
                    .where(AutonomousCycleRun.started_at >= window_start)
                    .where(AutonomousCycleRun.started_at <= now)
                    .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
                )
            ).scalars().all()
        )

        packages = list(
            (
                await db.execute(
                    select(CanonicalPreviewPackage)
                    .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                    .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                    .where(CanonicalPreviewPackage.side == "BUY")
                    .where(CanonicalPreviewPackage.generated_at >= window_start)
                    .where(CanonicalPreviewPackage.generated_at <= now)
                    .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id))
                )
            ).scalars().all()
        )

    strategy_evaluations = len(cycles)
    buy_cycles = [cycle for cycle in cycles if str(cycle.proposed_action or "").upper() == "OPEN_POSITION_PROPOSED"]
    sell_cycles = [cycle for cycle in cycles if str(cycle.proposed_action or "").upper() == "CLOSE_POSITION_PROPOSED"]
    hold_cycles = [cycle for cycle in cycles if cycle not in buy_cycles and cycle not in sell_cycles]

    package_by_decision_id: dict[UUID, CanonicalPreviewPackage] = {}
    for package in packages:
        if package.decision_record_id in package_by_decision_id:
            continue
        package_by_decision_id[package.decision_record_id] = package

    ready_packages = sum(1 for package in packages if str(package.package_state or "").upper() in _READY_PACKAGE_STATES)
    buy_blockers: list[dict[str, Any]] = []
    blocked_counter: Counter[str] = Counter()
    for cycle in buy_cycles:
        package = package_by_decision_id.get(cycle.decision_record_id) if cycle.decision_record_id is not None else None
        is_ready = package is not None and str(package.package_state or "").upper() in _READY_PACKAGE_STATES

        if is_ready:
            reason = "ready_package_created"
            blocker = "other"
        elif package is not None:
            reason = str(package.invalidated_reason or package.package_state or "package_not_ready")
            blocker = _classify_buy_blocker(reason)
            blocked_counter[blocker] += 1
        else:
            reason = _extract_cycle_blocker_reason(cycle)
            blocker = _classify_buy_blocker(reason)
            blocked_counter[blocker] += 1

        buy_blockers.append(
            {
                "cycle_id": str(cycle.cycle_id),
                "decision_record_id": None if cycle.decision_record_id is None else str(cycle.decision_record_id),
                "evaluated_at": cycle.started_at.isoformat(),
                "ready_package": is_ready,
                "package_id": None if package is None else str(package.package_id),
                "package_state": None if package is None else str(package.package_state),
                "first_blocker": blocker,
                "blocker_reason": reason,
            }
        )

    primary_blocker = blocked_counter.most_common(1)[0][0] if blocked_counter else "none"
    return {
        "window": {"hours": 24, "start": window_start.isoformat(), "end": now.isoformat()},
        "canonical_proving_campaign": {
            "campaign_id": str(campaign_id),
            "campaign_version": int(campaign_version),
        },
        "identity_source": identity_source,
        "totals": {
            "strategy_evaluations": strategy_evaluations,
            "buy_opportunities": len(buy_cycles),
            "sell_opportunities": len(sell_cycles),
            "hold_decisions": len(hold_cycles),
            "ready_packages": ready_packages,
        },
        "buy_blockers": buy_blockers,
        "no_buy_opportunities": len(buy_cycles) == 0,
        "summary": {
            "buy_opportunities": len(buy_cycles),
            "ready_packages": ready_packages,
            "primary_blocker": primary_blocker,
        },
        "invariants": {
            "read_only": True,
            "no_database_writes": True,
            "no_package_creation": True,
            "no_order_submission": True,
        },
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _extract_hold_reason(cycle: AutonomousCycleRun) -> str:
    context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    composition = context.get("authoritative_composition") if isinstance(context.get("authoritative_composition"), dict) else {}
    selected = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}
    reason = str(selected.get("reason") or "").strip()
    if reason:
        return reason
    reason = str(cycle.failure_reason or "").strip()
    if reason:
        return reason
    for code in cycle.deterministic_explanation or []:
        token = str(code or "").strip()
        if token.startswith("CHECK_FAILED:"):
            return token.split(":", 1)[1]
    return "hold_reason_missing"


def _extract_product(context: dict[str, Any], selected: dict[str, Any]) -> str | None:
    supported_trigger = context.get("supported_trigger") if isinstance(context.get("supported_trigger"), dict) else {}
    trigger_product = str(supported_trigger.get("product_id") or "").strip()
    if trigger_product:
        return trigger_product
    instrument = str(selected.get("instrument") or "").strip()
    return instrument or None


def _extract_candle_fields(context: dict[str, Any], composition: dict[str, Any], instrument: str | None) -> tuple[str | None, str | None]:
    candle = context.get("candle") if isinstance(context.get("candle"), dict) else {}
    close_time = str(candle.get("close_time") or "").strip() or None

    authoritative = composition.get("authoritative_evidence") if isinstance(composition.get("authoritative_evidence"), dict) else {}
    market_map = authoritative.get("market") if isinstance(authoritative.get("market"), dict) else {}

    market_entry: dict[str, Any] | None = None
    if instrument and isinstance(market_map.get(instrument), dict):
        market_entry = market_map.get(instrument)
    elif market_map:
        first = next(iter(market_map.values()))
        market_entry = first if isinstance(first, dict) else None

    candle_id = None
    if market_entry is not None:
        source_identity = market_entry.get("source_identity") if isinstance(market_entry.get("source_identity"), dict) else {}
        raw_candle_id = source_identity.get("candle_id") or market_entry.get("latest_closed_candle_id")
        candle_id = None if raw_candle_id is None else str(raw_candle_id)

    return candle_id, close_time


def _extract_candidate(composition: dict[str, Any], instrument: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    eligible = composition.get("eligible_candidates") if isinstance(composition.get("eligible_candidates"), list) else []
    rejected = composition.get("rejected_candidates") if isinstance(composition.get("rejected_candidates"), list) else []

    eligible_match = None
    for item in eligible:
        if not isinstance(item, dict):
            continue
        if instrument is None or str(item.get("instrument") or "").strip() == instrument:
            eligible_match = item
            break

    rejected_match = None
    for item in rejected:
        if not isinstance(item, dict):
            continue
        if instrument is None or str(item.get("instrument") or "").strip() == instrument:
            rejected_match = item
            break

    return eligible_match, rejected_match


def _condition_row(*, name: str, actual: Any, required: Any, passed: bool) -> dict[str, Any]:
    return {
        "condition": name,
        "actual_value": actual,
        "required_threshold": required,
        "pass": bool(passed),
    }


def _distance_to_requirement(*, condition: dict[str, Any]) -> dict[str, Any] | None:
    if bool(condition.get("pass")):
        return None

    condition_name = str(condition.get("condition") or "")
    actual = _decimal_or_none(condition.get("actual_value"))
    required = condition.get("required_threshold")
    required_decimal = _decimal_or_none(required)

    if condition_name == "expected_net_dollars_positive_for_buy" and actual is not None:
        remaining = Decimal("0") - actual
        if remaining < Decimal("0"):
            remaining = Decimal("0")
        return {
            "condition": condition_name,
            "distance": format(remaining, "f"),
            "unit": "USD",
        }

    if required_decimal is not None and actual is not None:
        remaining = required_decimal - actual
        if remaining < Decimal("0"):
            remaining = Decimal("0")
        return {
            "condition": condition_name,
            "distance": format(remaining, "f"),
            "unit": "points",
        }

    return None


def _is_unknown_trace_value(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().upper() == "UNKNOWN"


def _extract_strategy_rule_trace(*, selected: dict[str, Any], eligible: dict[str, Any] | None, rejected: dict[str, Any] | None) -> dict[str, Any] | None:
    direct = selected.get("strategy_rule_trace") if isinstance(selected.get("strategy_rule_trace"), dict) else None
    if direct is not None:
        return direct

    for candidate in (eligible, rejected):
        if not isinstance(candidate, dict):
            continue
        strategy = candidate.get("strategy") if isinstance(candidate.get("strategy"), dict) else {}
        direct_strategy = strategy.get("strategy_rule_trace") if isinstance(strategy.get("strategy_rule_trace"), dict) else None
        if direct_strategy is not None:
            return direct_strategy

        decision_record = strategy.get("decision_record") if isinstance(strategy.get("decision_record"), dict) else {}
        generated_signals = decision_record.get("generated_signals") if isinstance(decision_record.get("generated_signals"), list) else []
        for signal in generated_signals:
            if not isinstance(signal, dict):
                continue
            direct_signal = signal.get("strategy_rule_trace") if isinstance(signal.get("strategy_rule_trace"), dict) else None
            if direct_signal is not None:
                return direct_signal
            strategy_evidence = signal.get("strategy_evidence") if isinstance(signal.get("strategy_evidence"), dict) else {}
            nested = strategy_evidence.get("strategy_rule_trace") if isinstance(strategy_evidence.get("strategy_rule_trace"), dict) else None
            if nested is not None:
                return nested

        indicators = decision_record.get("indicators") if isinstance(decision_record.get("indicators"), dict) else {}
        nested_indicators = indicators.get("strategy_rule_trace") if isinstance(indicators.get("strategy_rule_trace"), dict) else None
        if nested_indicators is not None:
            return nested_indicators

    return None


async def hold_decision_diagnostic() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=24)

    async with AsyncSessionLocal() as db:
        campaign_id, campaign_version, identity_source = await _resolve_canonical_proving_campaign_identity(db=db)
        if campaign_id is None or campaign_version is None:
            return {
                "window": {"hours": 24, "start": window_start.isoformat(), "end": now.isoformat()},
                "canonical_proving_campaign": None,
                "hold_decisions": [],
                "totals": {
                    "strategy_evaluations": 0,
                    "buy_opportunities": 0,
                    "sell_opportunities": 0,
                    "hold_decisions": 0,
                },
                "summary": {
                    "strategy_evaluations": 0,
                    "buy_opportunities": 0,
                    "sell_opportunities": 0,
                    "hold_decisions": 0,
                    "most_common_hold_reason": "none",
                    "most_common_unmet_buy_condition": "none",
                },
                "identity_source": identity_source,
                "invariants": {
                    "read_only": True,
                    "no_database_writes": True,
                    "no_package_creation": True,
                    "no_order_submission": True,
                },
            }

        cycles = list(
            (
                await db.execute(
                    select(AutonomousCycleRun)
                    .where(AutonomousCycleRun.cycle_kind == "campaign")
                    .where(AutonomousCycleRun.capital_campaign_id == campaign_id)
                    .where(AutonomousCycleRun.capital_campaign_version == campaign_version)
                    .where(AutonomousCycleRun.started_at >= window_start)
                    .where(AutonomousCycleRun.started_at <= now)
                    .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
                )
            ).scalars().all()
        )

    buy_cycles = [cycle for cycle in cycles if str(cycle.proposed_action or "").upper() == "OPEN_POSITION_PROPOSED"]
    sell_cycles = [cycle for cycle in cycles if str(cycle.proposed_action or "").upper() == "CLOSE_POSITION_PROPOSED"]
    hold_cycles = [cycle for cycle in cycles if cycle not in buy_cycles and cycle not in sell_cycles]

    hold_reason_counter: Counter[str] = Counter()
    unmet_buy_counter: Counter[str] = Counter()
    hold_rows: list[dict[str, Any]] = []

    for cycle in hold_cycles:
        context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
        composition = context.get("authoritative_composition") if isinstance(context.get("authoritative_composition"), dict) else {}
        selected = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}

        product = _extract_product(context=context, selected=selected)
        strategy_identity = str(selected.get("strategy_identity") or "").strip() or None
        strategy_version = str(selected.get("strategy_version") or "").strip() or None
        hold_reason = _extract_hold_reason(cycle)
        hold_reason_counter[hold_reason] += 1

        instrument = str(selected.get("instrument") or product or "").strip() or None
        candle_id, candle_close_time = _extract_candle_fields(context=context, composition=composition, instrument=instrument)
        eligible_candidate, rejected_candidate = _extract_candidate(composition=composition, instrument=instrument)
        strategy_rule_trace = _extract_strategy_rule_trace(selected=selected, eligible=eligible_candidate, rejected=rejected_candidate)

        buy_conditions: list[dict[str, Any]] = []
        sell_conditions: list[dict[str, Any]] = []
        missing_evidence: list[str] = []

        trace_distance_to_buy = None
        if strategy_rule_trace is not None:
            trace_previous_spread = strategy_rule_trace.get("previous_spread")
            trace_current_spread = strategy_rule_trace.get("current_spread")
            trace_buy_passed = strategy_rule_trace.get("buy_condition_passed")
            trace_sell_passed = strategy_rule_trace.get("sell_condition_passed")

            trace_prev_spread_decimal = None if _is_unknown_trace_value(trace_previous_spread) else _decimal_or_none(trace_previous_spread)
            trace_curr_spread_decimal = None if _is_unknown_trace_value(trace_current_spread) else _decimal_or_none(trace_current_spread)

            buy_conditions.append(
                _condition_row(
                    name="previous_spread_non_positive_for_buy",
                    actual=trace_previous_spread,
                    required="<= 0",
                    passed=trace_prev_spread_decimal is not None and trace_prev_spread_decimal <= Decimal("0"),
                )
            )
            buy_conditions.append(
                _condition_row(
                    name="current_spread_positive_for_buy",
                    actual=trace_current_spread,
                    required="> 0",
                    passed=trace_curr_spread_decimal is not None and trace_curr_spread_decimal > Decimal("0"),
                )
            )
            buy_conditions.append(
                _condition_row(
                    name="buy_condition_passed",
                    actual=trace_buy_passed,
                    required=True,
                    passed=trace_buy_passed is True,
                )
            )

            sell_conditions.append(
                _condition_row(
                    name="previous_spread_non_negative_for_sell",
                    actual=trace_previous_spread,
                    required=">= 0",
                    passed=trace_prev_spread_decimal is not None and trace_prev_spread_decimal >= Decimal("0"),
                )
            )
            sell_conditions.append(
                _condition_row(
                    name="current_spread_negative_for_sell",
                    actual=trace_current_spread,
                    required="< 0",
                    passed=trace_curr_spread_decimal is not None and trace_curr_spread_decimal < Decimal("0"),
                )
            )
            sell_conditions.append(
                _condition_row(
                    name="sell_condition_passed",
                    actual=trace_sell_passed,
                    required=True,
                    passed=trace_sell_passed is True,
                )
            )

            if candle_id is None and not _is_unknown_trace_value(strategy_rule_trace.get("candle_id")):
                candle_id = str(strategy_rule_trace.get("candle_id"))
            if candle_close_time is None and not _is_unknown_trace_value(strategy_rule_trace.get("candle_close_time")):
                candle_close_time = str(strategy_rule_trace.get("candle_close_time"))

            if _is_unknown_trace_value(trace_previous_spread):
                missing_evidence.append("strategy_rule_trace.previous_spread")
            if _is_unknown_trace_value(trace_current_spread):
                missing_evidence.append("strategy_rule_trace.current_spread")
            if _is_unknown_trace_value(trace_buy_passed):
                missing_evidence.append("strategy_rule_trace.buy_condition_passed")
            if _is_unknown_trace_value(trace_sell_passed):
                missing_evidence.append("strategy_rule_trace.sell_condition_passed")

            trace_distance_raw = strategy_rule_trace.get("distance_to_bullish_crossover")
            if _is_unknown_trace_value(trace_distance_raw):
                missing_evidence.append("strategy_rule_trace.distance_to_bullish_crossover")
            else:
                trace_distance_to_buy = {
                    "condition": "current_spread_positive_for_buy",
                    "distance": str(trace_distance_raw),
                    "unit": "points",
                }

        decision_kind = str(selected.get("decision_kind") or "").strip() or None
        buy_conditions.append(
            _condition_row(
                name="decision_kind_open_position",
                actual=decision_kind,
                required="OPEN_POSITION_PROPOSED",
                passed=decision_kind == "OPEN_POSITION_PROPOSED",
            )
        )

        buy_expected_net = None
        if isinstance(eligible_candidate, dict):
            buy_expected_net = eligible_candidate.get("expected_net_dollars")
        if buy_expected_net is None and isinstance(rejected_candidate, dict):
            buy_expected_net = rejected_candidate.get("expected_net_dollars")
        if buy_expected_net is None and hold_reason == "non_positive_net_edge":
            missing_evidence.append("buy_condition.expected_net_dollars")
        buy_expected_net_dec = _decimal_or_none(buy_expected_net)
        buy_conditions.append(
            _condition_row(
                name="expected_net_dollars_positive_for_buy",
                actual=None if buy_expected_net_dec is None else format(buy_expected_net_dec, "f"),
                required="> 0",
                passed=buy_expected_net_dec is not None and buy_expected_net_dec > Decimal("0"),
            )
        )

        risk_verdict = str(selected.get("risk_verdict") or "").strip() or None
        if risk_verdict is None and isinstance(rejected_candidate, dict):
            risk_blob = rejected_candidate.get("risk") if isinstance(rejected_candidate.get("risk"), dict) else {}
            risk_verdict = str(risk_blob.get("verdict") or "").strip() or None
        if risk_verdict is None:
            missing_evidence.append("buy_condition.risk_verdict")
        buy_conditions.append(
            _condition_row(
                name="risk_verdict_allows_buy",
                actual=risk_verdict,
                required="ALLOW (not VETO)",
                passed=risk_verdict is not None and risk_verdict != "VETO",
            )
        )

        market_freshness = None
        authoritative = composition.get("authoritative_evidence") if isinstance(composition.get("authoritative_evidence"), dict) else {}
        market_map = authoritative.get("market") if isinstance(authoritative.get("market"), dict) else {}
        market_entry = market_map.get(instrument) if instrument is not None and isinstance(market_map.get(instrument), dict) else None
        if market_entry is None and market_map:
            candidate_entry = next(iter(market_map.values()))
            market_entry = candidate_entry if isinstance(candidate_entry, dict) else None
        if market_entry is not None:
            market_freshness = str(market_entry.get("freshness") or "").strip() or None
        if market_freshness is None:
            missing_evidence.append("buy_condition.market_freshness")
        buy_conditions.append(
            _condition_row(
                name="market_evidence_fresh_for_buy",
                actual=market_freshness,
                required="fresh",
                passed=market_freshness == "fresh",
            )
        )

        sell_conditions.append(
            _condition_row(
                name="decision_kind_close_position",
                actual=decision_kind,
                required="CLOSE_POSITION_PROPOSED",
                passed=decision_kind == "CLOSE_POSITION_PROPOSED",
            )
        )

        open_position_actual = None
        position_map = authoritative.get("position") if isinstance(authoritative.get("position"), dict) else {}
        position_entry = position_map.get(instrument) if instrument is not None and isinstance(position_map.get(instrument), dict) else None
        if position_entry is None and position_map:
            candidate_position = next(iter(position_map.values()))
            position_entry = candidate_position if isinstance(candidate_position, dict) else None
        if isinstance(position_entry, dict):
            position_blob = position_entry.get("position") if isinstance(position_entry.get("position"), dict) else {}
            open_position_actual = position_blob.get("quantity")
        if open_position_actual is None:
            missing_evidence.append("sell_condition.open_position_quantity")
        open_position_qty = _decimal_or_none(open_position_actual)
        sell_conditions.append(
            _condition_row(
                name="open_position_exists_for_sell",
                actual=None if open_position_qty is None else format(open_position_qty, "f"),
                required="> 0",
                passed=open_position_qty is not None and open_position_qty > Decimal("0"),
            )
        )

        sell_expected_net = None
        if isinstance(eligible_candidate, dict):
            sell_expected_net = eligible_candidate.get("expected_net_dollars")
        if sell_expected_net is None and isinstance(rejected_candidate, dict):
            sell_expected_net = rejected_candidate.get("expected_net_dollars")
        if sell_expected_net is None:
            missing_evidence.append("sell_condition.expected_net_dollars")
        sell_expected_net_dec = _decimal_or_none(sell_expected_net)
        sell_conditions.append(
            _condition_row(
                name="expected_net_dollars_positive_for_sell",
                actual=None if sell_expected_net_dec is None else format(sell_expected_net_dec, "f"),
                required="> 0",
                passed=sell_expected_net_dec is not None and sell_expected_net_dec > Decimal("0"),
            )
        )

        if strategy_identity is None:
            missing_evidence.append("strategy_identity")
        if strategy_version is None:
            missing_evidence.append("strategy_version")
        if candle_id is None:
            missing_evidence.append("candle_id")
        if candle_close_time is None:
            missing_evidence.append("candle_close_time")

        if strategy_rule_trace is None:
            missing_evidence.append("strategy_buy_rule_trace")
            missing_evidence.append("strategy_sell_rule_trace")

        first_unmet_buy_condition = next((item for item in buy_conditions if not bool(item.get("pass"))), None)
        first_unmet_name = None if first_unmet_buy_condition is None else str(first_unmet_buy_condition.get("condition"))
        if first_unmet_name:
            unmet_buy_counter[first_unmet_name] += 1

        if trace_distance_to_buy is not None:
            distance_to_buy = trace_distance_to_buy
        else:
            distance_to_buy = None if first_unmet_buy_condition is None else _distance_to_requirement(condition=first_unmet_buy_condition)

        hold_rows.append(
            {
                "decision_timestamp": cycle.started_at.isoformat(),
                "product": product,
                "strategy_identity": strategy_identity,
                "strategy_version": strategy_version,
                "candle_id": candle_id,
                "candle_close_time": candle_close_time,
                "hold_reason": hold_reason,
                "buy_conditions": buy_conditions,
                "sell_conditions": sell_conditions,
                "first_unmet_buy_condition": first_unmet_name,
                "distance_to_buy": distance_to_buy,
                "missing_evidence": sorted(set(missing_evidence)),
            }
        )

    most_common_hold_reason = hold_reason_counter.most_common(1)[0][0] if hold_reason_counter else "none"
    most_common_unmet_buy = unmet_buy_counter.most_common(1)[0][0] if unmet_buy_counter else "none"

    return {
        "window": {"hours": 24, "start": window_start.isoformat(), "end": now.isoformat()},
        "canonical_proving_campaign": {
            "campaign_id": str(campaign_id),
            "campaign_version": int(campaign_version),
        },
        "identity_source": identity_source,
        "totals": {
            "strategy_evaluations": len(cycles),
            "buy_opportunities": len(buy_cycles),
            "sell_opportunities": len(sell_cycles),
            "hold_decisions": len(hold_cycles),
        },
        "hold_decisions": hold_rows,
        "summary": {
            "strategy_evaluations": len(cycles),
            "buy_opportunities": len(buy_cycles),
            "sell_opportunities": len(sell_cycles),
            "hold_decisions": len(hold_cycles),
            "most_common_hold_reason": most_common_hold_reason,
            "most_common_unmet_buy_condition": most_common_unmet_buy,
        },
        "invariants": {
            "read_only": True,
            "no_database_writes": True,
            "no_package_creation": True,
            "no_order_submission": True,
        },
    }


def _product_symbol(value: str) -> str:
    normalized = normalize_product_id(value)
    return normalized.split("-", 1)[0] if "-" in normalized else normalized


def _gate_state(*, passed: bool | None) -> str:
    if passed is None:
        return "NOT_APPLICABLE"
    return "PASSED" if passed else "FAILED"


def _normalized_allowed(values: Any, *, lower: bool) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        out.append(item.lower() if lower else item.upper())
    return sorted(set(out))


def _parse_effective_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _root_cause_code(*, gates: dict[str, str], runtime_linked_uuid: str | None, campaign_id: UUID, would_appear: bool) -> str:
    if gates.get("definition_row_exists") == "FAILED":
        return "DEFINITION_ROW_MISSING"
    if gates.get("requested_version_is_latest") == "FAILED":
        return "REQUESTED_VERSION_NOT_LATEST"
    if gates.get("runtime_campaign_exists") == "FAILED":
        if runtime_linked_uuid and runtime_linked_uuid != str(campaign_id):
            return "RUNTIME_UUID_LINK_MISMATCH"
        return "RUNTIME_CAMPAIGN_MISSING"
    if gates.get("runtime_uuid_link_matches") == "FAILED":
        return "RUNTIME_UUID_LINK_MISMATCH"
    if gates.get("runtime_definition_version_matches") == "FAILED":
        return "RUNTIME_DEFINITION_VERSION_MISMATCH"
    if gates.get("status_allowed_for_orchestration") == "FAILED":
        return "CAMPAIGN_STATUS_INELIGIBLE"
    if gates.get("draft_allowed_in_unattended_mode") == "FAILED":
        return "DRAFT_EXCLUDED_FROM_UNATTENDED_MODE"
    if gates.get("product_allowed") == "FAILED":
        return "PRODUCT_NOT_ALLOWED"
    if gates.get("provider_allowed") == "FAILED":
        return "PROVIDER_NOT_ALLOWED"
    if gates.get("effective_time_window_valid") == "FAILED":
        return "CAMPAIGN_OUTSIDE_EFFECTIVE_WINDOW"
    if would_appear:
        return "ELIGIBLE"
    return "OTHER:unresolved_unattended_selection_miss"


def _build_campaign_unattended_eligibility_audit_payload(
    *,
    campaign_id: UUID,
    campaign_version: int,
    provider: str,
    environment: str,
    product_id: str,
    definition: CapitalCampaignDefinition | None,
    available_versions: list[int],
    latest_version: int | None,
    runtime_exact: CapitalCampaign | None,
    runtime_linked: CapitalCampaign | None,
    unattended_considered: list[dict[str, Any]],
    unattended_eligible: list[dict[str, Any]],
    unattended_skipped: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    normalized_product = normalize_product_id(product_id)
    normalized_provider = str(provider or "").strip().lower()
    normalized_environment = str(environment or "").strip().lower()

    definition_exists = definition is not None
    requested_is_latest = None if latest_version is None else (int(campaign_version) == int(latest_version))

    runtime_exists = None if not definition_exists else (runtime_exact is not None)
    runtime_uuid_link_matches = None
    if definition_exists and runtime_exact is not None:
        runtime_uuid_link_matches = str(runtime_exact.uuid) == str(definition.campaign_id)

    runtime_definition_version_matches = None
    if definition_exists and runtime_exact is not None:
        runtime_definition_version_matches = int(getattr(runtime_exact, "definition_version", -1) or -1) == int(definition.version)

    status_allowed = None
    draft_allowed = None
    product_allowed = None
    provider_allowed = None
    effective_window_valid = None

    allowed_instruments: list[str] = []
    allowed_venues: list[str] = []
    effective_start = None
    effective_end = None
    campaign_modes: list[str] = []

    if definition_exists:
        allowed_instruments = _normalized_allowed(getattr(definition, "allowed_instruments", []), lower=False)
        allowed_venues = _normalized_allowed(getattr(definition, "allowed_venues", []), lower=True)
        campaign_modes = list(getattr(definition, "campaign_modes", []) or [])
        status = str(getattr(definition, "status", "")).strip().upper()
        status_allowed = status in _ORCHESTRATION_READINESS_STATUSES
        draft_allowed = status != "DRAFT"
        product_allowed = normalized_product in allowed_instruments
        provider_allowed = normalized_provider in allowed_venues
        metadata_evidence = getattr(definition, "metadata_evidence", {}) if isinstance(getattr(definition, "metadata_evidence", {}), dict) else {}
        effective_start = _parse_effective_timestamp(metadata_evidence.get("effective_start_at") or metadata_evidence.get("start_at"))
        effective_end = _parse_effective_timestamp(metadata_evidence.get("effective_end_at") or metadata_evidence.get("end_at"))
        if effective_start is not None or effective_end is not None:
            effective_window_valid = (effective_start is None or now >= effective_start) and (effective_end is None or now <= effective_end)

    unattended_keys = {(str(item.get("campaign_id")), int(item.get("version"))) for item in unattended_eligible}
    would_appear = (str(campaign_id), int(campaign_version)) in unattended_keys

    gate_rows = [
        {
            "name": "latest_definition_exists",
            "state": _gate_state(passed=latest_version is not None),
            "expected": "latest definition version exists",
            "actual": None if latest_version is None else str(latest_version),
        },
        {
            "name": "definition_row_exists",
            "state": _gate_state(passed=definition_exists),
            "expected": f"capital_campaign_definitions row for campaign_id={campaign_id} version={campaign_version}",
            "actual": "found" if definition_exists else "missing",
        },
        {
            "name": "requested_version_is_latest",
            "state": _gate_state(passed=requested_is_latest),
            "expected": None if latest_version is None else str(latest_version),
            "actual": str(campaign_version),
        },
        {
            "name": "runtime_campaign_exists",
            "state": _gate_state(passed=runtime_exists),
            "expected": None if not definition_exists else f"capital_campaigns.uuid={campaign_id}",
            "actual": None if runtime_exact is None else str(runtime_exact.id),
        },
        {
            "name": "runtime_uuid_link_matches",
            "state": _gate_state(passed=runtime_uuid_link_matches),
            "expected": None if runtime_exact is None else str(campaign_id),
            "actual": None if runtime_exact is None else str(runtime_exact.uuid),
        },
        {
            "name": "runtime_definition_version_matches",
            "state": _gate_state(passed=runtime_definition_version_matches),
            "expected": None if runtime_exact is None or definition is None else str(definition.version),
            "actual": None if runtime_exact is None else str(getattr(runtime_exact, "definition_version", None)),
        },
        {
            "name": "status_allowed_for_orchestration",
            "state": _gate_state(passed=status_allowed),
            "expected": sorted(_ORCHESTRATION_READINESS_STATUSES),
            "actual": None if definition is None else str(getattr(definition, "status", None)),
        },
        {
            "name": "draft_allowed_in_unattended_mode",
            "state": _gate_state(passed=draft_allowed),
            "expected": "status != DRAFT (allow_draft_preview=False)",
            "actual": None if definition is None else str(getattr(definition, "status", None)),
        },
        {
            "name": "product_allowed",
            "state": _gate_state(passed=product_allowed),
            "expected": normalized_product,
            "actual": allowed_instruments,
        },
        {
            "name": "provider_allowed",
            "state": _gate_state(passed=provider_allowed),
            "expected": normalized_provider,
            "actual": allowed_venues,
        },
        {
            "name": "effective_time_window_valid",
            "state": _gate_state(passed=effective_window_valid),
            "expected": "now within [effective_start_at,effective_end_at] when configured",
            "actual": {
                "now": now.isoformat(),
                "effective_start_at": None if effective_start is None else effective_start.isoformat(),
                "effective_end_at": None if effective_end is None else effective_end.isoformat(),
            },
        },
        {
            "name": "environment_filter_applies",
            "state": "NOT_APPLICABLE",
            "expected": "unattended campaign selection does not filter by environment",
            "actual": normalized_environment,
        },
    ]

    gate_state_map = {item["name"]: item["state"] for item in gate_rows}
    root_code = _root_cause_code(
        gates=gate_state_map,
        runtime_linked_uuid=None if runtime_linked is None else str(runtime_linked.uuid),
        campaign_id=campaign_id,
        would_appear=would_appear,
    )

    definition_payload = None
    if definition is not None:
        definition_payload = {
            "campaign_id": str(definition.campaign_id),
            "version": int(definition.version),
            "status": str(definition.status),
            "allowed_instruments": list(definition.allowed_instruments or []),
            "allowed_venues": list(definition.allowed_venues or []),
            "effective_start_at": None if effective_start is None else effective_start.isoformat(),
            "effective_end_at": None if effective_end is None else effective_end.isoformat(),
            "campaign_modes": campaign_modes,
            "orchestration_enablement": {
                "status_allowed": bool(status_allowed) if status_allowed is not None else None,
                "draft_allowed_in_unattended": bool(draft_allowed) if draft_allowed is not None else None,
            },
        }

    runtime_payload = None
    if runtime_exact is not None:
        runtime_payload = {
            "runtime_id": int(runtime_exact.id),
            "uuid": str(runtime_exact.uuid),
            "definition_version": getattr(runtime_exact, "definition_version", None),
            "status": getattr(runtime_exact, "status", None),
            "paper_account_id": None if getattr(runtime_exact, "paper_account_id", None) is None else str(runtime_exact.paper_account_id),
            "starting_capital": _decimal_str(getattr(runtime_exact, "starting_capital", None)),
            "current_equity": _decimal_str(getattr(runtime_exact, "current_equity", None)),
            "realized_profit": _decimal_str(getattr(runtime_exact, "realized_profit", None)),
            "unrealized_profit": _decimal_str(getattr(runtime_exact, "unrealized_profit", None)),
            "roi": _decimal_str(getattr(runtime_exact, "roi", None)),
        }

    return {
        "campaign_id": str(campaign_id),
        "campaign_version": int(campaign_version),
        "provider": normalized_provider,
        "environment": normalized_environment,
        "product": normalized_product,
        "definition_row_exists": definition_exists,
        "available_definition_versions": available_versions,
        "latest_definition_version": latest_version,
        "definition": definition_payload,
        "runtime": runtime_payload,
        "runtime_linked_record": None
        if runtime_linked is None
        else {
            "runtime_id": int(runtime_linked.id),
            "uuid": str(runtime_linked.uuid),
            "definition_campaign_id": None
            if getattr(runtime_linked, "definition_campaign_id", None) is None
            else str(runtime_linked.definition_campaign_id),
            "definition_version": getattr(runtime_linked, "definition_version", None),
        },
        "unattended_scan": {
            "considered_campaigns": unattended_considered,
            "eligible_campaigns": unattended_eligible,
            "skipped_campaigns": unattended_skipped,
            "would_appear_in_unattended_candidate_list_today": would_appear,
        },
        "gates": gate_rows,
        "root_cause_code": root_code,
        "invariants": {
            "read_only": True,
            "no_database_writes": True,
            "no_campaign_transition": True,
            "no_package_creation": True,
            "no_authorization": True,
            "no_activation": True,
            "no_provider_order_submission": True,
            "no_capital_movement": True,
        },
    }


async def campaign_unattended_eligibility_audit(
    *,
    campaign_id: UUID,
    campaign_version: int,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        definition = await db.scalar(
            select(CapitalCampaignDefinition)
            .where(CapitalCampaignDefinition.campaign_id == campaign_id)
            .where(CapitalCampaignDefinition.version == campaign_version)
            .limit(1)
        )

        available_version_rows = list(
            (
                await db.execute(
                    select(CapitalCampaignDefinition.version)
                    .where(CapitalCampaignDefinition.campaign_id == campaign_id)
                    .order_by(desc(CapitalCampaignDefinition.version))
                )
            ).scalars().all()
        )
        available_versions = [int(item) for item in available_version_rows]
        latest_version = max(available_versions) if available_versions else None

        runtime_exact = await db.scalar(
            select(CapitalCampaign)
            .where(CapitalCampaign.uuid == campaign_id)
            .order_by(desc(CapitalCampaign.updated_at), desc(CapitalCampaign.id))
            .limit(1)
        )
        runtime_linked = await db.scalar(
            select(CapitalCampaign)
            .where(CapitalCampaign.definition_campaign_id == campaign_id)
            .order_by(desc(CapitalCampaign.updated_at), desc(CapitalCampaign.id))
            .limit(1)
        )

        unattended_scan = await _list_campaign_definitions(
            db=db,
            campaign_id=None,
            status=None,
            latest_only=True,
        )
        considered_campaigns = [
            {
                "campaign_id": str(item.campaign_id),
                "version": int(item.version),
                "status": str(item.status),
            }
            for item in unattended_scan.items
        ]

        unattended_eligible: list[dict[str, Any]] = []
        unattended_skipped: list[dict[str, Any]] = []
        for item in unattended_scan.items:
            eligible_scope = _eligible_for_orchestration(campaign=item)
            status_value = str(getattr(item, "status", "")).strip().upper()
            if not eligible_scope:
                reasons: list[str] = []
                if status_value not in _ORCHESTRATION_READINESS_STATUSES:
                    reasons.append("status_not_ready_for_orchestration")
                normalized_instruments = _normalized_allowed(getattr(item, "allowed_instruments", []), lower=False)
                normalized_venues = _normalized_allowed(getattr(item, "allowed_venues", []), lower=True)
                if normalize_product_id(product_id) not in normalized_instruments:
                    reasons.append("product_not_allowed")
                if str(provider or "").strip().lower() not in normalized_venues:
                    reasons.append("provider_not_allowed")
                unattended_skipped.append(
                    {
                        "campaign_id": str(item.campaign_id),
                        "version": int(item.version),
                        "reason": ",".join(reasons) if reasons else "ineligible_scope",
                    }
                )
                continue

            if status_value == "DRAFT":
                unattended_skipped.append(
                    {
                        "campaign_id": str(item.campaign_id),
                        "version": int(item.version),
                        "reason": "draft_excluded_from_unattended_mode",
                    }
                )
                continue

            unattended_eligible.append(
                {
                    "campaign_id": str(item.campaign_id),
                    "version": int(item.version),
                    "status": status_value,
                }
            )

    return _build_campaign_unattended_eligibility_audit_payload(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        provider=provider,
        environment=environment,
        product_id=product_id,
        definition=definition,
        available_versions=available_versions,
        latest_version=latest_version,
        runtime_exact=runtime_exact,
        runtime_linked=runtime_linked,
        unattended_considered=considered_campaigns,
        unattended_eligible=unattended_eligible,
        unattended_skipped=unattended_skipped,
    )


def _interval_minutes(interval: str | None) -> int | None:
    raw = str(interval or "").strip().lower()
    if not raw:
        return None
    if raw.endswith("m"):
        value = raw[:-1]
        return int(value) if value.isdigit() and int(value) > 0 else None
    if raw.endswith("h"):
        value = raw[:-1]
        return int(value) * 60 if value.isdigit() and int(value) > 0 else None
    if raw.endswith("d"):
        value = raw[:-1]
        return int(value) * 1440 if value.isdigit() and int(value) > 0 else None
    return None


def _strategy_identity_is_coherent(*, strategy_identity: str | None, strategy_version: str | None) -> bool:
    identity = str(strategy_identity or "").strip()
    version = str(strategy_version or "").strip()
    if not identity:
        return False
    if "@" not in identity:
        return identity == version or not version
    slug, identity_version = identity.split("@", 1)
    slug = slug.strip()
    identity_version = identity_version.strip()
    if not slug or not identity_version:
        return False
    if not version:
        return False
    if "@" in version:
        v_slug, v_version = version.split("@", 1)
        return v_slug.strip() == slug and v_version.strip() == identity_version
    return version == identity_version


def _latest_cycle_outcome(cycle: AutonomousCycleRun | None) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if cycle is None:
        return None, None, None, None, None
    context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    composition = context.get("authoritative_composition") if isinstance(context.get("authoritative_composition"), dict) else {}
    selected = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}
    strategy_identity = str(selected.get("strategy_identity") or "").strip() or None
    strategy_version = str(selected.get("strategy_version") or "").strip() or None
    return (
        str(cycle.proposed_action or "").strip() or None,
        str(cycle.failure_reason or selected.get("reason") or "").strip() or None,
        str(selected.get("decision_record_id") or "").strip() or None,
        strategy_identity,
        strategy_version,
    )


def _derive_first_autonomous_profit_status(evidence: dict[str, Any]) -> dict[str, Any]:
    now = evidence["now"]
    runtime = evidence.get("runtime")
    definition = evidence.get("definition")
    paper_account = evidence.get("paper_account")
    profile = evidence.get("profile")
    connection = evidence.get("connection")
    latest_candle = evidence.get("latest_candle")
    latest_cycle = evidence.get("latest_cycle")
    ready_package = evidence.get("ready_package")
    approval_event = evidence.get("approval_event")
    activation = evidence.get("activation")
    buy_submitted = bool(evidence.get("buy_submitted"))
    buy_fill_reconciled = bool(evidence.get("buy_fill_reconciled"))
    sell_submitted = bool(evidence.get("sell_submitted"))
    sell_fill_reconciled = bool(evidence.get("sell_fill_reconciled"))
    position_open = bool(evidence.get("position_open"))
    unresolved_reconciliation_count = int(evidence.get("unresolved_reconciliation_count") or 0)
    open_live_order_count = int(evidence.get("open_live_order_count") or 0)
    latest_reconciliation_unknown_count = int(evidence.get("unknown_reconciliation_count") or 0)
    provider_equity = evidence.get("provider_equity")
    paper_liquid_cash = evidence.get("paper_liquid_cash")
    provider_readiness_verdict = str(evidence.get("provider_readiness_verdict") or "").strip()
    provider_balance_synced_at = evidence.get("provider_balance_synced_at")
    latest_ingestion_candle_at = evidence.get("latest_ingestion_candle_at")
    realized_gross_profit = evidence.get("realized_gross_profit")
    fees = evidence.get("fees")
    realized_net_profit = evidence.get("realized_net_profit")
    autonomous_buy_provenance = bool(evidence.get("autonomous_buy_provenance"))
    autonomous_sell_provenance = bool(evidence.get("autonomous_sell_provenance"))
    starting_reconciled_usd = evidence.get("starting_reconciled_usd")
    ending_reconciled_usd = evidence.get("ending_reconciled_usd")

    latest_cycle_outcome, latest_cycle_reason, latest_cycle_decision_record_id, latest_strategy_identity, latest_strategy_version = _latest_cycle_outcome(latest_cycle)
    latest_strategy_coherent = _strategy_identity_is_coherent(
        strategy_identity=latest_strategy_identity,
        strategy_version=latest_strategy_version,
    ) if latest_strategy_identity is not None and latest_strategy_version is not None else False

    freshness_seconds = None
    freshness_minutes = None
    candle_interval_minutes = None
    ingestion_grace_minutes = None
    maximum_age_minutes = None
    freshness_verdict = "unavailable"
    if latest_candle is not None and getattr(latest_candle, "close_time", None) is not None:
        close_time = latest_candle.close_time.astimezone(timezone.utc)
        freshness_seconds = int((now - close_time).total_seconds())
        freshness_minutes = freshness_seconds // 60
        candle_interval_minutes = _interval_minutes(getattr(latest_candle, "interval", None))
        ingestion_grace_minutes = _INTERVAL_INGESTION_GRACE_MINUTES.get(str(getattr(latest_candle, "interval", "")).strip().lower(), 0)
        if candle_interval_minutes is None:
            freshness_verdict = "fail_closed_interval_unparseable"
        elif freshness_seconds < 0:
            freshness_verdict = "fail_closed_future_timestamp"
        else:
            maximum_age_minutes = candle_interval_minutes + ingestion_grace_minutes
            freshness_verdict = "fresh" if freshness_seconds <= (maximum_age_minutes * 60) else "stale"

    provider_connected = connection is not None and str(getattr(connection, "status", "")) == "connected"
    provider_ready = provider_readiness_verdict in {"READY_FOR_OPERATOR_REVIEW", "READY", "connected"}
    provider_balance_fresh = provider_balance_synced_at is not None and isinstance(provider_balance_synced_at, datetime) and int((now - provider_balance_synced_at.astimezone(timezone.utc)).total_seconds()) <= 1800
    provider_reconciliation_clean = unresolved_reconciliation_count == 0 and latest_reconciliation_unknown_count == 0
    runtime_campaign_matches = runtime is not None and str(getattr(runtime, "uuid", "")) == str(evidence["campaign_id"]) and int(getattr(runtime, "definition_version", -1)) == int(evidence["campaign_version"])
    runtime_uses_dedicated_account = runtime is not None and str(getattr(runtime, "paper_account_id", "")) == str(evidence["paper_account_id"])
    profile_uses_dedicated_account = profile is not None and str(getattr(profile, "paper_account_id", "")) == str(evidence["paper_account_id"])
    dedicated_account_active = paper_account is not None and bool(getattr(paper_account, "is_active", False))
    paper_cash_reconciled = unresolved_reconciliation_count == 0
    paper_liquid_cash_supports_exact_5 = paper_liquid_cash is not None and Decimal(str(paper_liquid_cash)) >= Decimal("5")
    max_open_positions_one = definition is not None and int(getattr(definition, "maximum_open_positions", -1)) == 1
    minimum_position_size_five = definition is not None and Decimal(str(getattr(definition, "minimum_position_size", "-1"))) == Decimal("5")
    maximum_position_size_five = definition is not None and Decimal(str(getattr(definition, "maximum_position_size", "-1"))) == Decimal("5")
    maximum_total_exposure_five = definition is not None and Decimal(str(getattr(definition, "maximum_total_exposure", "-1"))) == Decimal("5")
    latest_btc_candle_fresh = freshness_verdict == "fresh"
    latest_cycle_truthful_terminal = latest_cycle is not None and str(getattr(latest_cycle, "state", "")) in {"COMPLETE", "FAILED_CLOSED"} and str(getattr(latest_cycle, "termination_stage", "")) in {"preview_generated", "hold_no_package_created", "failed_closed"}
    worker_recently_ingested = latest_ingestion_candle_at is not None and int((now - latest_ingestion_candle_at.astimezone(timezone.utc)).total_seconds()) <= 1800
    decision_record_linkage_present = latest_cycle_decision_record_id is not None if latest_cycle is not None else False

    ready_package_current = ready_package is not None
    package_historically_advanced = approval_event is not None or activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled
    hold_no_package_expected = latest_cycle_outcome == "HOLD" and not ready_package_current and not package_historically_advanced
    package_progress_distinguishable = hold_no_package_expected or ready_package_current or package_historically_advanced

    exact_package_authorization = approval_event is not None or activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled
    dry_run_passed = bool(evidence.get("dry_run_passed")) or activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled
    bounded_activation_exists = activation is not None or buy_submitted or buy_fill_reconciled or sell_submitted or sell_fill_reconciled

    autonomous_buy_submitted = buy_submitted and autonomous_buy_provenance
    autonomous_buy_fill_reconciled = buy_fill_reconciled and autonomous_buy_provenance
    autonomous_sell_submitted = sell_submitted and autonomous_sell_provenance
    autonomous_sell_fill_reconciled = sell_fill_reconciled and autonomous_sell_provenance
    position_managed_historically = autonomous_buy_fill_reconciled and (autonomous_sell_submitted or autonomous_sell_fill_reconciled)

    positive_realized_net_profit = realized_net_profit is not None and Decimal(str(realized_net_profit)) > Decimal("0")
    ending_usd_exceeds_starting_usd = (
        starting_reconciled_usd is not None
        and ending_reconciled_usd is not None
        and Decimal(str(ending_reconciled_usd)) > Decimal(str(starting_reconciled_usd))
    )
    fees_known = fees is not None

    stage_1_complete = all([
        provider_connected,
        provider_ready,
        runtime_uses_dedicated_account,
        profile_uses_dedicated_account,
        paper_cash_reconciled,
        paper_liquid_cash_supports_exact_5,
        max_open_positions_one,
        minimum_position_size_five,
        maximum_position_size_five,
        maximum_total_exposure_five,
        provider_reconciliation_clean,
    ])
    stage_2_complete = all([
        stage_1_complete,
        latest_btc_candle_fresh,
        worker_recently_ingested,
        latest_strategy_coherent,
        latest_cycle_truthful_terminal,
    ])
    stage_3_complete = stage_2_complete and (ready_package_current or package_historically_advanced)
    stage_4_complete = stage_3_complete and exact_package_authorization
    stage_5_complete = stage_4_complete and dry_run_passed
    stage_6_complete = stage_5_complete and bounded_activation_exists
    stage_7_complete = stage_6_complete and autonomous_buy_submitted and autonomous_buy_fill_reconciled
    stage_8_complete = stage_7_complete and position_managed_historically
    stage_9_complete = stage_8_complete and autonomous_sell_submitted and autonomous_sell_fill_reconciled
    stage_10_complete = stage_9_complete and all([
        autonomous_buy_provenance,
        autonomous_buy_fill_reconciled,
        autonomous_sell_provenance,
        autonomous_sell_fill_reconciled,
        fees_known,
        ending_usd_exceeds_starting_usd,
        positive_realized_net_profit,
    ])

    stage_rows = [
        (1, "FOUNDATION_READY", stage_1_complete),
        (2, "AUTONOMOUS_EVALUATION_READY", stage_2_complete),
        (3, "READY_PACKAGE_CREATED", stage_3_complete),
        (4, "PACKAGE_AUTHORIZED", stage_4_complete),
        (5, "DRY_RUN_PASSED", stage_5_complete),
        (6, "BOUNDED_ACTIVATION", stage_6_complete),
        (7, "LIVE_BUY_RECONCILED", stage_7_complete),
        (8, "POSITION_MANAGED", stage_8_complete),
        (9, "LIVE_SELL_RECONCILED", stage_9_complete),
        (10, "POSITIVE_NET_PROFIT_CONFIRMED", stage_10_complete),
    ]
    stage_blocking_gate_map = {
        3: "READY_PACKAGE_NOT_YET_CREATED",
        4: "PACKAGE_NOT_AUTHORIZED",
        5: "DRY_RUN_NOT_PASSED",
        6: "BOUNDED_ACTIVATION_MISSING",
        7: "LIVE_BUY_NOT_RECONCILED",
        8: "POSITION_MANAGEMENT_EVIDENCE_MISSING",
        9: "LIVE_SELL_NOT_RECONCILED",
        10: "POSITIVE_NET_PROFIT_NOT_CONFIRMED",
    }

    highest_contiguous_stage = 0
    for stage_number, _, completed in stage_rows:
        if completed:
            highest_contiguous_stage = stage_number
            continue
        break

    def _checkpoint_state(*, passed: bool, waiting: bool = False, not_applicable: bool = False, completed_historically: bool = False) -> str:
        if passed:
            return "PASSED"
        if completed_historically:
            return "COMPLETED_HISTORICALLY"
        if not_applicable:
            return "NOT_APPLICABLE"
        if waiting:
            return "WAITING"
        return "FAILED"

    checkpoint_rows = [
        ("provider_connection_connected", provider_connected, _checkpoint_state(passed=provider_connected)),
        ("provider_readiness_acceptable", provider_ready, _checkpoint_state(passed=provider_ready)),
        ("provider_balance_fresh", provider_balance_fresh, _checkpoint_state(passed=provider_balance_fresh, completed_historically=stage_9_complete and not provider_balance_fresh)),
        ("provider_reconciliation_clean", provider_reconciliation_clean, _checkpoint_state(passed=provider_reconciliation_clean)),
        ("runtime_campaign_matches", runtime_campaign_matches, _checkpoint_state(passed=runtime_campaign_matches)),
        ("runtime_uses_dedicated_account", runtime_uses_dedicated_account, _checkpoint_state(passed=runtime_uses_dedicated_account)),
        ("profile_uses_dedicated_account", profile_uses_dedicated_account, _checkpoint_state(passed=profile_uses_dedicated_account)),
        ("dedicated_account_active", dedicated_account_active, _checkpoint_state(passed=dedicated_account_active)),
        ("paper_cash_reconciled", paper_cash_reconciled, _checkpoint_state(passed=paper_cash_reconciled)),
        ("paper_liquid_cash_supports_exact_5", paper_liquid_cash_supports_exact_5, _checkpoint_state(passed=paper_liquid_cash_supports_exact_5)),
        ("max_open_positions_one", max_open_positions_one, _checkpoint_state(passed=max_open_positions_one)),
        ("minimum_position_size_five", minimum_position_size_five, _checkpoint_state(passed=minimum_position_size_five)),
        ("maximum_position_size_five", maximum_position_size_five, _checkpoint_state(passed=maximum_position_size_five)),
        ("maximum_total_exposure_five", maximum_total_exposure_five, _checkpoint_state(passed=maximum_total_exposure_five)),
        ("latest_btc_candle_fresh_interval_aware", latest_btc_candle_fresh, _checkpoint_state(passed=latest_btc_candle_fresh, waiting=stage_1_complete and not latest_btc_candle_fresh)),
        ("latest_cycle_truthful_terminal", latest_cycle_truthful_terminal, _checkpoint_state(passed=latest_cycle_truthful_terminal)),
        ("worker_recently_ingested_kraken_btc", worker_recently_ingested, _checkpoint_state(passed=worker_recently_ingested, waiting=stage_1_complete and not worker_recently_ingested)),
        ("latest_strategy_identity_coherent", latest_strategy_coherent, _checkpoint_state(passed=latest_strategy_coherent)),
        ("decision_record_linkage_present_when_applicable", decision_record_linkage_present, _checkpoint_state(passed=decision_record_linkage_present, not_applicable=latest_cycle is None)),
        ("ready_package_progress_distinguishable", package_progress_distinguishable, _checkpoint_state(passed=ready_package_current, not_applicable=hold_no_package_expected, completed_historically=package_historically_advanced and not ready_package_current, waiting=not hold_no_package_expected and not ready_package_current and not package_historically_advanced)),
        ("exact_package_authorization_exists", exact_package_authorization, _checkpoint_state(passed=approval_event is not None, not_applicable=hold_no_package_expected and not package_historically_advanced, completed_historically=exact_package_authorization and approval_event is None, waiting=stage_3_complete and not exact_package_authorization)),
        ("production_dry_run_passed", dry_run_passed, _checkpoint_state(passed=bool(evidence.get("dry_run_passed")), not_applicable=hold_no_package_expected and not package_historically_advanced, completed_historically=dry_run_passed and not bool(evidence.get("dry_run_passed")), waiting=stage_4_complete and not dry_run_passed)),
        ("bounded_proving_activation_exists", bounded_activation_exists, _checkpoint_state(passed=activation is not None, not_applicable=hold_no_package_expected and not package_historically_advanced, completed_historically=bounded_activation_exists and activation is None, waiting=stage_5_complete and not bounded_activation_exists)),
        ("live_buy_order_submitted", autonomous_buy_submitted, _checkpoint_state(passed=autonomous_buy_submitted, waiting=stage_6_complete and not autonomous_buy_submitted, not_applicable=not stage_6_complete)),
        ("live_buy_fill_reconciled", autonomous_buy_fill_reconciled, _checkpoint_state(passed=autonomous_buy_fill_reconciled, waiting=autonomous_buy_submitted and not autonomous_buy_fill_reconciled, not_applicable=not stage_6_complete)),
        ("open_live_btc_position_exists", position_open, _checkpoint_state(passed=position_open, completed_historically=position_managed_historically and not position_open, waiting=stage_7_complete and not position_managed_historically, not_applicable=not stage_7_complete)),
        ("live_sell_order_submitted", autonomous_sell_submitted, _checkpoint_state(passed=autonomous_sell_submitted, waiting=stage_8_complete and not autonomous_sell_submitted, not_applicable=not stage_8_complete)),
        ("live_sell_fill_reconciled", autonomous_sell_fill_reconciled, _checkpoint_state(passed=autonomous_sell_fill_reconciled, waiting=autonomous_sell_submitted and not autonomous_sell_fill_reconciled, not_applicable=not stage_8_complete)),
        ("realized_fees_known", fees_known, _checkpoint_state(passed=fees_known, waiting=stage_9_complete and not fees_known, not_applicable=not stage_9_complete)),
        ("ending_usd_exceeds_starting_usd", ending_usd_exceeds_starting_usd, _checkpoint_state(passed=ending_usd_exceeds_starting_usd, waiting=stage_9_complete and fees_known and not ending_usd_exceeds_starting_usd, not_applicable=not stage_9_complete)),
    ]

    completed_checkpoint_count = sum(1 for _, _, state in checkpoint_rows if state in {"PASSED", "COMPLETED_HISTORICALLY"})
    total_checkpoint_count = len(checkpoint_rows)

    first_profit_complete = stage_10_complete

    critical_blocking_gate = next(
        (
            name
            for name, passed, _ in checkpoint_rows
            if not passed
            and name
            in {
                "provider_connection_connected",
                "provider_readiness_acceptable",
                "provider_reconciliation_clean",
                "runtime_campaign_matches",
                "runtime_uses_dedicated_account",
                "profile_uses_dedicated_account",
                "dedicated_account_active",
                "paper_cash_reconciled",
                "latest_strategy_identity_coherent",
                "decision_record_linkage_present_when_applicable",
            }
        ),
        None,
    )

    if first_profit_complete:
        status = "FIRST_AUTONOMOUS_NET_PROFIT_COMPLETE"
        blocking_gate = None
    elif critical_blocking_gate is not None:
        status = "BLOCKED"
        blocking_gate = critical_blocking_gate
    elif freshness_verdict != "fresh":
        status = "WAITING_FOR_FRESH_MARKET_DATA"
        blocking_gate = "latest_btc_candle_fresh_interval_aware"
    elif ready_package is not None and approval_event is None:
        status = "READY_PACKAGE_AVAILABLE"
        blocking_gate = "exact_package_authorization_exists"
    elif approval_event is not None and not bool(evidence.get("dry_run_passed")):
        status = "WAITING_FOR_DRY_RUN"
        blocking_gate = "production_dry_run_passed"
    elif bool(evidence.get("dry_run_passed")) and activation is None:
        status = "WAITING_FOR_ACTIVATION"
        blocking_gate = "bounded_proving_activation_exists"
    elif buy_submitted and not buy_fill_reconciled:
        status = "WAITING_FOR_BUY_FILL"
        blocking_gate = "live_buy_fill_reconciled"
    elif position_open and not sell_submitted:
        status = "POSITION_OPEN"
        blocking_gate = "live_sell_order_submitted"
    elif sell_submitted and not sell_fill_reconciled:
        status = "WAITING_FOR_SELL_FILL"
        blocking_gate = "live_sell_fill_reconciled"
    elif buy_fill_reconciled and sell_fill_reconciled:
        status = "VERIFYING_NET_PROFIT"
        blocking_gate = "ending_usd_exceeds_starting_usd"
    elif latest_cycle_outcome == "HOLD":
        status = "WAITING_FOR_EXECUTABLE_SIGNAL"
        blocking_gate = stage_blocking_gate_map.get(highest_contiguous_stage + 1, "READY_PACKAGE_NOT_YET_CREATED")
    else:
        status = "BLOCKED"
        blocking_gate = next((name for name, passed, _ in checkpoint_rows if not passed and name != "ready_package_progress_distinguishable"), "missing_safety_evidence")

    completion_percent = _FIRST_PROFIT_STAGE_ANCHORS.get(highest_contiguous_stage, 0.0)

    next_action_map = {
        "BLOCKED": "run evidence audit and fix the first failed safety gate",
        "WAITING_FOR_FRESH_MARKET_DATA": "wait for next closed Kraken BTC 15m candle ingestion",
        "WAITING_FOR_EXECUTABLE_SIGNAL": "wait for actionable BUY or SELL decision evidence",
        "READY_PACKAGE_AVAILABLE": "record canonical package authorization",
        "WAITING_FOR_AUTHORIZATION": "record canonical package authorization",
        "WAITING_FOR_DRY_RUN": "run canonical package dry run",
        "WAITING_FOR_ACTIVATION": "activate bounded proving",
        "WAITING_FOR_BUY_FILL": "wait for BUY fill reconciliation",
        "POSITION_OPEN": "wait for SELL signal and submit bounded SELL",
        "WAITING_FOR_SELL_FILL": "wait for SELL fill reconciliation",
        "VERIFYING_NET_PROFIT": "verify reconciled net profit including fees",
        "FIRST_AUTONOMOUS_NET_PROFIT_COMPLETE": "record milestone completion evidence",
    }

    safe_to_submit_order_now = status in {"READY_PACKAGE_AVAILABLE", "WAITING_FOR_AUTHORIZATION", "WAITING_FOR_DRY_RUN", "WAITING_FOR_ACTIVATION"} and open_live_order_count == 0 and unresolved_reconciliation_count == 0

    return {
        "completion_percent": completion_percent,
        "completed_checkpoint_count": completed_checkpoint_count,
        "total_checkpoint_count": total_checkpoint_count,
        "status": status,
        "blocking_gate": blocking_gate,
        "latest_cycle_id": None if latest_cycle is None else str(latest_cycle.cycle_id),
        "latest_cycle_outcome": latest_cycle_outcome,
        "latest_cycle_reason": latest_cycle_reason,
        "ready_package_id": None if ready_package is None else str(ready_package.package_id),
        "activation_id": None if activation is None else str(activation.activation_id),
        "provider_equity": None if provider_equity is None else format(Decimal(str(provider_equity)), "f"),
        "paper_liquid_cash": None if paper_liquid_cash is None else format(Decimal(str(paper_liquid_cash)), "f"),
        "open_live_order_count": open_live_order_count,
        "unresolved_reconciliation_count": unresolved_reconciliation_count,
        "live_position_state": "OPEN" if position_open else "FLAT",
        "realized_gross_profit": None if realized_gross_profit is None else format(Decimal(str(realized_gross_profit)), "f"),
        "fees": None if fees is None else format(Decimal(str(fees)), "f"),
        "realized_net_profit": None if realized_net_profit is None else format(Decimal(str(realized_net_profit)), "f"),
        "safe_to_submit_order_now": safe_to_submit_order_now,
        "exact_next_operator_action": next_action_map.get(status, "review checkpoint evidence"),
        "stage": {
            "highest_contiguous_completed": highest_contiguous_stage,
            "name": dict((n, s) for n, s, _ in stage_rows).get(highest_contiguous_stage, "NONE"),
            "rows": [
                {
                    "number": stage_number,
                    "name": stage_name,
                    "completed": completed,
                    "anchor_percent": _FIRST_PROFIT_STAGE_ANCHORS[stage_number],
                }
                for stage_number, stage_name, completed in stage_rows
            ],
        },
        "evidence": {
            "provider_balance_synced_at": None if provider_balance_synced_at is None else provider_balance_synced_at.isoformat(),
            "latest_candle_close_time": None if latest_candle is None else latest_candle.close_time.isoformat(),
            "evaluation_time": now.isoformat(),
            "freshness_seconds": freshness_seconds,
            "freshness_minutes": freshness_minutes,
            "candle_interval_minutes": candle_interval_minutes,
            "ingestion_grace_minutes": ingestion_grace_minutes,
            "maximum_age_minutes": maximum_age_minutes,
            "freshness_verdict": freshness_verdict,
            "latest_decision_record_id": latest_cycle_decision_record_id,
            "latest_strategy_identity": latest_strategy_identity,
            "latest_strategy_version": latest_strategy_version,
            "latest_ingestion_candle_at": None if latest_ingestion_candle_at is None else latest_ingestion_candle_at.isoformat(),
            "approval_event_id": None if approval_event is None else str(approval_event.id),
            "dry_run_live_crypto_order_id": None if activation is None else str(activation.dry_run_live_crypto_order_id),
            "autonomous_buy_provenance": autonomous_buy_provenance,
            "autonomous_sell_provenance": autonomous_sell_provenance,
            "starting_reconciled_usd": None if starting_reconciled_usd is None else format(Decimal(str(starting_reconciled_usd)), "f"),
            "ending_reconciled_usd": None if ending_reconciled_usd is None else format(Decimal(str(ending_reconciled_usd)), "f"),
            "package_progress_mode": (
                "hold_no_package_expected"
                if hold_no_package_expected
                else "ready_package_current"
                if ready_package_current
                else "ready_package_historically_advanced"
                if package_historically_advanced
                else "undetermined"
            ),
        },
        "checkpoints": [
            {"name": name, "passed": passed, "state": state}
            for name, passed, state in checkpoint_rows
        ],
        "formula": {
            "completion_percent": "stage anchor for highest contiguous completed stage",
            "stage_anchors": _FIRST_PROFIT_STAGE_ANCHORS,
            "milestone_complete": "autonomous_buy_provenance AND autonomous_buy_fill_reconciled AND autonomous_sell_provenance AND autonomous_sell_fill_reconciled AND fees_known AND ending_usd_exceeds_starting_usd AND realized_net_profit > 0",
        },
    }


async def _gather_first_autonomous_profit_evidence(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        connection = await db.scalar(
            select(ExchangeConnection)
            .where(ExchangeConnection.provider == provider)
            .where(ExchangeConnection.environment == environment)
            .order_by(desc(ExchangeConnection.updated_at), desc(ExchangeConnection.exchange_connection_id))
            .limit(1)
        )
        runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.id == runtime_campaign_id).limit(1))
        definition = await db.scalar(
            select(CapitalCampaignDefinition)
            .where(CapitalCampaignDefinition.campaign_id == campaign_id)
            .where(CapitalCampaignDefinition.version == campaign_version)
            .limit(1)
        )
        paper_account = await db.get(PaperAccount, paper_account_id)
        profile = await db.get(LiveTradingProfile, live_trading_profile_id)

        asset_symbol = _product_symbol(product_id)
        asset = await db.scalar(
            select(Asset)
            .where(Asset.symbol == asset_symbol)
            .where(Asset.exchange == provider)
            .where(Asset.asset_class == "crypto")
            .where(Asset.is_active.is_(True))
            .order_by(desc(Asset.created_at), desc(Asset.id))
            .limit(1)
        )

        latest_candle = None
        latest_ingestion_candle_at = None
        if asset is not None:
            latest_candle = await db.scalar(
                select(Candle)
                .where(Candle.asset_id == asset.id)
                .where(Candle.interval == _KRKN_BTC_INTERVAL)
                .where(Candle.close_time <= now)
                .order_by(desc(Candle.close_time), desc(Candle.id))
                .limit(1)
            )
            latest_ingestion_candle_at = await db.scalar(
                select(Candle.created_at)
                .where(Candle.asset_id == asset.id)
                .where(Candle.interval == _KRKN_BTC_INTERVAL)
                .order_by(desc(Candle.close_time), desc(Candle.id))
                .limit(1)
            )

        latest_cycle = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.capital_campaign_id == campaign_id)
            .where(AutonomousCycleRun.capital_campaign_version == campaign_version)
            .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
            .limit(1)
        )

        ready_package = await db.scalar(
            select(CanonicalPreviewPackage)
            .where(CanonicalPreviewPackage.campaign_id == campaign_id)
            .where(CanonicalPreviewPackage.campaign_version == campaign_version)
            .where(CanonicalPreviewPackage.package_state == "READY")
            .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id))
            .limit(1)
        )

        approval_event = None
        if ready_package is not None:
            approval_rows = list(
                (
                    await db.execute(
                        select(LiveApprovalEvent)
                        .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
                        .where(LiveApprovalEvent.approval_state == "approved")
                        .where(LiveApprovalEvent.checkpoint_type == "bounded_proving_entry")
                        .order_by(desc(LiveApprovalEvent.recorded_at), desc(LiveApprovalEvent.id))
                        .limit(100)
                    )
                ).scalars().all()
            )
            for item in approval_rows:
                scope = item.approval_scope if isinstance(item.approval_scope, dict) else {}
                if str(scope.get("canonical_preview_package_id") or "") == str(ready_package.package_id):
                    approval_event = item
                    break

        activation = await db.scalar(
            select(CanonicalProvingActivation)
            .where(CanonicalProvingActivation.campaign_id == campaign_id)
            .where(CanonicalProvingActivation.campaign_version == campaign_version)
            .where(CanonicalProvingActivation.provider == provider)
            .where(CanonicalProvingActivation.environment == environment)
            .where(CanonicalProvingActivation.product == product_id)
            .order_by(desc(CanonicalProvingActivation.activated_at), desc(CanonicalProvingActivation.activation_id))
            .limit(1)
        )

        unresolved_reconciliation_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id)
                    .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATES)))
                )
            )
            or 0
        )
        unknown_reconciliation_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id)
                    .where(LiveReconciliationEvent.reconciliation_status == "unknown")
                )
            )
            or 0
        )
        open_live_order_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == provider)
                    .where(LiveCryptoOrder.environment == environment)
                    .where(LiveCryptoOrder.product_id == product_id)
                    .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                )
            )
            or 0
        )

        orders = list(
            (
                await db.execute(
                    select(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == provider)
                    .where(LiveCryptoOrder.environment == environment)
                    .where(LiveCryptoOrder.product_id == product_id)
                    .order_by(desc(LiveCryptoOrder.created_at), desc(LiveCryptoOrder.live_crypto_order_id))
                    .limit(200)
                )
            ).scalars().all()
        )
        buy_submitted = any(str(item.side).upper() == "BUY" and item.submitted_at is not None for item in orders)
        buy_fill_reconciled = any(str(item.side).upper() == "BUY" and item.filled_at is not None for item in orders)
        sell_submitted = any(str(item.side).upper() == "SELL" and item.submitted_at is not None for item in orders)
        sell_fill_reconciled = any(str(item.side).upper() == "SELL" and item.filled_at is not None for item in orders)
        autonomous_buy_provenance = any(
            str(item.side).upper() == "BUY"
            and item.decision_record_id is not None
            and item.submitted_at is not None
            for item in orders
        )
        autonomous_sell_provenance = any(
            str(item.side).upper() == "SELL"
            and item.decision_record_id is not None
            and item.submitted_at is not None
            for item in orders
        )

        position_open = False
        if asset is not None and paper_account is not None:
            trades = list(
                (
                    await db.execute(
                        select(Trade)
                        .where(Trade.paper_account_id == paper_account.id)
                        .where(Trade.asset_id == asset.id)
                        .order_by(Trade.executed_at.asc(), Trade.id.asc())
                    )
                ).scalars().all()
            )
            net_qty = Decimal("0")
            for item in trades:
                qty = Decimal(str(item.quantity))
                if str(item.side).lower() == "buy":
                    net_qty += qty
                elif str(item.side).lower() == "sell":
                    net_qty -= qty
            position_open = net_qty > Decimal("0")

        dry_run_passed = False
        if activation is not None:
            dry_run_order = await db.get(LiveCryptoOrder, activation.dry_run_live_crypto_order_id)
            dry_run_passed = dry_run_order is not None and str(dry_run_order.status) == "DRY_RUN_READY"

    realized_net_profit = None if runtime is None else Decimal(str(runtime.realized_profit))
    fees = None if runtime is None else Decimal(str(runtime.fees))
    realized_gross_profit = None if realized_net_profit is None or fees is None else (realized_net_profit + fees)
    starting_reconciled_usd = None if runtime is None else Decimal(str(runtime.starting_capital))
    ending_reconciled_usd = None if runtime is None else Decimal(str(runtime.current_equity))
    paper_liquid_cash = None if paper_account is None else Decimal(str(paper_account.current_cash_balance))
    provider_equity = None if connection is None else connection.total_equity_usd

    return {
        "now": now,
        "campaign_id": campaign_id,
        "campaign_version": campaign_version,
        "paper_account_id": paper_account_id,
        "connection": connection,
        "runtime": runtime,
        "definition": definition,
        "paper_account": paper_account,
        "profile": profile,
        "latest_candle": latest_candle,
        "latest_ingestion_candle_at": latest_ingestion_candle_at,
        "latest_cycle": latest_cycle,
        "ready_package": ready_package,
        "approval_event": approval_event,
        "activation": activation,
        "unresolved_reconciliation_count": unresolved_reconciliation_count,
        "unknown_reconciliation_count": unknown_reconciliation_count,
        "open_live_order_count": open_live_order_count,
        "buy_submitted": buy_submitted,
        "buy_fill_reconciled": buy_fill_reconciled,
        "sell_submitted": sell_submitted,
        "sell_fill_reconciled": sell_fill_reconciled,
        "autonomous_buy_provenance": autonomous_buy_provenance,
        "autonomous_sell_provenance": autonomous_sell_provenance,
        "position_open": position_open,
        "dry_run_passed": dry_run_passed,
        "provider_equity": provider_equity,
        "paper_liquid_cash": paper_liquid_cash,
        "provider_readiness_verdict": None if connection is None else connection.last_readiness_verdict,
        "provider_balance_synced_at": None if connection is None else connection.last_successful_sync_at,
        "starting_reconciled_usd": starting_reconciled_usd,
        "ending_reconciled_usd": ending_reconciled_usd,
        "realized_gross_profit": realized_gross_profit,
        "fees": fees,
        "realized_net_profit": realized_net_profit,
    }


async def first_autonomous_profit_status(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    evidence = await _gather_first_autonomous_profit_evidence(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        runtime_campaign_id=runtime_campaign_id,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
    )
    payload = _derive_first_autonomous_profit_status(evidence)
    payload["invariants"] = {
        "read_only": True,
        "no_provider_order_submission": True,
        "checkpoint_count": 30,
    }
    return payload


def _runtime_exchange_scope(runtime_exchange: str | None) -> tuple[str | None, str | None]:
    raw = (runtime_exchange or "").strip().lower()
    if not raw:
        return None, None
    if raw.endswith("_sandbox"):
        return raw.removesuffix("_sandbox"), "sandbox"
    return raw, "production"


def _coerce_decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _resolve_since_datetime(value: str) -> datetime:
    raw = value.strip()
    lowered = raw.lower()
    if lowered in {"now", "0", "0m", "0h", "0d"}:
        return datetime.now(timezone.utc)

    relative = re.fullmatch(r"(\d+)\s+(minute|minutes|hour|hours|day|days)\s+ago", lowered)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if "minute" in unit:
            return datetime.now(timezone.utc) - timedelta(minutes=amount)
        if "hour" in unit:
            return datetime.now(timezone.utc) - timedelta(hours=amount)
        return datetime.now(timezone.utc) - timedelta(days=amount)

    candidate = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_uuid_list(values: Any) -> list[uuid.UUID]:
    if not isinstance(values, list):
        return []
    out: list[uuid.UUID] = []
    for value in values:
        try:
            out.append(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _sum_trade_quantity(trades: list[Trade], *, side: str) -> Decimal:
    total = Decimal("0")
    for trade in trades:
        if trade.side == side:
            total += Decimal(str(trade.quantity))
    return total


def _infer_non_candidate_reason(signals: list[Signal]) -> str:
    if not signals:
        return "UNPROVEN"
    actionable = [item for item in signals if item.action in {"buy", "sell"}]
    if not actionable:
        return "HOLD"
    return "UNPROVEN"


def _event_payload_campaign_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        campaign_id = metadata.get("campaign_id")
        if campaign_id is not None:
            return str(campaign_id)
    return None


async def _compute_position_quantity(
    *,
    db: Any,
    paper_account_id: uuid.UUID,
    asset_id: uuid.UUID,
    executed_at: datetime,
    include_trade_at_timestamp: bool,
) -> Decimal:
    trades = list(
        (
            await db.execute(
                select(Trade)
                .where(Trade.paper_account_id == paper_account_id)
                .where(Trade.asset_id == asset_id)
                .where(Trade.executed_at <= executed_at)
                .order_by(Trade.executed_at.asc(), Trade.id.asc())
            )
        ).scalars().all()
    )
    total = Decimal("0")
    for trade in trades:
        if not include_trade_at_timestamp and trade.executed_at == executed_at:
            continue
        qty = Decimal(str(trade.quantity))
        if trade.side == "buy":
            total += qty
        elif trade.side == "sell":
            total -= qty
    return max(Decimal("0"), total)


def _execution_summary_from_audits(audit_rows: list[dict[str, Any]], trades: list[Trade]) -> dict[str, Any]:
    actions = {str(item.get("action") or "") for item in audit_rows}
    service_called = any(action.startswith("signal_execution") for action in actions) or bool(trades)
    rejected = any("rejected" in action for action in actions)
    skipped = any("duplicate" in action for action in actions)
    errored = any("failed" in action for action in actions)
    return {
        "execution_service_called": service_called,
        "order_creation_reason": "paper_internal_sim_creates_trade_directly" if trades else "paper_order_model_absent",
        "trade_created": bool(trades),
        "rejected": rejected,
        "skipped": skipped,
        "error": errored,
    }


async def _build_cycle_forensics(*, db: Any, cycle: AutonomousCycleRun) -> dict[str, Any]:
    decision: DecisionRecord | None = None
    if cycle.decision_record_id is not None:
        decision = await db.get(DecisionRecord, cycle.decision_record_id)

    signal_ids: list[uuid.UUID] = []
    if decision is not None:
        source_lineage = decision.source_lineage or {}
        signal_ids = _safe_uuid_list(source_lineage.get("signals"))

    signals: list[Signal] = []
    if signal_ids:
        signals = list(
            (
                await db.execute(
                    select(Signal)
                    .where(Signal.id.in_(signal_ids))
                    .order_by(Signal.created_at.asc(), Signal.id.asc())
                )
            ).scalars().all()
        )

    strategy_map: dict[uuid.UUID, Strategy] = {}
    if signals:
        strategy_ids = sorted({item.strategy_id for item in signals}, key=str)
        if strategy_ids:
            strategies = list((await db.execute(select(Strategy).where(Strategy.id.in_(strategy_ids)))).scalars().all())
            strategy_map = {item.id: item for item in strategies}

    asset_map: dict[uuid.UUID, Asset] = {}
    if signals:
        asset_ids = sorted({item.asset_id for item in signals}, key=str)
        if asset_ids:
            assets = list((await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars().all())
            asset_map = {item.id: item for item in assets}

    risk_events: list[RiskEvent] = []
    if signal_ids:
        risk_events = list(
            (
                await db.execute(
                    select(RiskEvent)
                    .where(RiskEvent.related_signal_id.in_(signal_ids))
                    .order_by(RiskEvent.created_at.asc(), RiskEvent.id.asc())
                )
            ).scalars().all()
        )
    if cycle.risk_event_id is not None and all(item.id != cycle.risk_event_id for item in risk_events):
        extra_event = await db.get(RiskEvent, cycle.risk_event_id)
        if extra_event is not None:
            risk_events.append(extra_event)

    trades: list[Trade] = []
    if signal_ids:
        trades = list(
            (
                await db.execute(
                    select(Trade)
                    .where(Trade.signal_id.in_(signal_ids))
                    .order_by(Trade.executed_at.asc(), Trade.id.asc())
                )
            ).scalars().all()
        )

    audit_rows: list[dict[str, Any]] = []
    if signal_ids:
        audit_rows = [
            {
                "id": item.id,
                "created_at": item.created_at,
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "before_state": item.before_state,
                "after_state": item.after_state,
            }
            for item in (
                (
                    await db.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "signal")
                        .where(AuditLog.entity_id.in_(signal_ids))
                        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    )
                ).scalars().all()
            )
        ]

    execution_summary = _execution_summary_from_audits(audit_rows, trades)

    interval = decision.timeframe if decision is not None else None
    provider = None
    latest_candle_time = None
    primary_asset_id = signals[0].asset_id if signals else None
    primary_asset = asset_map.get(primary_asset_id) if primary_asset_id is not None else None
    if primary_asset is not None:
        provider = primary_asset.exchange
        if interval is None:
            interval = (cycle.cycle_context or {}).get("strategy_interval") if isinstance(cycle.cycle_context, dict) else None
        if interval is not None:
            latest_candle_time = await db.scalar(
                select(Candle.close_time)
                .where(Candle.asset_id == primary_asset.id)
                .where(Candle.interval == interval)
                .order_by(Candle.open_time.desc())
                .limit(1)
            )

    candidate = any(item.action in {"buy", "sell"} for item in signals)
    candidate_reason = None
    if not candidate:
        candidate_reason = _infer_non_candidate_reason(signals)

    accounting_entries: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    trade_fill_evidence = 0
    balance_change_observed = 0
    position_change_observed = 0
    position_change_unproven = 0
    balance_change_unproven = 0
    for trade in trades:
        total_fees += Decimal(str(trade.fee))
        before_position = await _compute_position_quantity(
            db=db,
            paper_account_id=trade.paper_account_id,
            asset_id=trade.asset_id,
            executed_at=trade.executed_at,
            include_trade_at_timestamp=False,
        )
        after_position = await _compute_position_quantity(
            db=db,
            paper_account_id=trade.paper_account_id,
            asset_id=trade.asset_id,
            executed_at=trade.executed_at,
            include_trade_at_timestamp=True,
        )

        trade_audit = await db.scalar(
            select(AuditLog)
            .where(AuditLog.entity_type == "trade")
            .where(AuditLog.entity_id == trade.id)
            .where(AuditLog.action == "paper_trade_simulated")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        before_balance = None
        after_balance = None
        if trade_audit is not None:
            before_state = trade_audit.before_state if isinstance(trade_audit.before_state, dict) else {}
            after_state = trade_audit.after_state if isinstance(trade_audit.after_state, dict) else {}
            before_balance = before_state.get("cash_balance")
            after_balance = after_state.get("cash_balance")
            trade_fill_evidence += 1
        if before_balance is not None and after_balance is not None and str(before_balance) != str(after_balance):
            balance_change_observed += 1
        elif before_balance is None or after_balance is None:
            balance_change_unproven += 1

        if before_position != after_position:
            position_change_observed += 1
        elif before_position is None or after_position is None:
            position_change_unproven += 1

        accounting_entries.append(
            {
                "trade_id": trade.id,
                "paper_account_id": trade.paper_account_id,
                "asset_id": trade.asset_id,
                "balance_before": before_balance,
                "balance_after": after_balance,
                "position_before": _decimal_str(before_position),
                "position_after": _decimal_str(after_position),
                "fee": _decimal_str(trade.fee),
                "executed_at": trade.executed_at,
            }
        )

    roster_runs = list(
        (
            await db.execute(
                select(StrategyRosterRun)
                .where(StrategyRosterRun.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterRun.started_at.asc(), StrategyRosterRun.roster_run_id.asc())
            )
        ).scalars().all()
    )

    roster_proposals = list(
        (
            await db.execute(
                select(StrategyRosterProposal)
                .where(StrategyRosterProposal.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterProposal.evaluated_at.asc(), StrategyRosterProposal.proposal_id.asc())
            )
        ).scalars().all()
    )

    outcome_score_rows = list(
        (
            await db.execute(
                select(StrategyRosterProposalOutcome)
                .join(
                    StrategyRosterProposal,
                    StrategyRosterProposalOutcome.proposal_id == StrategyRosterProposal.proposal_id,
                )
                .where(StrategyRosterProposal.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterProposalOutcome.evaluated_at.asc(), StrategyRosterProposalOutcome.outcome_id.asc())
            )
        ).scalars().all()
    )

    event_start = cycle.started_at - timedelta(minutes=5)
    event_end = (cycle.completed_at or cycle.started_at) + timedelta(minutes=30)
    research_events = list(
        (
            await db.execute(
                select(ValidationRunEvent)
                .where(ValidationRunEvent.created_at >= event_start)
                .where(ValidationRunEvent.created_at <= event_end)
                .where(ValidationRunEvent.event_type.like("RESEARCH_CYCLE_%"))
                .order_by(ValidationRunEvent.created_at.asc(), ValidationRunEvent.id.asc())
            )
        ).scalars().all()
    )

    signal_rows = []
    for signal in signals:
        strategy = strategy_map.get(signal.strategy_id)
        signal_rows.append(
            {
                "signal_id": signal.id,
                "strategy_id": signal.strategy_id,
                "strategy": None if strategy is None else strategy.slug,
                "action": signal.action.upper(),
                "confidence": _decimal_str(signal.ai_confidence),
                "reason": None,
                "status": signal.status,
                "asset_id": signal.asset_id,
            }
        )

    cycle_context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    cycle_handoff = cycle_context.get("execution_handoff") if isinstance(cycle_context.get("execution_handoff"), dict) else {}
    strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
    strategy_signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context.get("signal_payload"), dict) else {}
    autonomous_proposed_action = (getattr(cycle, "proposed_action", None) or strategy_signal_payload.get("action") or "HOLD").upper()
    if autonomous_proposed_action not in {"BUY", "SELL", "HOLD"}:
        autonomous_proposed_action = "HOLD"

    roster_buy = sum(1 for item in roster_proposals if str(item.action).upper() == "BUY")
    roster_sell = sum(1 for item in roster_proposals if str(item.action).upper() == "SELL")
    roster_hold = sum(1 for item in roster_proposals if str(item.action).upper() == "HOLD")
    roster_mode = "SHADOW"
    roster_executable = "NO"
    roster_reason = "Strategy Roster proposals are shadow research observations and never executable orders"

    canonical_signal = cycle_handoff.get("canonical_signal") if isinstance(cycle_handoff.get("canonical_signal"), dict) else None

    if cycle_handoff:
        execution_handoff_status = str(cycle_handoff.get("execution_handoff") or "UNPROVEN")
        cycle_handoff_status = str(cycle_handoff.get("status") or "UNPROVEN")
        if cycle_handoff_status in {"PAPER_EXECUTION_FAILED", "PAPER_EXECUTION_REJECTED", "PAPER_EXECUTION_SKIPPED"}:
            execution_handoff_blocker = str(cycle_handoff.get("exact_result") or cycle_handoff_status)
        else:
            execution_handoff_blocker = "NOT APPLICABLE"
    elif signal_rows:
        execution_handoff_status = "LEGACY_SIGNAL_PIPELINE"
        execution_handoff_blocker = "NOT APPLICABLE"
    elif autonomous_proposed_action in {"BUY", "SELL"}:
        execution_handoff_status = "NOT IMPLEMENTED"
        execution_handoff_blocker = "AUTONOMOUS_CANONICAL_SIGNAL_HANDOFF_NOT_IMPLEMENTED"
    else:
        execution_handoff_status = "NOT APPLICABLE"
        execution_handoff_blocker = "HOLD_ACTION"

    summary = "No legacy executable signals linked to this autonomous cycle"
    if cycle_handoff:
        summary = str(cycle_handoff.get("status") or "UNPROVEN")
    elif candidate and execution_summary.get("trade_created"):
        summary = "Actionable signal became paper trade"
    elif candidate and execution_summary.get("rejected"):
        summary = "Actionable signal rejected before trade"
    elif candidate and execution_summary.get("skipped"):
        summary = "Actionable signal skipped"
    elif candidate and not execution_summary.get("execution_service_called"):
        summary = "Actionable signal not executed"

    candidate_status = "UNPROVEN" if not signal_rows and canonical_signal is None else ("YES" if candidate else "NO")
    if canonical_signal is not None and str(canonical_signal.get("executable") or "NO").upper() == "YES":
        candidate_status = "YES"
    risk_evaluated_status = "YES" if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")
    risk_decision = risk_events[-1].action_taken if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")
    risk_reason = risk_events[-1].detail if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")

    execution_attempted_status = "YES" if bool(cycle_handoff.get("attempted")) else ("YES" if candidate else "NO")
    execution_service_called_status = (
        "YES"
        if (bool(cycle_handoff.get("attempted")) or execution_summary.get("execution_service_called"))
        else "UNPROVEN"
        if candidate
        else "NOT APPLICABLE"
    )
    order_created_status = "NOT APPLICABLE"
    trade_created_status = "YES" if execution_summary.get("trade_created") else "NO"
    if trades:
        filled_status = "YES" if trade_fill_evidence == len(trades) else "UNPROVEN"
    elif candidate:
        filled_status = "NO"
    else:
        filled_status = "NOT APPLICABLE"

    rejected_status = "YES" if execution_summary.get("rejected") else ("NO" if candidate else "NOT APPLICABLE")
    skipped_status = "YES" if execution_summary.get("skipped") else ("NO" if candidate else "NOT APPLICABLE")
    error_status = "YES" if execution_summary.get("error") else ("NO" if candidate else "NOT APPLICABLE")

    decision_record_linkage_status = "YES" if cycle.decision_record_id is not None else ("UNPROVEN" if signal_rows else "NOT APPLICABLE")
    outcome_linkage_status = (
        "YES"
        if outcome_score_rows
        else "NO"
        if cycle.decision_record_id is not None
        else "UNPROVEN"
    )
    research_linkage_status = "YES" if research_events else "NO"

    account_balance_changed_status = (
        "YES"
        if balance_change_observed > 0
        else "UNPROVEN"
        if trades and balance_change_unproven > 0
        else "NO"
        if trades
        else "NOT APPLICABLE"
    )
    position_changed_status = (
        "YES"
        if position_change_observed > 0
        else "UNPROVEN"
        if trades and position_change_unproven > 0
        else "NO"
        if trades
        else "NOT APPLICABLE"
    )
    accounting_entry_status = "YES" if trade_fill_evidence > 0 else ("UNPROVEN" if trades else "NOT APPLICABLE")

    return {
        "cycle_id": cycle.cycle_id,
        "timestamp": cycle.started_at,
        "asset": None if primary_asset is None else primary_asset.symbol,
        "asset_id": primary_asset_id,
        "provider": provider,
        "interval": interval,
        "latest_candle_time": latest_candle_time,
        "signal_section": {
            "signals_generated": len(signal_rows),
            "signals": signal_rows,
            "source": "signals_table_via_decision_lineage",
        },
        "strategy_roster": {
            "proposal_count": len(roster_proposals),
            "buy_count": roster_buy,
            "sell_count": roster_sell,
            "hold_count": roster_hold,
            "mode": roster_mode,
            "executable": roster_executable,
            "reason": roster_reason,
        },
        "canonical_signal": {
            "signal_id": (canonical_signal or {}).get("signal_id"),
            "action": (canonical_signal or {}).get("action"),
            "executable": (canonical_signal or {}).get("executable", "NO"),
            "mode": (canonical_signal or {}).get("mode", "PAPER"),
        },
        "autonomous_decision": {
            "proposed_action": autonomous_proposed_action,
            "mandate_verdict": getattr(cycle, "mandate_verdict", None) or "UNPROVEN",
            "risk_verdict": getattr(cycle, "risk_verdict", None) or "UNPROVEN",
            "execution_handoff": execution_handoff_status,
            "exact_blocker": execution_handoff_blocker,
        },
        "execution_candidate": {
            "is_candidate": candidate,
            "status": candidate_status,
            "reason_if_no": candidate_reason if candidate_status == "NO" else "NOT APPLICABLE",
        },
        "risk": {
            "evaluated_status": risk_evaluated_status,
            "decision": risk_decision,
            "reason": risk_reason,
            "risk_event_ids": [item.id for item in risk_events],
        },
        "execution": {
            "execution_attempted_status": execution_attempted_status,
            "execution_service_called_status": execution_service_called_status,
            "exact_result": cycle_handoff.get("exact_result") if cycle_handoff else None,
            "order_created_status": order_created_status,
            "order_creation_reason": execution_summary.get("order_creation_reason"),
            "trade_created_status": trade_created_status,
            "filled_status": filled_status,
            "rejected_status": rejected_status,
            "skipped_status": skipped_status,
            "error_status": error_status,
            "trade_ids": [item.id for item in trades],
            "signal_ids": signal_ids,
        },
        "accounting": {
            "paper_account_ids": sorted({item.paper_account_id for item in trades}, key=str),
            "entries": accounting_entries,
            "fees_total": _decimal_str(total_fees),
            "pnl": decision.pnl if decision is not None else None,
            "buy_quantity_total": _decimal_str(_sum_trade_quantity(trades, side="buy")),
            "sell_quantity_total": _decimal_str(_sum_trade_quantity(trades, side="sell")),
            "account_balance_changed_status": account_balance_changed_status,
            "position_changed_status": position_changed_status,
            "accounting_entry_persisted_status": accounting_entry_status,
        },
        "decision_records": {
            "decision_record_id": cycle.decision_record_id,
            "outcome_score_linkage_count": len(outcome_score_rows),
            "outcome_score_ids": [item.outcome_id for item in outcome_score_rows],
            "decision_record_linkage_status": decision_record_linkage_status,
            "outcome_linkage_status": outcome_linkage_status,
            "research_linkage_status": research_linkage_status,
            "research_linkage": [
                {
                    "event_id": item.id,
                    "event_type": item.event_type,
                    "campaign_id": _event_payload_campaign_id(item.payload),
                    "created_at": item.created_at,
                }
                for item in research_events
            ],
            "autonomous_cycle_linkage": {
                "cycle_id": cycle.cycle_id,
                "scheduled_roster_run_ids": [item.roster_run_id for item in roster_runs],
            },
        },
        "summary": summary,
    }


async def fetch_execution_forensics(
    *,
    since: str | None,
    cycle_id: UUID | None,
    latest: bool,
) -> dict[str, Any]:
    selectors = int(bool(since)) + int(cycle_id is not None) + int(latest)
    if selectors != 1:
        raise ValueError("Choose exactly one selector: --since, --cycle, or --latest")

    async with AsyncSessionLocal() as db:
        cycles: list[AutonomousCycleRun]
        criteria: dict[str, Any] = {
            "selector": "latest" if latest else "cycle" if cycle_id is not None else "since",
            "since": since,
            "cycle_id": cycle_id,
        }

        if latest:
            item = await db.scalar(select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1))
            cycles = [] if item is None else [item]
        elif cycle_id is not None:
            item = await db.get(AutonomousCycleRun, cycle_id)
            if item is None:
                raise ValueError(f"Cycle {cycle_id} not found")
            cycles = [item]
        else:
            assert since is not None
            threshold = _resolve_since_datetime(since)
            criteria["resolved_since"] = threshold
            cycles = list(
                (
                    await db.execute(
                        select(AutonomousCycleRun)
                        .where(AutonomousCycleRun.started_at >= threshold)
                        .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
                        .limit(_EXECUTION_FORENSICS_MAX_SINCE_CYCLES)
                    )
                ).scalars().all()
            )
            criteria["max_cycles"] = _EXECUTION_FORENSICS_MAX_SINCE_CYCLES

        deduped_cycles: list[AutonomousCycleRun] = []
        seen_cycle_ids: set[uuid.UUID] = set()
        for item in cycles:
            cycle_key = item.cycle_id
            if cycle_key in seen_cycle_ids:
                continue
            seen_cycle_ids.add(cycle_key)
            deduped_cycles.append(item)
        cycles = deduped_cycles

        reports = [await _build_cycle_forensics(db=db, cycle=item) for item in cycles]

    return {
        "mode": "read_only_forensics",
        "criteria": criteria,
        "cycle_count": len(reports),
        "truncated": bool(since) and len(reports) >= _EXECUTION_FORENSICS_MAX_SINCE_CYCLES,
        "cycles": reports,
    }


def _seconds_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if later is None or earlier is None:
        return None
    delta = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds()))


def _preview_command_mode(*, replayed: bool, command_name: str) -> str:
    if command_name == "preview-show":
        return "VIEW_EXISTING"
    return "IDEMPOTENT_REPLAY" if replayed else "NEW_PREVIEW"


def _decision_classification(*, proposed_action: str | None, risk_verdict: str | None, deterministic_explanation: list[str], failure_reason: str | None) -> str:
    action = (proposed_action or "").upper()
    risk = (risk_verdict or "").upper()
    explanation_blob = " ".join(deterministic_explanation).lower()
    reason = (failure_reason or "").lower()

    if reason.startswith("mandate_status_") or "mandate_not_active" in explanation_blob or "mandate_version_invalid" in reason:
        return "MANDATE_REJECTED"
    if "reconciliation_not_ready" in reason or "provider_not_ready" in reason or "insufficient_candle_context" in explanation_blob or "exchange_connection_not_found" in reason:
        return "INFRASTRUCTURE_BLOCKED"
    if risk == "REJECTED":
        return "RISK_REJECTED"
    if action == "HOLD":
        if "strategy_evaluated" in explanation_blob or "signal_action=hold" in explanation_blob:
            return "STRATEGY_DERIVED"
        return "SAFETY_HOLD" if explanation_blob else "INFRASTRUCTURE_BLOCKED"
    if action in {"BUY", "SELL"}:
        return "STRATEGY_DERIVED"
    return "INFRASTRUCTURE_BLOCKED"


def _capital_state(*, preview: CryptoOrderPreview | None, proposed_action: str | None) -> str:
    if preview is not None:
        return "PREVIEW_ONLY"
    if (proposed_action or "").upper() == "HOLD":
        return "NONE"
    return "UNKNOWN"


def _build_timeline_payload(
    *,
    command_mode: str,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cycle_created_at = _parse_datetime(getattr(cycle, "created_at", None)) or _parse_datetime(getattr(cycle, "started_at", None))
    decision_created_at = _parse_datetime(getattr(decision, "timestamp", None))
    snapshot_created_at = _parse_datetime(getattr(snapshot, "timestamp", None))
    preview_created_at = _parse_datetime(getattr(preview, "created_at", None))

    cycle_context = getattr(cycle, "cycle_context", None) or {}
    timeline_context = {}
    if isinstance(cycle_context, dict):
        strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
        signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context, dict) else {}
        if isinstance(signal_payload, dict):
            timeline_context = signal_payload.get("timeline") if isinstance(signal_payload.get("timeline"), dict) else {}
        if not timeline_context and isinstance(cycle_context.get("timeline"), dict):
            timeline_context = cycle_context.get("timeline")

    latest_completed_candle_open = _parse_datetime(timeline_context.get("latest_completed_candle_open")) if isinstance(timeline_context, dict) else None
    latest_completed_candle_close = _parse_datetime(timeline_context.get("latest_completed_candle_close")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_open = _parse_datetime(timeline_context.get("oldest_candle_used_open")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_close = _parse_datetime(timeline_context.get("oldest_candle_used_close")) if isinstance(timeline_context, dict) else None
    evaluated_at = _parse_datetime(timeline_context.get("evaluated_at")) or decision_created_at or cycle_created_at or now

    cycle_age_seconds = _seconds_between(now, cycle_created_at)
    decision_age_seconds = _seconds_between(now, decision_created_at)
    snapshot_age_seconds = _seconds_between(now, snapshot_created_at)
    market_data_age_seconds = _seconds_between(now, latest_completed_candle_close)

    history_candle_count = timeline_context.get("history_candle_count") if isinstance(timeline_context, dict) else None
    current_candle_excluded = bool(timeline_context.get("current_incomplete_candle_excluded")) if isinstance(timeline_context, dict) else None
    decision_applies_to = timeline_context.get("decision_applies_to") if isinstance(timeline_context, dict) else None

    mismatch_warning = False
    if cycle_age_seconds is not None and decision_age_seconds is not None:
        if abs(cycle_age_seconds - decision_age_seconds) > 120:
            mismatch_warning = True

    return {
        "evaluated_at": evaluated_at,
        "cycle_created_at": cycle_created_at,
        "decision_created_at": decision_created_at,
        "snapshot_created_at": snapshot_created_at,
        "preview_created_at": preview_created_at,
        "latest_completed_candle_open": latest_completed_candle_open,
        "latest_completed_candle_close": latest_completed_candle_close,
        "oldest_candle_used_open": oldest_candle_used_open,
        "oldest_candle_used_close": oldest_candle_used_close,
        "history_candle_count": history_candle_count,
        "cycle_age_seconds": cycle_age_seconds,
        "decision_age_seconds": decision_age_seconds,
        "snapshot_age_seconds": snapshot_age_seconds,
        "market_data_age_seconds": market_data_age_seconds,
        "current_incomplete_candle_excluded": current_candle_excluded,
        "decision_applies_to": decision_applies_to,
        "age_sources": {
            "cycle_age_seconds": "autonomous_cycle_runs.created_at",
            "decision_age_seconds": "decision_records.timestamp",
            "snapshot_age_seconds": "decision_snapshots.timestamp",
            "market_data_age_seconds": "candles.close_time",
        },
        "timestamp_mismatch_warning": mismatch_warning,
    }


def _build_preview_evidence_payload(
    *,
    command_name: str,
    result: Any,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    evaluation_mode = _preview_command_mode(replayed=bool(getattr(result, "replayed", False)), command_name=command_name)
    command_mode = evaluation_mode
    if command_name == "preview-show":
        command_mode = "VIEW_EXISTING"

    proposed_action = getattr(result, "proposed_action", None) or getattr(cycle, "proposed_action", None) or "HOLD"
    risk_verdict = getattr(result, "risk_verdict", None) or getattr(cycle, "risk_verdict", None)
    deterministic_explanation = list(getattr(result.diagnostics, "deterministic_explanation", []) if getattr(result, "diagnostics", None) else [])
    if not deterministic_explanation and cycle is not None:
        deterministic_explanation = list(getattr(cycle, "deterministic_explanation", []) or [])

    timeline = _build_timeline_payload(
        command_mode=command_mode,
        cycle=cycle,
        decision=decision,
        snapshot=snapshot,
        preview=preview,
    )

    decision_classification = _decision_classification(
        proposed_action=proposed_action,
        risk_verdict=risk_verdict,
        deterministic_explanation=deterministic_explanation,
        failure_reason=getattr(result.diagnostics, "failure_reason", None) if getattr(result, "diagnostics", None) else getattr(cycle, "failure_reason", None),
    )

    capital_state = _capital_state(preview=preview, proposed_action=proposed_action)
    new_evaluation = command_mode == "NEW_PREVIEW"
    outcome = (proposed_action or "FAILED").upper() if command_mode != "VIEW_EXISTING" else (getattr(decision, "outcome", None) or (proposed_action or "FAILED")).upper()

    if command_mode == "VIEW_EXISTING":
        record_created = timeline.get("decision_created_at") or timeline.get("cycle_created_at")
    elif command_mode == "IDEMPOTENT_REPLAY":
        record_created = timeline.get("cycle_created_at")
    else:
        record_created = timeline.get("cycle_created_at") or timeline.get("decision_created_at")

    timeline_warning = bool(timeline.get("timestamp_mismatch_warning"))

    return {
        "command_mode": command_mode,
        "evaluation_mode": evaluation_mode,
        "outcome": outcome,
        "decision_classification": decision_classification,
        "capital_state": capital_state,
        "new_evaluation": new_evaluation,
        "record_created_at": record_created,
        "timeline": timeline,
        "timeline_warning": timeline_warning,
    }


async def execute_preview_cycle(
    *,
    mandate_id: UUID | None,
    actor: str,
    product_id: str,
    strategy_interval: str,
    trigger: str,
    idempotency_seed: str | None,
    software_build_version: str | None,
    forced_action: str | None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        resolved_mandate_id = mandate_id
        if resolved_mandate_id is None:
            resolved_mandate_id = await db.scalar(
                select(AutonomousCapitalMandate.mandate_id)
                .where(AutonomousCapitalMandate.status == "ACTIVE")
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            if resolved_mandate_id is None:
                resolved_mandate_id = await db.scalar(
                    select(AutonomousCapitalMandate.mandate_id)
                    .order_by(desc(AutonomousCapitalMandate.updated_at))
                    .limit(1)
                )
        if resolved_mandate_id is None:
            raise ValueError("No mandate found. Seed or create a mandate before running preview.")

        result = await run_autonomous_preview_cycle(
            db=db,
            request=AutonomousCycleRequest(
                mandate_id=resolved_mandate_id,
                actor=actor,
                product_id=product_id,
                strategy_interval=strategy_interval,
                trigger=trigger,
                idempotency_seed=idempotency_seed,
                software_build_version=software_build_version,
                forced_action=forced_action,
            ),
        )

        cycle = await db.get(AutonomousCycleRun, result.cycle_id)
        decision = await db.get(DecisionRecord, result.decision_record_id) if result.decision_record_id else None
        snapshot = await db.get(DecisionSnapshot, result.decision_record_id) if result.decision_record_id else None
        preview = await db.get(CryptoOrderPreview, result.preview_id) if result.preview_id else None

    payload = {
        "cycle_id": result.cycle_id,
        "state": result.state,
        "idempotency_key": result.idempotency_key,
        "mandate_id": result.mandate_id,
        "mandate_version_id": result.mandate_version_id,
        "proposed_action": result.proposed_action,
        "mandate_verdict": result.mandate_verdict,
        "risk_verdict": result.risk_verdict,
        "decision_record_id": result.decision_record_id,
        "preview_id": result.preview_id,
        "mandate_evaluation_id": result.mandate_evaluation_id,
        "risk_event_id": result.risk_event_id,
        "audit_correlation_id": result.audit_correlation_id,
        "replayed": result.replayed,
        "cycle_context": result.cycle_context,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "diagnostics": {
            "duration_ms": result.diagnostics.duration_ms,
            "evaluation_stage": result.diagnostics.evaluation_stage,
            "termination_stage": result.diagnostics.termination_stage,
            "failure_reason": result.diagnostics.failure_reason,
            "deterministic_explanation": list(result.diagnostics.deterministic_explanation),
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview",
            result=result,
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


def _resolve_git_sha() -> str | None:
    configured_sha = (
        Path(__file__).resolve().parents[4] / ".git" / "HEAD"
    )
    if configured_sha.exists():
        try:
            head_value = configured_sha.read_text(encoding="utf-8").strip()
            if head_value.startswith("ref:"):
                ref_path = head_value.split(":", 1)[1].strip()
                ref_file = configured_sha.parent / ref_path
                if ref_file.exists():
                    return ref_file.read_text(encoding="utf-8").strip()[:12]
            if head_value:
                return head_value[:12]
        except OSError:
            return None
    return None


async def fetch_preview_evidence(*, preview_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        preview = await db.get(CryptoOrderPreview, preview_id)
        if preview is None:
            raise ValueError(f"Preview {preview_id} not found")

        decision: DecisionRecord | None = None
        snapshot: DecisionSnapshot | None = None
        if preview.decision_record_id is not None:
            decision = await db.get(DecisionRecord, preview.decision_record_id)
            snapshot = await db.get(DecisionSnapshot, preview.decision_record_id)

        cycle: AutonomousCycleRun | None = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.preview_id == preview.crypto_order_preview_id)
            .order_by(desc(AutonomousCycleRun.started_at))
            .limit(1)
        )

    payload = {
        "preview": {
            "crypto_order_preview_id": preview.crypto_order_preview_id,
            "status": preview.status,
            "provider": preview.provider,
            "environment": preview.environment,
            "product_id": preview.product_id,
            "side": preview.side,
            "order_type": preview.order_type,
            "requested_amount": _coerce_decimal(preview.requested_amount),
            "requested_amount_currency": preview.requested_amount_currency,
            "quote_size": _coerce_decimal(preview.quote_size),
            "base_size": _coerce_decimal(preview.base_size),
            "estimated_average_price": _coerce_decimal(preview.estimated_average_price),
            "estimated_total_value": _coerce_decimal(preview.estimated_total_value),
            "estimated_base_size": _coerce_decimal(preview.estimated_base_size),
            "estimated_quote_size": _coerce_decimal(preview.estimated_quote_size),
            "estimated_fee": _coerce_decimal(preview.estimated_fee),
            "estimated_fee_currency": preview.estimated_fee_currency,
            "estimated_slippage": _coerce_decimal(preview.estimated_slippage),
            "estimated_commission_total": _coerce_decimal(preview.estimated_commission_total),
            "best_bid": _coerce_decimal(preview.best_bid),
            "best_ask": _coerce_decimal(preview.best_ask),
            "status_reason": preview.failure_reason,
            "warning_messages": list(preview.warning_messages or []),
            "readiness_verdict": preview.readiness_verdict,
            "risk_verdict": preview.risk_verdict,
            "risk_explanation": preview.risk_explanation,
            "decision_record_id": preview.decision_record_id,
            "risk_event_id": preview.risk_event_id,
            "audit_correlation_id": preview.audit_correlation_id,
            "created_at": preview.created_at,
            "updated_at": preview.updated_at,
            "expires_at": preview.expires_at,
        },
        "decision_record": {
            "decision_id": decision.decision_id if decision else None,
            "timeframe": decision.timeframe if decision else None,
            "trade_accepted": decision.trade_accepted if decision else None,
            "trade_rejected_reason": decision.trade_rejected_reason if decision else None,
            "outcome": decision.outcome if decision else None,
            "generated_signals": decision.generated_signals if decision else None,
            "indicators": decision.indicators if decision else None,
            "risk_adjustments": decision.risk_adjustments if decision else None,
            "supporting_strategies": decision.supporting_strategies if decision else None,
            "opposing_strategies": decision.opposing_strategies if decision else None,
            "execution_details": decision.execution_details if decision else None,
        },
        "decision_snapshot": {
            "decision_id": snapshot.decision_id if snapshot else None,
            "strategy_version": snapshot.strategy_version if snapshot else None,
            "configuration_version": snapshot.configuration_version if snapshot else None,
            "decision_engine_version": snapshot.decision_engine_version if snapshot else None,
            "generated_features": snapshot.generated_features if snapshot else None,
            "strategy_inputs": snapshot.strategy_inputs if snapshot else None,
            "risk_inputs": snapshot.risk_inputs if snapshot else None,
        },
        "cycle": {
            "cycle_id": cycle.cycle_id if cycle else None,
            "state": cycle.state if cycle else None,
            "evaluation_stage": cycle.evaluation_stage if cycle else None,
            "termination_stage": cycle.termination_stage if cycle else None,
            "failure_reason": cycle.failure_reason if cycle else None,
            "mandate_id": cycle.mandate_id if cycle else None,
            "mandate_version_id": cycle.mandate_version_id if cycle else None,
            "proposed_action": cycle.proposed_action if cycle else None,
            "risk_verdict": cycle.risk_verdict if cycle else None,
            "started_at": cycle.started_at if cycle else None,
            "completed_at": cycle.completed_at if cycle else None,
            "created_at": cycle.created_at if cycle else None,
            "deterministic_explanation": cycle.deterministic_explanation if cycle else None,
            "cycle_context": cycle.cycle_context if cycle else None,
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview-show",
            result=type("_PreviewResult", (), {"replayed": False, "proposed_action": preview.side, "risk_verdict": preview.risk_verdict, "diagnostics": type("_Diag", (), {"deterministic_explanation": cycle.deterministic_explanation if cycle else [], "failure_reason": cycle.failure_reason if cycle else None})()})(),
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


async def fetch_campaign_orchestration_readiness(*, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_readiness(db=db, campaign_id=campaign_id, version=version)


async def fetch_campaign_orchestration_preview(*, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_campaign_orchestration_preview_for_candle(db=db, campaign_id=campaign_id, version=version, allow_draft_preview=True)


async def fetch_campaign_orchestration_status(*, campaign_id: UUID, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_status(db=db, campaign_id=campaign_id, version=version)


async def fetch_campaign_orchestration_history(*, campaign_id: UUID, version: int | None, limit: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_history(db=db, campaign_id=campaign_id, version=version, limit=limit)


async def fetch_commissioned_control_plane_status(*, campaign_id: UUID, version: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _get_commissioned_control_plane_status(db=db, campaign_id=campaign_id, version=version)
        return result.model_dump(mode="json")


async def mutate_commissioned_control_plane_action(
    *,
    campaign_id: UUID,
    version: int,
    actor: str,
    action: str,
    idempotency_key: str,
    reason: str | None,
) -> dict[str, Any]:
    request = CommissionedControlPlaneMutationRequest(
        campaign_id=campaign_id,
        version=version,
        actor=actor,
        action=action,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    async with AsyncSessionLocal() as db:
        result = await _mutate_commissioned_control_plane(db=db, request=request)
        return result.model_dump(mode="json")


async def fetch_candle_readiness(
    *,
    symbol: str,
    interval: str,
    exchange: str | None,
    max_age_minutes: int,
    lookback_limit: int,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_exchange = exchange.strip().lower() if exchange else None

    async with AsyncSessionLocal() as db:
        asset_query = select(Asset).where(func.upper(Asset.symbol) == normalized_symbol)
        if normalized_exchange:
            asset_query = asset_query.where(func.lower(Asset.exchange) == normalized_exchange)
        assets = (await db.execute(asset_query.order_by(desc(Asset.created_at)).limit(2))).scalars().all()

        if not assets:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "asset_not_found",
            }

        if len(assets) > 1:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "ambiguous_asset_resolution",
            }

        asset = assets[0]
        latest_candle = await db.scalar(
            select(Candle)
            .where(Candle.asset_id == asset.id, Candle.interval == interval)
            .order_by(desc(Candle.open_time))
            .limit(1)
        )
        row_count = (
            await db.scalar(
                select(func.count())
                .select_from(Candle)
                .where(Candle.asset_id == asset.id, Candle.interval == interval)
            )
            or 0
        )

    if latest_candle is None:
        return {
            "symbol": asset.symbol,
            "exchange": asset.exchange,
            "interval": interval,
            "asset_id": asset.id,
            "row_count": int(row_count),
            "latest_open_time": None,
            "latest_close_time": None,
            "age_minutes": None,
            "ready": False,
            "reason": "no_candles",
        }

    now = datetime.now(timezone.utc)
    close_time = latest_candle.close_time
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((now - close_time).total_seconds() // 60))
    ready = age_minutes <= max_age_minutes

    return {
        "symbol": asset.symbol,
        "exchange": asset.exchange,
        "interval": interval,
        "asset_id": asset.id,
        "row_count": int(row_count),
        "latest_open_time": latest_candle.open_time,
        "latest_close_time": latest_candle.close_time,
        "age_minutes": age_minutes,
        "ready": ready,
        "reason": "ok" if ready else "stale_candles",
        "max_age_minutes": max_age_minutes,
        "lookback_limit": lookback_limit,
    }


async def fetch_operator_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        await db.execute(select(1))

        if mandate_id is not None:
            mandate: AutonomousCapitalMandate | None = await db.get(AutonomousCapitalMandate, mandate_id)
            if mandate is None:
                raise ValueError(f"Mandate {mandate_id} not found")
            cycle_stmt = (
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.mandate_id == mandate_id)
                .order_by(desc(AutonomousCycleRun.started_at))
                .limit(1)
            )
        else:
            mandate = await db.scalar(
                select(AutonomousCapitalMandate)
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            cycle_stmt = select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1)

        latest_cycle = await db.scalar(cycle_stmt)
        latest_preview = await db.scalar(select(CryptoOrderPreview).order_by(desc(CryptoOrderPreview.created_at)).limit(1))
        connections = (
            await db.execute(select(ExchangeConnection).order_by(ExchangeConnection.provider.asc(), ExchangeConnection.environment.asc()))
        ).scalars().all()
        campaign_count = int((await db.scalar(select(func.count()).select_from(CapitalCampaign))) or 0)
        decision_count = int((await db.scalar(select(func.count()).select_from(DecisionRecord))) or 0)
        open_preview_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(CryptoOrderPreview)
                    .where(CryptoOrderPreview.expires_at > now)
                )
            )
            or 0
        )

        open_live_orders = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(
                        func.lower(LiveCryptoOrder.status).notin_(
                            [
                                "filled",
                                "cancelled",
                                "failed",
                                "rejected",
                                "expired",
                                "settled",
                                "completed",
                            ]
                        )
                    )
                )
            )
            or 0
        )

    candle_summary: dict[str, Any] | None = None
    if candle_symbol:
        candle_summary = await fetch_candle_readiness(
            symbol=candle_symbol,
            interval=candle_interval,
            exchange=candle_exchange,
            max_age_minutes=candle_max_age_minutes,
            lookback_limit=200,
        )

    kraken_production = None
    for item in connections:
        if item.provider == "kraken_spot" and item.environment == "production":
            kraken_production = item
            break

    latest_strategy: dict[str, Any] = {"name": None, "version": None}
    open_positions: int | None = None
    if latest_cycle is not None:
        context = latest_cycle.cycle_context or {}
        strategy = context.get("strategy") if isinstance(context, dict) else None
        reconciliation = context.get("reconciliation_status") if isinstance(context, dict) else None
        if isinstance(strategy, dict):
            latest_strategy = {
                "name": strategy.get("name"),
                "version": strategy.get("version"),
            }
        if isinstance(reconciliation, dict) and isinstance(reconciliation.get("open_position_count"), int):
            open_positions = reconciliation.get("open_position_count")

    latest_signal = latest_cycle.proposed_action if latest_cycle else None
    worker_heartbeat = latest_cycle.completed_at if latest_cycle and latest_cycle.completed_at else None
    if worker_heartbeat is None and latest_cycle is not None:
        worker_heartbeat = latest_cycle.started_at

    system_health = "healthy"
    if kraken_production is not None and kraken_production.status not in {"connected"}:
        system_health = "degraded"
    if candle_summary and not candle_summary.get("ready"):
        system_health = "degraded"

    preview_operator_recommendation = "No action required."
    if latest_cycle is not None:
        action = str(latest_cycle.proposed_action or "").upper()
        state = str(latest_cycle.state or "").upper()
        risk_verdict = str(latest_cycle.risk_verdict or "").upper()
        if state == "FAILED":
            preview_operator_recommendation = "Inspect latest cycle failure before proceeding."
        elif action == "HOLD":
            preview_operator_recommendation = "Waiting for next qualifying BUY."
        elif action in {"BUY", "SELL"} and risk_verdict == "REJECTED":
            preview_operator_recommendation = "Inspect Risk rejection."
        elif action in {"BUY", "SELL"}:
            preview_operator_recommendation = "Review latest preview evidence and approval readiness."

    api_status = "responsive"
    database_status = "connected"
    kraken_status = "Unavailable"
    if kraken_production is not None:
        readiness = kraken_production.last_readiness_verdict or "Unknown"
        kraken_status = f"{kraken_production.status} ({readiness})"

    worker_status = "Unavailable"
    if worker_heartbeat is not None:
        heartbeat_value = worker_heartbeat if worker_heartbeat.tzinfo is not None else worker_heartbeat.replace(tzinfo=timezone.utc)
        age_minutes = int(max(0, (now - heartbeat_value).total_seconds() // 60))
        worker_status = f"heartbeat {age_minutes}m ago"

    git_sha = _resolve_git_sha()

    return {
        "environment": settings.environment,
        "git_sha": git_sha,
        "api_status": api_status,
        "database_status": database_status,
        "worker_status": worker_status,
        "worker_heartbeat": worker_heartbeat,
        "kraken_status": kraken_status,
        "system_health": system_health,
        "database_url_configured": bool(settings.database_url),
        "mandate_id": mandate.mandate_id if mandate else None,
        "mandate_status": mandate.status if mandate else None,
        "latest_strategy": latest_strategy,
        "latest_signal": latest_signal,
        "campaign_count": campaign_count,
        "decision_count": decision_count,
        "open_positions": open_positions,
        "open_previews": open_preview_count,
        "open_live_orders": open_live_orders,
        "research_status": "available" if settings.research_evolution_enabled else "disabled",
        "operator_recommendation": preview_operator_recommendation,
        "safety_flags": {
            "live_crypto_order_submission_enabled": settings.live_crypto_order_submission_enabled,
            "live_crypto_dry_run_enabled": settings.live_crypto_dry_run_enabled,
            "live_crypto_max_order_usd": _coerce_decimal(settings.live_crypto_max_order_usd),
            "live_crypto_preparation_enabled": settings.live_crypto_preparation_enabled,
        },
        "latest_cycle": {
            "cycle_id": latest_cycle.cycle_id if latest_cycle else None,
            "state": latest_cycle.state if latest_cycle else None,
            "proposed_action": latest_cycle.proposed_action if latest_cycle else None,
            "risk_verdict": latest_cycle.risk_verdict if latest_cycle else None,
            "failure_reason": latest_cycle.failure_reason if latest_cycle else None,
            "started_at": latest_cycle.started_at if latest_cycle else None,
            "completed_at": latest_cycle.completed_at if latest_cycle else None,
        },
        "latest_preview": {
            "crypto_order_preview_id": latest_preview.crypto_order_preview_id if latest_preview else None,
            "status": latest_preview.status if latest_preview else None,
            "provider": latest_preview.provider if latest_preview else None,
            "product_id": latest_preview.product_id if latest_preview else None,
            "side": latest_preview.side if latest_preview else None,
            "created_at": latest_preview.created_at if latest_preview else None,
            "expires_at": latest_preview.expires_at if latest_preview else None,
        },
        "connection_summary": [
            {
                "exchange_connection_id": item.exchange_connection_id,
                "provider": item.provider,
                "environment": item.environment,
                "status": item.status,
                "credentials_valid": item.credentials_valid,
                "last_readiness_verdict": item.last_readiness_verdict,
                "last_verified_at": item.last_verified_at,
                "last_heartbeat_at": item.last_heartbeat_at,
            }
            for item in connections
        ],
        "candle_summary": candle_summary,
    }


async def fetch_risk_ledger_diagnosis(*, account_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        account = await db.get(PaperAccount, account_id)
        if account is None:
            raise ValueError(f"Paper account {account_id} not found")

        effective_policy = await resolve_effective_risk_policy(db=db, paper_account_id=account.id)
        latest_trade = await db.scalar(
            select(Trade)
            .where(Trade.paper_account_id == account.id)
            .order_by(desc(Trade.executed_at), desc(Trade.id))
            .limit(1)
        )
        trade_count = int(
            (await db.scalar(select(func.count()).select_from(Trade).where(Trade.paper_account_id == account.id))) or 0
        )

        equity_evidence = await resolve_equity_risk_evidence(
            db=db,
            paper_account=account,
            actor="operator_cli:risk_diagnosis",
            max_price_age_seconds=settings.live_crypto_price_max_age_seconds,
        )

        status_payload: dict[str, Any] | None = None
        status_error: dict[str, Any] | None = None
        try:
            risk_status = await risk_monitor.get_risk_status(db=db, account_id=account.id)
            status_payload = {
                "daily_loss": {
                    "used": format(risk_status.daily_loss.used, "f"),
                    "limit": format(risk_status.daily_loss.limit, "f"),
                    "pct_used": format(risk_status.daily_loss.pct_used, "f"),
                },
                "drawdown": {
                    "used": format(risk_status.drawdown.used, "f"),
                    "limit": format(risk_status.drawdown.limit, "f"),
                    "pct_used": format(risk_status.drawdown.pct_used, "f"),
                },
                "daily_loss_input_source": risk_status.daily_loss_input_source,
                "drawdown_input_source": risk_status.drawdown_input_source,
                "current_equity": format(risk_status.current_equity, "f"),
                "current_cash_balance": format(risk_status.current_cash_balance, "f"),
                "current_position_value": format(risk_status.current_position_value, "f"),
                "start_of_day_equity": format(risk_status.start_of_day_equity, "f"),
                "high_water_mark_equity": format(risk_status.high_water_mark_equity, "f"),
                "valuation_source": risk_status.valuation_source,
                "valuation_state": risk_status.valuation_state,
                "daily_loss_baseline_source": risk_status.daily_loss_baseline_source,
                "drawdown_baseline_source": risk_status.drawdown_baseline_source,
                "baseline_state": risk_status.baseline_state,
                "generated_at": risk_status.generated_at,
            }
        except Exception as exc:  # pragma: no cover - defensive payload branch
            status_error = {
                "error": str(exc),
                "equity_evidence_ready": equity_evidence.ready,
                "equity_evidence_fail_closed_reason": equity_evidence.fail_closed_reason,
            }

        snapshot = await build_account_snapshot(
            db=db,
            paper_account_id=account.id,
            starting_balance=account.starting_balance,
        )

    starting_balance = Decimal(account.starting_balance)
    current_cash_balance = Decimal(account.current_cash_balance)
    old_daily_loss_limit = starting_balance * Decimal(effective_policy.max_daily_loss_pct)
    old_drawdown_limit = starting_balance * Decimal(effective_policy.max_drawdown_pct)
    old_daily_loss_used = max(Decimal("0"), starting_balance - current_cash_balance)
    old_drawdown_used = old_daily_loss_used

    authoritative_start_of_day_equity = equity_evidence.baseline.start_of_day_equity
    authoritative_high_water_mark_equity = equity_evidence.baseline.high_water_mark_equity
    authoritative_current_equity = equity_evidence.valuation.current_equity
    authoritative_daily_loss_used = max(Decimal("0"), authoritative_start_of_day_equity - authoritative_current_equity)
    authoritative_drawdown_used = max(Decimal("0"), authoritative_high_water_mark_equity - authoritative_current_equity)
    authoritative_daily_loss_limit = authoritative_start_of_day_equity * Decimal(effective_policy.max_daily_loss_pct)
    authoritative_drawdown_limit = authoritative_high_water_mark_equity * Decimal(effective_policy.max_drawdown_pct)

    old_daily_loss_pct = old_daily_loss_used / old_daily_loss_limit if old_daily_loss_limit > 0 else Decimal("0")
    old_drawdown_pct = old_drawdown_used / old_drawdown_limit if old_drawdown_limit > 0 else Decimal("0")
    authoritative_daily_loss_pct = authoritative_daily_loss_used / authoritative_daily_loss_limit if authoritative_daily_loss_limit > 0 else Decimal("0")
    authoritative_drawdown_pct = authoritative_drawdown_used / authoritative_drawdown_limit if authoritative_drawdown_limit > 0 else Decimal("0")

    latest_trade_executed_at = None if latest_trade is None else latest_trade.executed_at
    balance_source_timestamp = latest_trade_executed_at or account.created_at
    snapshot_gap_cash = snapshot.cash_balance - current_cash_balance
    snapshot_gap_equity = snapshot.equity - current_cash_balance

    return {
        "account": {
            "account_id": str(account.id),
            "created_at": account.created_at,
            "asset_class": account.asset_class,
            "is_active": bool(account.is_active),
        },
        "evaluation": {
            "generated_at": datetime.now(timezone.utc),
            "policy_source": effective_policy.source,
            "status_input_source": None if status_payload is None else status_payload.get("daily_loss_input_source"),
            "latest_trade_executed_at": latest_trade_executed_at,
            "balance_source_timestamp": balance_source_timestamp,
            "trade_count": trade_count,
        },
        "inputs": {
            "starting_balance": {
                "value": format(starting_balance, "f"),
                "source": "paper_accounts.starting_balance",
                "record_created_at": account.created_at,
            },
            "current_cash_balance": {
                "value": format(current_cash_balance, "f"),
                "source": "paper_accounts.current_cash_balance",
                "record_created_at": account.created_at,
                "latest_trade_executed_at": latest_trade_executed_at,
            },
            "max_daily_loss_pct": {
                "value": format(effective_policy.max_daily_loss_pct, "f"),
                "source": effective_policy.source,
            },
            "max_drawdown_pct": {
                "value": format(effective_policy.max_drawdown_pct, "f"),
                "source": effective_policy.source,
            },
        },
        "formulas": {
            "legacy_cash_only.daily_loss.used": "max(0, starting_balance - current_cash_balance)",
            "legacy_cash_only.daily_loss.limit": "starting_balance * max_daily_loss_pct",
            "legacy_cash_only.drawdown.used": "max(0, starting_balance - current_cash_balance)",
            "legacy_cash_only.drawdown.limit": "starting_balance * max_drawdown_pct",
            "authoritative_equity.daily_loss.used": "max(0, start_of_day_equity - current_equity)",
            "authoritative_equity.daily_loss.limit": "start_of_day_equity * max_daily_loss_pct",
            "authoritative_equity.drawdown.used": "max(0, high_water_mark_equity - current_equity)",
            "authoritative_equity.drawdown.limit": "high_water_mark_equity * max_drawdown_pct",
            "pct_used": "used / limit if limit > 0 else 0",
        },
        "status": status_payload,
        "status_error": status_error,
        "equity_evidence": {
            "ready": equity_evidence.ready,
            "fail_closed_reason": equity_evidence.fail_closed_reason,
            "valuation_state": equity_evidence.valuation.valuation_state,
            "valuation_source": equity_evidence.valuation.valuation_source,
            "latest_price_timestamp": equity_evidence.valuation.latest_price_timestamp,
            "stale_cutoff": equity_evidence.valuation.stale_cutoff,
            "missing_price_assets": equity_evidence.valuation.missing_price_assets,
            "stale_price_assets": equity_evidence.valuation.stale_price_assets,
            "price_evidence": equity_evidence.valuation.price_evidence,
            "unresolved_reconciliation_count": equity_evidence.unresolved_reconciliation_count,
            "unknown_provider_order_count": equity_evidence.unknown_provider_order_count,
            "start_of_day_source": equity_evidence.baseline.start_of_day_source,
            "high_water_mark_source": equity_evidence.baseline.high_water_mark_source,
            "baseline_state": equity_evidence.baseline.baseline_state,
        },
        "snapshot": {
            "cash_balance": format(snapshot.cash_balance, "f"),
            "position_value": format(snapshot.position_value, "f"),
            "equity": format(snapshot.equity, "f"),
            "equity_return_usd": format(snapshot.equity_return_usd, "f"),
            "equity_return_pct": format(snapshot.equity_return_pct, "f"),
            "positions": [
                {
                    "asset_id": str(item.asset_id),
                    "symbol": item.symbol,
                    "quantity": format(item.quantity, "f"),
                    "avg_entry_price": format(item.avg_entry_price, "f"),
                    "position_value": format(item.position_value, "f"),
                    "unrealized_pnl_usd": format(item.unrealized_pnl_usd, "f"),
                    "unrealized_pnl_pct": format(item.unrealized_pnl_pct, "f"),
                }
                for item in snapshot.positions
            ],
        },
        "diagnosis": {
            "persisted_cash_balance_minus_computed_cash_balance": format(snapshot_gap_cash, "f"),
            "persisted_cash_balance_minus_snapshot_equity": format(snapshot_gap_equity, "f"),
            "ledger_alignment": "aligned" if snapshot_gap_cash == Decimal("0") else "divergent",
            "legacy_cash_only": {
                "daily_loss": {
                    "used": format(old_daily_loss_used, "f"),
                    "limit": format(old_daily_loss_limit, "f"),
                    "pct_used": format(old_daily_loss_pct, "f"),
                },
                "drawdown": {
                    "used": format(old_drawdown_used, "f"),
                    "limit": format(old_drawdown_limit, "f"),
                    "pct_used": format(old_drawdown_pct, "f"),
                },
            },
            "authoritative_equity_based": {
                "daily_loss": {
                    "used": format(authoritative_daily_loss_used, "f"),
                    "limit": format(authoritative_daily_loss_limit, "f"),
                    "pct_used": format(authoritative_daily_loss_pct, "f"),
                },
                "drawdown": {
                    "used": format(authoritative_drawdown_used, "f"),
                    "limit": format(authoritative_drawdown_limit, "f"),
                    "pct_used": format(authoritative_drawdown_pct, "f"),
                },
                "current_equity": format(authoritative_current_equity, "f"),
                "current_cash_balance": format(equity_evidence.valuation.cash_balance, "f"),
                "current_position_value": format(equity_evidence.valuation.position_value, "f"),
                "start_of_day_equity": format(authoritative_start_of_day_equity, "f"),
                "high_water_mark_equity": format(authoritative_high_water_mark_equity, "f"),
                "valuation_source": equity_evidence.valuation.valuation_source,
                "valuation_state": equity_evidence.valuation.valuation_state,
                "daily_loss_baseline_source": equity_evidence.baseline.start_of_day_source,
                "drawdown_baseline_source": equity_evidence.baseline.high_water_mark_source,
            },
        },
    }


async def fetch_watch_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    return await fetch_operator_status(
        mandate_id=mandate_id,
        candle_symbol=candle_symbol,
        candle_interval=candle_interval,
        candle_exchange=candle_exchange,
        candle_max_age_minutes=candle_max_age_minutes,
    )


async def fetch_strategy_roster_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_run = await db.scalar(
            select(StrategyRosterRun)
            .where(StrategyRosterRun.provider == provider)
            .where(StrategyRosterRun.product_id == product_id)
            .where(StrategyRosterRun.interval == interval)
            .order_by(desc(StrategyRosterRun.candle_close_time), desc(StrategyRosterRun.created_at))
            .limit(1)
        )

        if latest_run is None:
            return {
                "provider": provider,
                "product_id": product_id,
                "interval": interval,
                "roster_run": None,
                "proposals": [],
            }

        proposals = list(
            (
                await db.execute(
                    select(StrategyRosterProposal)
                    .where(StrategyRosterProposal.roster_run_id == latest_run.roster_run_id)
                    .order_by(StrategyRosterProposal.strategy_slug.asc())
                )
            ).scalars().all()
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "roster_run": {
            "roster_run_id": latest_run.roster_run_id,
            "asset_id": latest_run.asset_id,
            "candle_open_time": latest_run.candle_open_time,
            "candle_close_time": latest_run.candle_close_time,
            "trigger": latest_run.trigger,
            "started_at": latest_run.started_at,
            "completed_at": latest_run.completed_at,
            "strategies_requested": list(latest_run.strategies_requested or []),
            "strategies_completed": list(latest_run.strategies_completed or []),
            "strategies_failed": list(latest_run.strategies_failed or []),
            "buy_count": latest_run.buy_count,
            "sell_count": latest_run.sell_count,
            "hold_count": latest_run.hold_count,
            "execution_mode": latest_run.execution_mode,
            "live_submission_allowed": latest_run.live_submission_allowed,
            "scheduled_cycle_id": latest_run.scheduled_cycle_id,
        },
        "proposals": [
            {
                "proposal_id": item.proposal_id,
                "strategy_slug": item.strategy_slug,
                "strategy_version": item.strategy_version,
                "strategy_identity": item.strategy_identity,
                "parameter_set_identity": item.parameter_set_identity,
                "action": item.action,
                "evaluation_status": item.evaluation_status,
                "strength": item.strength,
                "confidence": item.confidence,
                "reason": item.reason,
                "deterministic_explanation": list(item.deterministic_explanation or []),
                "indicator_values": item.indicator_values,
                "market_window_evidence": item.market_window_evidence,
                "evaluated_at": item.evaluated_at,
            }
            for item in proposals
        ],
    }
async def fetch_strategy_scorecards_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_outcome_at = await db.scalar(
            select(StrategyRosterProposalOutcome.evaluated_at)
            .where(StrategyRosterProposalOutcome.provider == provider)
            .where(StrategyRosterProposalOutcome.product_id == product_id)
            .where(StrategyRosterProposalOutcome.interval == interval)
            .order_by(desc(StrategyRosterProposalOutcome.evaluated_at))
            .limit(1)
        )
        scorecards = await fetch_strategy_scorecards(
            db=db,
            provider=provider,
            product_id=product_id,
            interval=interval,
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "latest_outcome_evaluated_at": latest_outcome_at,
        "scorecards": [
            {
                "strategy_slug": item.strategy_slug,
                "per_horizon": [
                    {
                        "horizon": bucket.horizon_label,
                        "total_evaluated": bucket.total_evaluated,
                        "buy_evaluations": bucket.buy_evaluations,
                        "buy_correct": bucket.buy_correct,
                        "sell_evaluations": bucket.sell_evaluations,
                        "sell_correct": bucket.sell_correct,
                        "hold_evaluations": bucket.hold_evaluations,
                        "hold_correct": bucket.hold_correct,
                        "overall_correct_pct": bucket.overall_correct_pct,
                        "average_raw_return_pct": bucket.average_raw_return_pct,
                        "average_fee_adjusted_return_pct": bucket.average_fee_adjusted_return_pct,
                        "average_mfe_pct": bucket.average_mfe_pct,
                        "average_mae_pct": bucket.average_mae_pct,
                    }
                    for bucket in item.per_horizon
                ],
                "aggregate": {
                    "horizon": item.aggregate.horizon_label,
                    "total_evaluated": item.aggregate.total_evaluated,
                    "buy_evaluations": item.aggregate.buy_evaluations,
                    "buy_correct": item.aggregate.buy_correct,
                    "sell_evaluations": item.aggregate.sell_evaluations,
                    "sell_correct": item.aggregate.sell_correct,
                    "hold_evaluations": item.aggregate.hold_evaluations,
                    "hold_correct": item.aggregate.hold_correct,
                    "overall_correct_pct": item.aggregate.overall_correct_pct,
                    "average_raw_return_pct": item.aggregate.average_raw_return_pct,
                    "average_fee_adjusted_return_pct": item.aggregate.average_fee_adjusted_return_pct,
                    "average_mfe_pct": item.aggregate.average_mfe_pct,
                    "average_mae_pct": item.aggregate.average_mae_pct,
                },
                "best_regime": item.best_regime,
                "worst_regime": item.worst_regime,
                "regime_evidence_count": item.regime_evidence_count,
                "regime_min_evidence_required": item.regime_min_evidence_required,
            }
            for item in scorecards
        ],
    }


def _serialize_commissioning_run(run: VenueCommissioningRun) -> dict[str, Any]:
    return {
        "commissioning_run_id": run.commissioning_run_id,
        "status": run.status,
        "execution_purpose": run.execution_purpose,
        "commissioning_type": run.commissioning_type,
        "provider": run.provider,
        "environment": run.environment,
        "product_id": run.product_id,
        "max_quote_notional": run.max_quote_notional,
        "max_buys": run.max_buys,
        "max_sells": run.max_sells,
        "hold_minutes": run.hold_minutes,
        "buy_requested_quote_usd": run.buy_requested_quote_usd,
        "buy_client_order_id": run.buy_client_order_id,
        "buy_provider_order_id": run.buy_provider_order_id,
        "buy_submitted_at": run.buy_submitted_at,
        "buy_filled_at": run.buy_filled_at,
        "buy_filled_quote_usd": run.buy_filled_quote_usd,
        "buy_filled_base_btc": run.buy_filled_base_btc,
        "buy_avg_price_usd": run.buy_avg_price_usd,
        "buy_fee_usd": run.buy_fee_usd,
        "hold_started_at": run.hold_started_at,
        "hold_due_at": run.hold_due_at,
        "sell_client_order_id": run.sell_client_order_id,
        "sell_provider_order_id": run.sell_provider_order_id,
        "sell_submitted_at": run.sell_submitted_at,
        "sell_filled_at": run.sell_filled_at,
        "sell_requested_base_btc": run.sell_requested_base_btc,
        "sell_filled_base_btc": run.sell_filled_base_btc,
        "sell_filled_quote_usd": run.sell_filled_quote_usd,
        "sell_avg_price_usd": run.sell_avg_price_usd,
        "sell_fee_usd": run.sell_fee_usd,
        "gross_pnl_usd": run.gross_pnl_usd,
        "total_fees_usd": run.total_fees_usd,
        "net_realized_pnl_usd": run.net_realized_pnl_usd,
        "dust_base_btc": run.dust_base_btc,
        "duplicate_orders_detected": run.duplicate_orders_detected,
        "manual_intervention_required": run.manual_intervention_required,
        "ledger_matches_kraken": run.ledger_matches_kraken,
        "activated_by": run.activated_by,
        "activated_at": run.activated_at,
        "started_by": run.started_by,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "revoked_by": run.revoked_by,
        "revoked_reason": run.revoked_reason,
        "updated_at": run.updated_at,
    }


async def fetch_venue_commission_readiness(
    *,
    provider: str,
    product_id: str,
    environment: str,
    amount_usd: Decimal,
    hold_minutes: int,
) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service
    from app.services.live.venue_commissioning import CommissioningConfig

    config = CommissioningConfig(
        provider=provider,
        product_id=product_id,
        environment=environment,
        amount=amount_usd,
        hold_minutes=hold_minutes,
    )
    async with AsyncSessionLocal() as db:
        readiness = await venue_commissioning_service["evaluate_readiness"](db=db, config=config)

    return {
        "provider": provider,
        "product_id": product_id,
        "environment": environment,
        "amount_usd": amount_usd,
        "hold_minutes": hold_minutes,
        "would_activate_safely": readiness.would_activate_safely,
        "exact_blocker": readiness.exact_blocker,
        "existing_active_run": readiness.existing_active_run,
        "checks": [
            {"label": item.label, "status": item.status, "reason": item.reason}
            for item in readiness.checks
        ],
    }


async def activate_venue_commission_run(
    *,
    actor: str,
    provider: str,
    product_id: str,
    environment: str,
    amount_usd: Decimal,
    hold_minutes: int,
    confirm: bool,
) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service
    from app.services.live.venue_commissioning import CommissioningConfig

    config = CommissioningConfig(
        provider=provider,
        product_id=product_id,
        environment=environment,
        amount=amount_usd,
        hold_minutes=hold_minutes,
    )
    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["activate_run"](
            db=db,
            actor=actor,
            config=config,
            confirm=confirm,
        )

    return {
        "activation": "accepted",
        "run": _serialize_commissioning_run(run),
    }


async def start_venue_commission_run(*, actor: str, commissioning_run_id: UUID, confirm: bool) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["start_run"](
            db=db,
            actor=actor,
            run_id=commissioning_run_id,
            confirm=confirm,
        )

    return {
        "start": "processed",
        "run": _serialize_commissioning_run(run),
    }


async def fetch_venue_commission_status(*, commissioning_run_id: UUID) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["get_run"](db=db, run_id=commissioning_run_id)

    return {
        "run": _serialize_commissioning_run(run),
    }


async def revoke_venue_commission_run(*, actor: str, commissioning_run_id: UUID, confirm: bool) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["revoke_run"](
            db=db,
            actor=actor,
            run_id=commissioning_run_id,
            confirm=confirm,
        )

    return {
        "revoke": "processed",
        "run": _serialize_commissioning_run(run),
    }


async def inspect_canonical_campaign_binding(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_canonical_campaign_binding(
            db=db,
            request=CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )

    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def bind_canonical_campaign_runtime(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _bind_canonical_campaign_runtime(
            db=db,
            request=CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )

    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_canonical_campaign_binding_status(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    return await inspect_canonical_campaign_binding(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
        actor=actor,
        confirm=confirm,
    )


async def canonical_campaign_status_transition_readiness(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    expected_current_status: str,
    target_status: str,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_canonical_campaign_status_transition(
            db=db,
            request=CanonicalCampaignStatusTransitionRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                expected_current_status=expected_current_status,
                target_status=target_status,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                idempotency_key=None,
                confirm=False,
            ),
        )
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def canonical_campaign_status_transition_execute(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    expected_current_status: str,
    target_status: str,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    idempotency_key: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _transition_canonical_campaign_status(
            db=db,
            request=CanonicalCampaignStatusTransitionRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                expected_current_status=expected_current_status,
                target_status=target_status,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                idempotency_key=idempotency_key,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def canonical_campaign_status_transition_audit(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    expected_current_status: str,
    target_status: str,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    limit: int = 20,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_canonical_campaign_status_transition_audit(
            db=db,
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            runtime_campaign_id=runtime_campaign_id,
            expected_current_status=expected_current_status,
            target_status=target_status,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_trading_profile_id,
            provider=provider,
            environment=environment,
            product_id=product_id,
            actor=actor,
            limit=limit,
        )


async def fetch_canonical_campaign_binding_audit(*, campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_canonical_campaign_binding_audit(db=db, campaign_id=campaign_id, limit=limit)


async def canonical_paper_cash_causality_audit(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_canonical_paper_cash_causality_audit(
            db=db,
            request=CanonicalPaperCashCausalityAuditRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
            ),
        )


async def canonical_campaign_authority_audit(
    *,
    campaign_id: UUID,
    campaign_version: int,
    cycle_id: UUID,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_canonical_campaign_authority_audit(
            db=db,
            request=CanonicalCampaignAuthorityAuditRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                cycle_id=cycle_id,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
            ),
        )


async def create_canonical_preview_package_bundle(
    *,
    campaign_id: UUID,
    campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    max_proposed_order_amount: Decimal,
    commissioning_entry_mode: str | None,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await create_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageCreateRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
                max_proposed_order_amount=max_proposed_order_amount,
                commissioning_entry_mode=commissioning_entry_mode,
                actor=actor,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
    return payload


async def show_canonical_preview_package_bundle(*, package_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await get_canonical_preview_package(db=db, package_id=package_id)


async def canonical_preview_package_readiness(*, package_id: UUID) -> dict[str, Any]:
    payload = await show_canonical_preview_package_bundle(package_id=package_id)
    return {
        "package_id": str(package_id),
        "readiness": payload.get("readiness"),
    }


async def canonical_preview_package_history(
    *,
    campaign_id: UUID,
    campaign_version: int | None,
    limit: int,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await list_canonical_preview_package_history(
            db=db,
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            limit=limit,
        )


async def authorize_canonical_preview_package_bundle(
    *,
    package_id: UUID,
    actor: str,
    approver_role: str,
    rationale: str,
    expires_at: datetime,
    max_order_usd: Decimal,
    max_total_deployed_campaign_capital_usd: Decimal,
    no_leverage: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await authorize_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageAuthorizeRequest(
                package_id=package_id,
                actor=actor,
                approver_role=approver_role,
                rationale=rationale,
                expires_at=expires_at,
                max_order_usd=max_order_usd,
                max_total_deployed_campaign_capital_usd=max_total_deployed_campaign_capital_usd,
                no_leverage=no_leverage,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def dry_run_canonical_preview_package_bundle(
    *,
    package_id: UUID,
    approval_event_id: UUID,
    operator_identity: str,
    idempotency_token: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await run_dry_run_for_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageDryRunRequest(
                package_id=package_id,
                approval_event_id=approval_event_id,
                operator_identity=operator_identity,
                idempotency_token=idempotency_token,
            ),
        )
        await db.commit()
        return payload


async def activate_canonical_proving_campaign_bundle(
    *,
    package_id: UUID,
    approval_event_id: UUID,
    dry_run_live_crypto_order_id: UUID,
    actor: str,
    expires_at: datetime,
    idempotency_key: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise PermissionError("confirmation required for canonical proving activation")

    async with AsyncSessionLocal() as db:
        payload = await activate_canonical_proving_campaign(
            db=db,
            request=CanonicalPreviewPackageActivationRequest(
                package_id=package_id,
                approval_event_id=approval_event_id,
                dry_run_live_crypto_order_id=dry_run_live_crypto_order_id,
                actor=actor,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def canonical_proving_activation_status(*, package_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await get_canonical_proving_activation_status(db=db, package_id=package_id)


async def pause_canonical_proving_activation_bundle(*, package_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await pause_canonical_proving_activation(
            db=db,
            request=CanonicalPreviewPackagePauseRequest(
                package_id=package_id,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def revoke_canonical_proving_activation_bundle(*, package_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await revoke_canonical_proving_activation(
            db=db,
            request=CanonicalPreviewPackageRevokeRequest(
                package_id=package_id,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def canonical_proving_commission_bundle(
    *,
    campaign_id: UUID,
    campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product: str,
    amount_usd: Decimal,
    actor: str,
    approver_role: str,
    rationale: str,
    no_leverage: bool,
    confirm: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    if amount_usd != Decimal("5"):
        raise PermissionError("commissioned proving requires exact amount_usd=5")
    if not no_leverage:
        raise PermissionError("commissioned proving requires no_leverage=true")
    if not confirm:
        raise PermissionError("confirm=true is required for commissioned proving")
    root_idempotency_key = str(idempotency_key or "").strip()
    if not root_idempotency_key:
        raise PermissionError("idempotency_key is required")

    async with AsyncSessionLocal() as db:
        _log_commission_stage(stage="db_session_acquired", status="completed", root_idempotency_key=root_idempotency_key)
        now = datetime.now(timezone.utc)
        definition = await _await_db_operation(
            stage="campaign_definition_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_campaign_definition_by_identity(db=db, campaign_id=campaign_id, version=campaign_version),
        )
        runtime = await _await_db_operation(
            stage="runtime_campaign_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_runtime_campaign_by_identity(db=db, campaign_id=campaign_id),
        )
        paper_account = await _await_db_operation(
            stage="paper_account_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_paper_account_by_id(db=db, paper_account_id=paper_account_id),
        )
        profile = await _await_db_operation(
            stage="profile_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_profile_by_id(db=db, live_trading_profile_id=live_trading_profile_id),
        )
        if definition is None or runtime is None or paper_account is None or profile is None:
            raise LookupError("commissioned proving identity chain incomplete")
        if runtime.id is None:
            raise LookupError("runtime capital campaign id unavailable")
        if getattr(profile, "paper_account_id", None) != paper_account_id:
            raise PermissionError("campaign/profile/account mismatch")

        commissioned_blob = _commissioned_blob_from_definition(definition)
        commissioned_state = str(commissioned_blob.get("state") or "DRAFT")
        entry_execution = commissioned_blob.get("entry_execution") if isinstance(commissioned_blob.get("entry_execution"), dict) else {}
        ownership = commissioned_blob.get("ownership_reconciliation") if isinstance(commissioned_blob.get("ownership_reconciliation"), dict) else {}

        if commissioned_state == "ACTIVE_POSITION" and ownership.get("position_identity"):
            status_payload = await _await_db_operation(
                stage="active_position_status_replay",
                root_idempotency_key=root_idempotency_key,
                operation=canonical_proving_commission_status(
                    campaign_id=campaign_id,
                    campaign_version=campaign_version,
                    paper_account_id=paper_account_id,
                    live_trading_profile_id=live_trading_profile_id,
                    provider=provider,
                    environment=environment,
                    product=product,
                ),
            )
            return {"replayed": True, "status": status_payload, "current_state": "ACTIVE_POSITION", "autonomous_lifecycle_owner": True}

        package: CanonicalPreviewPackage | None = None
        preview: CryptoOrderPreview | None = None
        approval_event: LiveApprovalEvent | None = None
        activation: CanonicalProvingActivation | None = None

        if commissioned_state not in _COMMISSIONED_SUBMISSION_MAY_HAVE_OCCURRED:
            attempted_refresh_scopes: set[str] = set()
            state_transitions = 0
            while True:
                state_transitions += 1
                if state_transitions > _CANONICAL_PROVING_MAX_STATE_TRANSITIONS:
                    raise PermissionError(
                        f"canonical proving commissioning did not converge after {_CANONICAL_PROVING_MAX_STATE_TRANSITIONS} state transitions"
                    )
                package = await _await_db_operation(
                    stage="latest_forced_package_lookup",
                    root_idempotency_key=root_idempotency_key,
                    operation=_load_latest_forced_canonical_package(db=db, campaign_id=campaign_id),
                )
                preview = await _await_db_operation(
                    stage="preview_lookup",
                    root_idempotency_key=root_idempotency_key,
                    operation=_load_preview_for_package_row(db=db, package=package),
                )
                approval_event = await _await_db_operation(
                    stage="latest_approval_lookup",
                    root_idempotency_key=root_idempotency_key,
                    operation=_load_latest_approval_for_package(db=db, package=package),
                )
                activation = await _await_db_operation(
                    stage="activation_lookup",
                    root_idempotency_key=root_idempotency_key,
                    operation=_load_activation_for_package(db=db, package=package),
                )

                if _package_requires_refresh(package=package, preview=preview, approval_event=approval_event, activation=activation, now=now):
                    refresh_scope = "initial" if package is None else str(package.package_id)
                    if refresh_scope in attempted_refresh_scopes:
                        raise PermissionError("unable to obtain a fresh canonical proving package")
                    attempted_refresh_scopes.add(refresh_scope)
                    created = await _await_db_operation(
                        stage="canonical_package_create",
                        root_idempotency_key=root_idempotency_key,
                        operation=create_canonical_preview_package(
                            db=db,
                            request=CanonicalPreviewPackageCreateRequest(
                                campaign_id=campaign_id,
                                campaign_version=campaign_version,
                                paper_account_id=paper_account_id,
                                live_trading_profile_id=live_trading_profile_id,
                                provider=provider,
                                environment=environment,
                                product=product,
                                max_proposed_order_amount=amount_usd,
                                actor=actor,
                                idempotency_key=_commission_phase_idempotency_key(
                                    root_idempotency_key=root_idempotency_key,
                                    phase="canonical_package_create",
                                    scope=refresh_scope,
                                ),
                                commissioning_entry_mode="initial_proving_entry",
                            ),
                        ),
                    )
                    await _await_db_operation(
                        stage="package_state_commit",
                        root_idempotency_key=root_idempotency_key,
                        operation=db.commit(),
                    )
                    package = await _await_db_operation(
                        stage="latest_forced_package_lookup",
                        root_idempotency_key=root_idempotency_key,
                        operation=_load_latest_forced_canonical_package(db=db, campaign_id=campaign_id),
                    )
                    preview = await _await_db_operation(
                    stage="preview_lookup",
                    root_idempotency_key=root_idempotency_key,
                    operation=_load_preview_for_package_row(db=db, package=package),
                )
                    approval_event = await _await_db_operation(
                        stage="latest_approval_lookup",
                        root_idempotency_key=root_idempotency_key,
                        operation=_load_latest_approval_for_package(db=db, package=package),
                    )
                    activation = await _await_db_operation(
                    stage="activation_lookup",
                    root_idempotency_key=root_idempotency_key,
                    operation=_load_activation_for_package(db=db, package=package),
                )
                    if not created.get("package"):
                        raise PermissionError("forced canonical package creation did not yield a package")

                if package is None or preview is None:
                    raise PermissionError("canonical proving package evidence unavailable")

                if package.package_state == "READY":
                    expires_at = package.preview_expires_at - timedelta(seconds=30)
                    if expires_at <= now:
                        package = None
                        continue
                    _log_commission_stage(stage="approval_renewal", status="started", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                    await _await_db_operation(
                        stage="canonical_package_authorize",
                        root_idempotency_key=root_idempotency_key,
                        operation=authorize_canonical_preview_package(
                            db=db,
                            request=CanonicalPreviewPackageAuthorizeRequest(
                                package_id=package.package_id,
                                actor=actor,
                                approver_role=approver_role,
                                rationale=rationale,
                                expires_at=expires_at,
                                max_order_usd=amount_usd,
                                max_total_deployed_campaign_capital_usd=amount_usd,
                                no_leverage=True,
                                idempotency_key=_commission_phase_idempotency_key(
                                    root_idempotency_key=root_idempotency_key,
                                    phase="canonical_package_authorize",
                                    scope=str(package.package_id),
                                ),
                            ),
                        ),
                    )
                    await _await_db_operation(
                        stage="package_state_commit",
                        root_idempotency_key=root_idempotency_key,
                        operation=db.commit(),
                    )
                    approval_event = await _await_db_operation(
                        stage="latest_approval_lookup",
                        root_idempotency_key=root_idempotency_key,
                        operation=_load_latest_approval_for_package(db=db, package=package),
                    )
                    _log_commission_stage(stage="approval_renewal", status="completed", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                    continue

                if package.package_state == "AUTHORIZED":
                    if not _approval_is_active(approval_event, now=now):
                        if package.dry_run_live_crypto_order_id is not None:
                            renewed_approval_expires_at = now + timedelta(minutes=5)
                            await _await_db_operation(
                                stage="canonical_package_authorize",
                                root_idempotency_key=root_idempotency_key,
                                operation=authorize_canonical_preview_package(
                                    db=db,
                                    request=CanonicalPreviewPackageAuthorizeRequest(
                                        package_id=package.package_id,
                                        actor=actor,
                                        approver_role=approver_role,
                                        rationale=rationale,
                                        expires_at=renewed_approval_expires_at,
                                        max_order_usd=amount_usd,
                                        max_total_deployed_campaign_capital_usd=amount_usd,
                                        no_leverage=True,
                                        idempotency_key=_commission_phase_idempotency_key(
                                            root_idempotency_key=root_idempotency_key,
                                            phase="canonical_package_authorize",
                                            scope=f"{package.package_id}:renew:{package.approval_event_id}",
                                        ),
                                    ),
                                ),
                            )
                            await _await_db_operation(
                                stage="package_state_commit",
                                root_idempotency_key=root_idempotency_key,
                                operation=db.commit(),
                            )
                            approval_event = await _await_db_operation(
                                stage="latest_approval_lookup",
                                root_idempotency_key=root_idempotency_key,
                                operation=_load_latest_approval_for_package(db=db, package=package),
                            )
                            continue
                        package = None
                        continue
                    if package.dry_run_live_crypto_order_id is not None:
                        _log_commission_stage(stage="reactivation", status="started", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        activated = await _await_db_operation(
                            stage="canonical_proving_activate",
                            root_idempotency_key=root_idempotency_key,
                            operation=activate_canonical_proving_campaign(
                                db=db,
                                request=CanonicalPreviewPackageActivationRequest(
                                    package_id=package.package_id,
                                    approval_event_id=approval_event.id,
                                    dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
                                    actor=actor,
                                    expires_at=approval_event.expires_at,
                                    idempotency_key=_commission_phase_idempotency_key(
                                        root_idempotency_key=root_idempotency_key,
                                        phase="canonical_proving_activate",
                                        scope=str(package.package_id),
                                    ),
                                ),
                            ),
                        )
                        await _await_db_operation(
                            stage="package_state_commit",
                            root_idempotency_key=root_idempotency_key,
                            operation=db.commit(),
                        )
                        activation_payload = activated.get("activation") if isinstance(activated.get("activation"), dict) else {}
                        activation = await _await_db_operation(
                            stage="activation_lookup",
                            root_idempotency_key=root_idempotency_key,
                            operation=_load_activation_for_package(db=db, package=package),
                        )
                        if not activation_payload:
                            raise PermissionError("canonical proving activation missing after activation")
                        _log_commission_stage(stage="reactivation", status="completed", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        continue
                    await _await_db_operation(
                        stage="canonical_package_dry_run",
                        root_idempotency_key=root_idempotency_key,
                        operation=run_dry_run_for_canonical_preview_package(
                            db=db,
                            request=CanonicalPreviewPackageDryRunRequest(
                                package_id=package.package_id,
                                approval_event_id=approval_event.id,
                                operator_identity=actor,
                                idempotency_token=_commission_phase_idempotency_key(
                                    root_idempotency_key=root_idempotency_key,
                                    phase="canonical_package_dry_run",
                                    scope=str(package.package_id),
                                ),
                            ),
                        ),
                    )
                    await _await_db_operation(
                        stage="package_state_commit",
                        root_idempotency_key=root_idempotency_key,
                        operation=db.commit(),
                    )
                    package = await _await_db_operation(
                        stage="package_reload_after_dry_run",
                        root_idempotency_key=root_idempotency_key,
                        operation=db.scalar(
                            select(CanonicalPreviewPackage)
                            .where(CanonicalPreviewPackage.package_id == package.package_id)
                            .limit(1)
                        ),
                    )
                    continue

                if package.package_state == "DRY_RUN_PASSED":
                    if package.dry_run_live_crypto_order_id is None:
                        raise PermissionError("canonical proving dry-run evidence missing")
                    if not _approval_is_active(approval_event, now=now):
                        renewed_approval_expires_at = now + timedelta(minutes=5)
                        _log_commission_stage(stage="approval_renewal", status="started", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        await _await_db_operation(
                            stage="canonical_package_authorize",
                            root_idempotency_key=root_idempotency_key,
                            operation=authorize_canonical_preview_package(
                                db=db,
                                request=CanonicalPreviewPackageAuthorizeRequest(
                                    package_id=package.package_id,
                                    actor=actor,
                                    approver_role=approver_role,
                                    rationale=rationale,
                                    expires_at=renewed_approval_expires_at,
                                    max_order_usd=amount_usd,
                                    max_total_deployed_campaign_capital_usd=amount_usd,
                                    no_leverage=True,
                                    idempotency_key=_commission_phase_idempotency_key(
                                        root_idempotency_key=root_idempotency_key,
                                        phase="canonical_package_authorize",
                                        scope=f"{package.package_id}:renew:{package.approval_event_id}",
                                    ),
                                ),
                            ),
                        )
                        await _await_db_operation(
                            stage="package_state_commit",
                            root_idempotency_key=root_idempotency_key,
                            operation=db.commit(),
                        )
                        approval_event = await _await_db_operation(
                            stage="latest_approval_lookup",
                            root_idempotency_key=root_idempotency_key,
                            operation=_load_latest_approval_for_package(db=db, package=package),
                        )
                        _log_commission_stage(stage="approval_renewal", status="completed", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        continue
                    _log_commission_stage(stage="reactivation", status="started", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                    activated = await _await_db_operation(
                        stage="canonical_proving_activate",
                        root_idempotency_key=root_idempotency_key,
                        operation=activate_canonical_proving_campaign(
                            db=db,
                            request=CanonicalPreviewPackageActivationRequest(
                                package_id=package.package_id,
                                approval_event_id=approval_event.id,
                                dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
                                actor=actor,
                                expires_at=approval_event.expires_at,
                                idempotency_key=_commission_phase_idempotency_key(
                                    root_idempotency_key=root_idempotency_key,
                                    phase="canonical_proving_activate",
                                    scope=str(package.package_id),
                                ),
                            ),
                        ),
                    )
                    await _await_db_operation(
                        stage="package_state_commit",
                        root_idempotency_key=root_idempotency_key,
                        operation=db.commit(),
                    )
                    activation_payload = activated.get("activation") if isinstance(activated.get("activation"), dict) else {}
                    activation = await _await_db_operation(
                        stage="activation_lookup",
                        root_idempotency_key=root_idempotency_key,
                        operation=_load_activation_for_package(db=db, package=package),
                    )
                    if not activation_payload:
                        raise PermissionError("canonical proving activation missing after activation")
                    _log_commission_stage(stage="reactivation", status="completed", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                    continue

                if package.package_state == "ACTIVATED":
                    if not _activation_is_active(activation, now=now):
                        if package.dry_run_live_crypto_order_id is None:
                            raise PermissionError("activated proving package missing dry-run evidence; manual recovery required")
                        renewed_approval_expires_at = now + timedelta(minutes=5)
                        _log_commission_stage(stage="approval_renewal", status="started", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        await _await_db_operation(
                            stage="canonical_package_authorize",
                            root_idempotency_key=root_idempotency_key,
                            operation=authorize_canonical_preview_package(
                                db=db,
                                request=CanonicalPreviewPackageAuthorizeRequest(
                                    package_id=package.package_id,
                                    actor=actor,
                                    approver_role=approver_role,
                                    rationale=rationale,
                                    expires_at=renewed_approval_expires_at,
                                    max_order_usd=amount_usd,
                                    max_total_deployed_campaign_capital_usd=amount_usd,
                                    no_leverage=True,
                                    idempotency_key=_commission_phase_idempotency_key(
                                        root_idempotency_key=root_idempotency_key,
                                        phase="canonical_package_authorize",
                                        scope=f"{package.package_id}:renew:{package.approval_event_id}",
                                    ),
                                ),
                            ),
                        )
                        await _await_db_operation(
                            stage="package_state_commit",
                            root_idempotency_key=root_idempotency_key,
                            operation=db.commit(),
                        )
                        approval_event = await _await_db_operation(
                            stage="latest_approval_lookup",
                            root_idempotency_key=root_idempotency_key,
                            operation=_load_latest_approval_for_package(db=db, package=package),
                        )
                        if approval_event is None:
                            raise PermissionError("renewed approval missing for activated proving package")
                        _log_commission_stage(stage="approval_renewal", status="completed", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        _log_commission_stage(stage="reactivation", status="started", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        activated = await _await_db_operation(
                            stage="canonical_proving_activate",
                            root_idempotency_key=root_idempotency_key,
                            operation=activate_canonical_proving_campaign(
                                db=db,
                                request=CanonicalPreviewPackageActivationRequest(
                                    package_id=package.package_id,
                                    approval_event_id=approval_event.id,
                                    dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
                                    actor=actor,
                                    expires_at=approval_event.expires_at,
                                    idempotency_key=_commission_phase_idempotency_key(
                                        root_idempotency_key=root_idempotency_key,
                                        phase="canonical_proving_activate",
                                        scope=str(package.package_id),
                                    ),
                                ),
                            ),
                        )
                        await _await_db_operation(
                            stage="package_state_commit",
                            root_idempotency_key=root_idempotency_key,
                            operation=db.commit(),
                        )
                        activation_payload = activated.get("activation") if isinstance(activated.get("activation"), dict) else {}
                        activation = await _await_db_operation(
                            stage="activation_lookup",
                            root_idempotency_key=root_idempotency_key,
                            operation=_load_activation_for_package(db=db, package=package),
                        )
                        if not activation_payload:
                            raise PermissionError("canonical proving activation missing after renewal")
                        _log_commission_stage(stage="reactivation", status="completed", root_idempotency_key=root_idempotency_key, package_id=package.package_id)
                        continue
                    break

                raise PermissionError(f"unsupported canonical proving package state: {package.package_state}")

        package = package or await _await_db_operation(
            stage="latest_forced_package_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_latest_forced_canonical_package(db=db, campaign_id=campaign_id),
        )
        preview = preview or await _await_db_operation(
            stage="preview_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_preview_for_package_row(db=db, package=package),
        )
        approval_event = approval_event or await _await_db_operation(
            stage="latest_approval_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_latest_approval_for_package(db=db, package=package),
        )
        activation = activation or await _await_db_operation(
            stage="activation_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_activation_for_package(db=db, package=package),
        )
        if package is None or preview is None or approval_event is None:
            raise PermissionError("canonical proving chain is incomplete")

        _log_commission_stage(stage="mandate_and_readiness_resolution", status="started", root_idempotency_key=root_idempotency_key)
        connection, connection_error = await _await_db_operation(
            stage="exchange_connection_resolution",
            root_idempotency_key=root_idempotency_key,
            operation=_resolve_exchange_connection_for_commissioning(
                db=db,
                preview_exchange_connection_id=preview.exchange_connection_id,
                provider=provider,
                environment=environment,
            ),
        )
        if connection is None:
            raise PermissionError(connection_error or "exchange connection missing")
        mandate = await _await_db_operation(
            stage="mandate_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_active_mandate_for_commissioning(
                db=db,
                runtime_campaign_id=runtime.id,
                live_trading_profile_id=live_trading_profile_id,
                paper_account_id=paper_account_id,
                provider=provider,
                environment=environment,
            ),
        )
        if mandate is None:
            mandate_error = await _await_db_operation(
                stage="mandate_resolution_diagnosis",
                root_idempotency_key=root_idempotency_key,
                operation=_diagnose_mandate_resolution_failure(
                    db=db,
                    runtime_campaign_id=runtime.id,
                    live_trading_profile_id=live_trading_profile_id,
                    paper_account_id=paper_account_id,
                    provider=provider,
                    environment=environment,
                ),
            )
            raise PermissionError(mandate_error)
        if str(getattr(mandate, "provider", "")).strip().lower() != provider.strip().lower() or str(getattr(mandate, "exchange_environment", "")).strip().lower() != environment.strip().lower():
            raise PermissionError("provider/environment mismatch")
        if getattr(mandate, "capital_campaign_id", None) not in {None, runtime.id} or getattr(mandate, "paper_account_id", None) not in {None, paper_account_id}:
            raise PermissionError("campaign/profile/account mismatch")

        mandate_version = await _await_db_operation(
            stage="mandate_version_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_authorized_mandate_version(db=db, mandate_id=mandate.mandate_id),
        )
        if mandate_version is None:
            raise PermissionError("mandate version missing")
        if getattr(mandate_version, "mandate_id", None) != mandate.mandate_id:
            raise PermissionError("mandate identity mismatch")
        if not bool(getattr(mandate_version, "is_authorized", False)):
            raise PermissionError("mandate not authorized or inactive")
        if not bool(getattr(mandate_version, "is_active", False)):
            raise PermissionError("mandate not authorized or inactive")

        asset = await _await_db_operation(
            stage="asset_lookup",
            root_idempotency_key=root_idempotency_key,
            operation=_load_asset_for_product_symbol(db=db, product=product, provider=provider),
        )
        if asset is None:
            raise PermissionError("commissioned proving asset evidence missing")

        readiness_request = _build_commissioned_readiness_request(
            campaign_id=campaign_id,
            version=campaign_version,
            live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id,
            provider=provider,
            environment=environment,
            product=product,
            requested_quote_amount=amount_usd,
            idempotency_key=_commission_phase_idempotency_key(
                root_idempotency_key=root_idempotency_key,
                phase="commissioned_readiness",
                scope=str(package.package_id),
            ),
            approval_event=approval_event,
            definition=definition,
            connection=connection,
            preview=preview,
            mandate=mandate,
            mandate_version=mandate_version,
        )

        _log_commission_stage(stage="mandate_and_readiness_resolution", status="completed", root_idempotency_key=root_idempotency_key)
        preview_response = await _await_db_operation(
            stage="commissioned_readiness_preview",
            root_idempotency_key=root_idempotency_key,
            operation=generate_commissioned_campaign_preview(db=db, request=readiness_request),
        )
        if commissioned_state in {"DRAFT", "READY"}:
            await _await_db_operation(
                stage="commissioned_ready_backfill",
                root_idempotency_key=root_idempotency_key,
                operation=backfill_commissioned_ready_metadata(
                    db=db,
                    campaign_id=campaign_id,
                    version=campaign_version,
                    actor=actor,
                    idempotency_key=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="commissioned_ready_backfill",
                        scope=str(package.package_id),
                    ),
                    commissioning_identity=_commissioning_identity(
                        campaign_id=campaign_id,
                        version=campaign_version,
                        root_idempotency_key=root_idempotency_key,
                    ),
                    commissioned_by=actor,
                    provider=provider,
                    environment=environment,
                    instrument=product,
                    paper_account_id=paper_account_id,
                    capital_budget=amount_usd,
                    maximum_position_size=amount_usd,
                    maximum_total_exposure=amount_usd,
                    commissioned_until=activation.expires_at if activation is not None else approval_event.expires_at,
                ),
            )
            commissioned_state = "READY"

        if commissioned_state == "READY":
            await _await_db_operation(
                stage="commissioned_campaign_commission",
                root_idempotency_key=root_idempotency_key,
                operation=commission_commissioned_campaign(
                    db=db,
                    request=CommissionedCampaignCommissionRequest(
                        campaign_id=campaign_id,
                        version=campaign_version,
                        actor=actor,
                        commissioning_reason=rationale,
                        preview_identity_hash=preview_response.preview_identity_hash,
                        requested_quote_amount=amount_usd,
                        idempotency_key=_commission_phase_idempotency_key(
                            root_idempotency_key=root_idempotency_key,
                            phase="commissioned_campaign_commission",
                            scope=str(package.package_id),
                        ),
                        authorization_expires_at=approval_event.expires_at,
                        commissioned_until=activation.expires_at if activation is not None else approval_event.expires_at,
                        readiness_request=readiness_request,
                    ),
                ),
            )
            commissioned_state = "COMMISSIONED"

        current_definition = await _await_db_operation(
            stage="current_definition_reload_pre_submission",
            root_idempotency_key=root_idempotency_key,
            operation=_load_campaign_definition_by_identity(db=db, campaign_id=campaign_id, version=campaign_version),
        )
        current_blob = _commissioned_blob_from_definition(current_definition)
        current_state = str(current_blob.get("state") or commissioned_state)
        current_entry_execution = current_blob.get("entry_execution") if isinstance(current_blob.get("entry_execution"), dict) else {}
        live_crypto_order_id = _uuid_from_value(current_entry_execution.get("live_crypto_order_id"))
        confirmation_challenge_id: UUID | None = None

        if current_state == "BUY_PENDING" and live_crypto_order_id is not None:
            _log_commission_stage(stage="provider_submission_boundary", status="started", root_idempotency_key=root_idempotency_key)
            live_order = await _await_db_operation(
                stage="buy_pending_order_reload",
                root_idempotency_key=root_idempotency_key,
                operation=db.scalar(
                    select(LiveCryptoOrder)
                    .where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id)
                    .limit(1)
                ),
            )
            if live_order is None or live_order.operator_confirmation_id is None:
                raise PermissionError("BUY_PENDING resume requires persisted live order confirmation identity")
            policy = await resolve_effective_risk_policy(db=db, paper_account_id=paper_account_id)
            execution_response = await execute_commissioned_entry(
                db=db,
                package_id=package.package_id,
                request=CommissionedEntryExecutionRequest(
                    campaign_id=campaign_id,
                    version=campaign_version,
                    actor=actor,
                    idempotency_key=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="commissioned_entry_execute",
                        scope=str(package.package_id),
                    ),
                    readiness_request=readiness_request,
                    expected_preview_identity_hash=preview_response.preview_identity_hash,
                    live_crypto_order_id=live_crypto_order_id,
                    confirmation_challenge_id=live_order.operator_confirmation_id,
                    confirmation_phrase="BUY BTC",
                    submit_idempotency_token=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="live_submit",
                        scope=str(live_crypto_order_id),
                    ),
                    risk_signal_id=package.crypto_order_preview_id,
                    paper_account_id=paper_account_id,
                    asset_id=asset.id,
                    requested_base_quantity=preview_response.estimated_base_quantity,
                    reference_price=preview_response.reference_price,
                    account_equity=paper_account.current_cash_balance,
                    max_position_size_pct=policy.max_position_size_pct,
                    min_order_notional=asset.min_order_notional,
                    qty_step_size=asset.qty_step_size,
                    supports_fractional=asset.supports_fractional,
                ),
            )
            current_state = execution_response.current_state
            _log_commission_stage(stage="provider_submission_boundary", status="completed", root_idempotency_key=root_idempotency_key)
        elif current_state not in _COMMISSIONED_RECONCILIATION_STATES:
            _log_commission_stage(stage="live_order_preparation", status="started", root_idempotency_key=root_idempotency_key)
            prepare_response = await LiveCryptoOrderService().prepare_confirmation(
                db=db,
                request=LiveCryptoOrderPrepareRequest(
                    live_trading_profile_id=live_trading_profile_id,
                    crypto_order_preview_id=package.crypto_order_preview_id,
                    operator_identity=actor,
                    idempotency_token=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="live_prepare_confirmation",
                        scope=str(package.package_id),
                    ),
                ),
            )
            _log_commission_stage(stage="live_order_preparation", status="completed", root_idempotency_key=root_idempotency_key)
            live_crypto_order_id = prepare_response.live_crypto_order.live_crypto_order_id
            confirmation_challenge_id = prepare_response.confirmation_challenge_id
            policy = await resolve_effective_risk_policy(db=db, paper_account_id=paper_account_id)
            _log_commission_stage(stage="provider_submission_boundary", status="started", root_idempotency_key=root_idempotency_key)
            execution_response = await execute_commissioned_entry(
                db=db,
                package_id=package.package_id,
                request=CommissionedEntryExecutionRequest(
                    campaign_id=campaign_id,
                    version=campaign_version,
                    actor=actor,
                    idempotency_key=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="commissioned_entry_execute",
                        scope=str(package.package_id),
                    ),
                    readiness_request=readiness_request,
                    expected_preview_identity_hash=preview_response.preview_identity_hash,
                    live_crypto_order_id=live_crypto_order_id,
                    confirmation_challenge_id=confirmation_challenge_id,
                    confirmation_phrase=prepare_response.confirmation_phrase_required,
                    submit_idempotency_token=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="live_submit",
                        scope=str(live_crypto_order_id),
                    ),
                    risk_signal_id=package.crypto_order_preview_id,
                    paper_account_id=paper_account_id,
                    asset_id=asset.id,
                    requested_base_quantity=preview_response.estimated_base_quantity,
                    reference_price=preview_response.reference_price,
                    account_equity=paper_account.current_cash_balance,
                    max_position_size_pct=policy.max_position_size_pct,
                    min_order_notional=asset.min_order_notional,
                    qty_step_size=asset.qty_step_size,
                    supports_fractional=asset.supports_fractional,
                ),
            )
            current_state = execution_response.current_state
            live_crypto_order_id = execution_response.live_crypto_order_id or live_crypto_order_id
            _log_commission_stage(stage="provider_submission_boundary", status="completed", root_idempotency_key=root_idempotency_key)

        reconcile_payload = None
        current_definition = await _load_campaign_definition_by_identity(db=db, campaign_id=campaign_id, version=campaign_version)
        current_blob = _commissioned_blob_from_definition(current_definition)
        current_state = str(current_blob.get("state") or current_state)
        current_entry_execution = current_blob.get("entry_execution") if isinstance(current_blob.get("entry_execution"), dict) else {}
        live_crypto_order_id = live_crypto_order_id or _uuid_from_value(current_entry_execution.get("live_crypto_order_id"))
        if live_crypto_order_id is not None and current_state in {"BUY_RECONCILIATION_PENDING", "RECONCILIATION_REQUIRED"}:
            reconcile_payload = await reconcile_commissioned_buy_ownership(
                db=db,
                request=CommissionedOwnershipReconciliationRequest(
                    campaign_id=campaign_id,
                    version=campaign_version,
                    actor=actor,
                    idempotency_key=_commission_phase_idempotency_key(
                        root_idempotency_key=root_idempotency_key,
                        phase="commissioned_buy_reconcile",
                        scope=str(live_crypto_order_id),
                    ),
                    live_crypto_order_id=live_crypto_order_id,
                ),
            )
            current_state = reconcile_payload.current_state

        _log_commission_stage(stage="finalizing_commission_result", status="started", root_idempotency_key=root_idempotency_key)
        status_payload = await canonical_proving_commission_status(
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            paper_account_id=paper_account_id,
            live_trading_profile_id=live_trading_profile_id,
            provider=provider,
            environment=environment,
            product=product,
        )
        _log_commission_stage(stage="finalizing_commission_result", status="completed", root_idempotency_key=root_idempotency_key, current_state=current_state)
        return {
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "package_id": None if package is None else str(package.package_id),
            "approval_event_id": None if approval_event is None else str(approval_event.id),
            "activation_id": None if activation is None else str(activation.activation_id),
            "live_crypto_order_id": None if live_crypto_order_id is None else str(live_crypto_order_id),
            "current_state": current_state,
            "autonomous_lifecycle_owner": bool((status_payload.get("commissioning_status") or {}).get("autonomous_lifecycle_owner", False)),
            "status": status_payload,
            "reconciliation": None if reconcile_payload is None else reconcile_payload.model_dump(mode="json"),
        }


async def canonical_proving_commission_status(
    *,
    campaign_id: UUID,
    campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        definition = await _load_campaign_definition_by_identity(db=db, campaign_id=campaign_id, version=campaign_version)
        package = await _await_db_operation(
            stage="latest_forced_package_lookup",
            root_idempotency_key=f"status:{campaign_id}",
            operation=_load_latest_forced_canonical_package(db=db, campaign_id=campaign_id),
        )
        approval_event = await _load_latest_approval_for_package(db=db, package=package)
        activation = await _await_db_operation(
            stage="activation_lookup",
            root_idempotency_key=f"status:{campaign_id}",
            operation=_load_activation_for_package(db=db, package=package),
        )
        preview = await _await_db_operation(
            stage="preview_lookup",
            root_idempotency_key=f"status:{campaign_id}",
            operation=_load_preview_for_package_row(db=db, package=package),
        )
        commissioned_status = await _get_commissioned_control_plane_status(db=db, campaign_id=campaign_id, version=campaign_version)
        commissioned_blob = _commissioned_blob_from_definition(definition)
        chain_summary = _commissioning_status_summary(blob=commissioned_blob)
        live_crypto_order_id = _uuid_from_value((chain_summary.get("entry_execution") or {}).get("live_crypto_order_id"))
        live_order = None
        if live_crypto_order_id is not None:
            live_order = await db.scalar(
                select(LiveCryptoOrder)
                .where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id)
                .limit(1)
            )
        payload = {
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "paper_account_id": str(paper_account_id),
            "live_trading_profile_id": str(live_trading_profile_id),
            "provider": provider,
            "environment": environment,
            "product": product,
            "package": None if package is None else (await get_canonical_preview_package(db=db, package_id=package.package_id)).get("package"),
            "approval_event_id": None if approval_event is None else str(approval_event.id),
            "activation": None if activation is None else {
                "activation_id": str(activation.activation_id),
                "activation_state": activation.activation_state,
                "expires_at": activation.expires_at.isoformat(),
            },
            "preview": None if preview is None else {
                "crypto_order_preview_id": str(preview.crypto_order_preview_id),
                "expires_at": preview.expires_at.isoformat(),
                "requested_amount": _serialize_decimal_str(Decimal(str(preview.requested_amount))),
            },
            "commissioned_control_plane": commissioned_status,
            "commissioning_status": chain_summary,
            "live_order": None if live_order is None else {
                "live_crypto_order_id": str(live_order.live_crypto_order_id),
                "status": live_order.status,
                "provider_order_id": live_order.provider_order_id,
                "submitted_at": None if live_order.submitted_at is None else live_order.submitted_at.isoformat(),
                "filled_at": None if live_order.filled_at is None else live_order.filled_at.isoformat(),
            },
            "read_only": True,
            "no_execution": True,
        }
        return _to_json_compatible(payload)


async def automatic_mandate_activation_readiness(*, provider: str, environment: str, product: str) -> dict[str, Any]:
    from app.services.orchestration.automatic_package_inspection import inspect_automatic_mandate_activation_readiness

    try:
        async with AsyncSessionLocal() as db:
            return await inspect_automatic_mandate_activation_readiness(
                db=db, provider=provider, environment=environment, product=product,
            )
    except Exception as exc:
        return {
            "verdict": "FAILED_CLOSED",
            "reason_codes": [{"code": "readiness_inspection_failed", "action": "Inspect operator and database logs; do not enable automatic activation."}],
            "error_type": type(exc).__name__,
            "read_only": True,
        }


async def _gather_autonomous_supervisor_evidence(
    *, provider: str, environment: str, product: str, since: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    from app.services.orchestration.automatic_package_inspection import inspect_automatic_mandate_activation_readiness

    now = datetime.now(timezone.utc)
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        cycle = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.cycle_kind == "campaign")
            .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id)).limit(1)
        )
        package_scope = [
            CanonicalPreviewPackage.provider == provider,
            CanonicalPreviewPackage.environment == environment,
            CanonicalPreviewPackage.product == product,
        ]
        if cycle is not None and cycle.capital_campaign_id is not None:
            package_scope.extend([
                CanonicalPreviewPackage.campaign_id == cycle.capital_campaign_id,
                CanonicalPreviewPackage.campaign_version == cycle.capital_campaign_version,
            ])
        historical_package = await db.scalar(
            select(CanonicalPreviewPackage)
            .where(*package_scope)
            .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id)).limit(1)
        )
        package = await db.scalar(
            select(CanonicalPreviewPackage)
            .where(
                *package_scope,
                CanonicalPreviewPackage.package_state.in_(["READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"]),
                CanonicalPreviewPackage.preview_expires_at > now,
            )
            .order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id)).limit(1)
        )
        activation = None if package is None else await db.scalar(
            select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package.package_id).limit(1)
        )
        order = None if package is None else await db.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.crypto_order_preview_id == package.crypto_order_preview_id).limit(1)
        )
        reconciliation = None if order is None else await db.scalar(
            select(LiveReconciliationEvent).where(LiveReconciliationEvent.live_crypto_order_id == order.live_crypto_order_id)
            .order_by(desc(LiveReconciliationEvent.recorded_at), desc(LiveReconciliationEvent.id)).limit(1)
        )
        asset = await db.scalar(select(Asset).where(Asset.symbol == _product_symbol(product), Asset.exchange == provider).limit(1))
        trades: list[Trade] = []
        if asset is not None and package is not None:
            trades = list((await db.scalars(
                select(Trade).where(Trade.asset_id == asset.id, Trade.paper_account_id == package.paper_account_id)
                .order_by(Trade.executed_at.asc(), Trade.id.asc())
            )).all())
        quantity = Decimal("0")
        for trade in trades:
            quantity += Decimal(str(trade.quantity)) if trade.side == "buy" else -Decimal(str(trade.quantity))
        position_open = quantity > 0
        latest_position = next((item for item in reversed(trades) if item.side == "buy"), None) if position_open else None
        campaign_packages = [] if package is None else list((await db.scalars(
            select(CanonicalPreviewPackage).where(
                CanonicalPreviewPackage.campaign_id == package.campaign_id,
                CanonicalPreviewPackage.campaign_version == package.campaign_version,
                CanonicalPreviewPackage.provider == provider,
                CanonicalPreviewPackage.environment == environment,
                CanonicalPreviewPackage.product == product,
            )
        )).all())
        preview_ids = [item.crypto_order_preview_id for item in campaign_packages]
        reconciliations = [] if package is None else list((await db.scalars(
            select(LiveReconciliationEvent)
            .where(LiveReconciliationEvent.provider_name == provider, LiveReconciliationEvent.live_trading_profile_id == package.live_trading_profile_id)
            .order_by(LiveReconciliationEvent.recorded_at.asc())
        )).all())
        reconciled_ids = {str(item.live_crypto_order_id) for item in reconciliations if item.reconciliation_status == "filled"}
        orders = [] if not preview_ids else list((await db.scalars(
            select(LiveCryptoOrder)
            .where(LiveCryptoOrder.provider == provider, LiveCryptoOrder.environment == environment, LiveCryptoOrder.product_id == product)
            .where(LiveCryptoOrder.crypto_order_preview_id.in_(preview_ids))
            .order_by(LiveCryptoOrder.created_at.asc())
        )).all())
        buy_orders = [item for item in orders if str(item.side).upper() == "BUY" and item.submitted_at is not None]
        sell_orders = [item for item in orders if str(item.side).upper() == "SELL" and item.submitted_at is not None]
        readiness = await inspect_automatic_mandate_activation_readiness(db=db, provider=provider, environment=environment, product=product)
        runtime = None if package is None else await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == package.runtime_campaign_id).limit(1))
        evidence = {
            "now": now, "environment": environment, "provider": provider, "product": product,
            "cycle": cycle, "package": package, "historical_package": historical_package,
            "activation": activation, "order": order,
            "position": latest_position, "position_open": position_open,
            "position_updated_at": None if latest_position is None else latest_position.executed_at,
            "reconciliation": reconciliation, "readiness": readiness,
            "buy_reconciled": any(str(item.live_crypto_order_id) in reconciled_ids for item in buy_orders),
            "sell_reconciled": any(str(item.live_crypto_order_id) in reconciled_ids for item in sell_orders),
            "autonomous_buy_provenance": any(item.decision_record_id is not None for item in buy_orders),
            "autonomous_sell_provenance": any(item.decision_record_id is not None for item in sell_orders),
            "net_profit": None if runtime is None else runtime.realized_profit,
            "automatic_activation_enabled": settings.automatic_mandate_package_activation_enabled,
            "live_submission_enabled": settings.live_crypto_order_submission_enabled,
            "provider_available": readiness.get("verdict") != "FAILED_CLOSED",
        }
        counts: dict[str, int] = {}
        if since is not None:
            counts["completed_cycles"] = int(await db.scalar(select(func.count()).select_from(AutonomousCycleRun).where(AutonomousCycleRun.completed_at >= since)) or 0)
            counts["healthy_hold_cycles"] = int(await db.scalar(select(func.count()).select_from(AutonomousCycleRun).where(AutonomousCycleRun.completed_at >= since, AutonomousCycleRun.proposed_action == "HOLD", AutonomousCycleRun.failure_reason.is_(None))) or 0)
            for side in ("BUY", "SELL"):
                counts[f"{side.lower()}_proposals"] = int(await db.scalar(select(func.count()).select_from(StrategyRosterProposal).where(StrategyRosterProposal.created_at >= since, StrategyRosterProposal.provider == provider, StrategyRosterProposal.product_id == product, StrategyRosterProposal.action == side)) or 0)
            counts["packages_created"] = int(await db.scalar(select(func.count()).select_from(CanonicalPreviewPackage).where(CanonicalPreviewPackage.generated_at >= since, CanonicalPreviewPackage.provider == provider, CanonicalPreviewPackage.environment == environment, CanonicalPreviewPackage.product == product)) or 0)
            counts["activations"] = int(await db.scalar(select(func.count()).select_from(CanonicalProvingActivation).where(CanonicalProvingActivation.activated_at >= since, CanonicalProvingActivation.provider == provider, CanonicalProvingActivation.environment == environment, CanonicalProvingActivation.product == product)) or 0)
            counts["orders"] = sum(1 for item in orders if item.created_at >= since)
            counts["positions_opened"] = sum(1 for item in trades if item.executed_at >= since and item.side == "buy")
            counts["positions_closed"] = sum(1 for item in trades if item.executed_at >= since and item.side == "sell")
            counts["reconciliations"] = sum(1 for item in reconciliations if item.recorded_at >= since)
            failed_cycles = list((await db.scalars(
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.started_at >= since, AutonomousCycleRun.failure_reason.is_not(None))
                .order_by(AutonomousCycleRun.started_at.asc())
            )).all())
            occurrences: dict[str, dict[str, Any]] = {}
            for item in failed_cycles:
                code = str(item.failure_reason)
                row = occurrences.setdefault(code, {"reason_code": code, "count": 0, "first_occurred_at": item.started_at.isoformat(), "most_recent_at": item.started_at.isoformat()})
                row["count"] += 1
                row["most_recent_at"] = item.started_at.isoformat()
            evidence["blocker_occurrences"] = list(occurrences.values())
        return evidence, counts


async def autonomous_profit_status(*, provider: str, environment: str, product: str) -> dict[str, Any]:
    from app.services.orchestration.autonomous_operations_supervisor import resolve_autonomous_profit_snapshot

    evidence, _ = await _gather_autonomous_supervisor_evidence(provider=provider, environment=environment, product=product)
    return resolve_autonomous_profit_snapshot(evidence)


async def autonomous_profit_report(*, provider: str, environment: str, product: str, since: timedelta) -> dict[str, Any]:
    from app.services.orchestration.autonomous_operations_supervisor import resolve_autonomous_profit_snapshot

    now = datetime.now(timezone.utc)
    evidence, counts = await _gather_autonomous_supervisor_evidence(
        provider=provider, environment=environment, product=product, since=now - since,
    )
    snapshot = resolve_autonomous_profit_snapshot(evidence)
    return {
        "generated_at": now.isoformat(), "since": (now - since).isoformat(), "scope": {"provider": provider, "environment": environment, "product": product},
        "counts": counts, "net_realized_profit": snapshot["latest_net_profit"],
        "unresolved_blockers": snapshot["reason_codes"] if snapshot["human_action_required"] else [],
        "blocker_occurrences": evidence.get("blocker_occurrences", []),
        "human_action_required": snapshot["human_action_required"],
        "first_autonomous_profit_achieved": snapshot["overall_status"] == "FIRST_AUTONOMOUS_PROFIT_COMPLETE",
        "current": snapshot, "read_only": True,
    }


async def stale_package_inspect(*, provider: str, environment: str, product: str) -> dict[str, Any]:
    """Return exact stale canonical inventory without performing lifecycle mutation."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        fresh_count = int((await db.scalar(
            select(func.count()).select_from(CanonicalPreviewPackage).where(
                CanonicalPreviewPackage.provider == provider,
                CanonicalPreviewPackage.environment == environment,
                CanonicalPreviewPackage.product == product,
                CanonicalPreviewPackage.package_state.in_(["READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"]),
                CanonicalPreviewPackage.preview_expires_at > now,
            )
        )) or 0)
        packages = list((await db.scalars(
            select(CanonicalPreviewPackage).where(
                CanonicalPreviewPackage.provider == provider,
                CanonicalPreviewPackage.environment == environment,
                CanonicalPreviewPackage.product == product,
                CanonicalPreviewPackage.package_state.in_(["READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"]),
                CanonicalPreviewPackage.preview_expires_at <= now,
            ).order_by(desc(CanonicalPreviewPackage.generated_at), desc(CanonicalPreviewPackage.package_id))
        )).all())
        rows = []
        for package in packages:
            activation = await db.scalar(
                select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package.package_id).limit(1)
            )
            audit = await db.scalar(
                select(AuditLog).where(
                    AuditLog.entity_type == "canonical_preview_package",
                    AuditLog.entity_id == package.package_id,
                ).order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(1)
            )
            rows.append({
                "package_id": str(package.package_id), "campaign_id": str(package.campaign_id),
                "campaign_version": package.campaign_version,
                "mandate_id": None if package.mandate_id is None else str(package.mandate_id),
                "mandate_version_id": None if package.mandate_version_id is None else str(package.mandate_version_id),
                "provider": package.provider, "environment": package.environment, "product": package.product,
                "state": package.package_state, "created_at": package.created_at.isoformat(),
                "generated_at": package.generated_at.isoformat(),
                "preview_expires_at": package.preview_expires_at.isoformat(),
                "age_seconds": max(0, int((now - package.preview_expires_at).total_seconds())),
                "authorization_expires_at": None if package.authorization_expires_at is None else package.authorization_expires_at.isoformat(),
                "activation_id": None if activation is None else str(activation.activation_id),
                "activation_state": None if activation is None else activation.activation_state,
                "terminal": package.package_state in _TERMINAL_PACKAGE_STATES,
                "superseded": package.package_state == "SUPERSEDED" or package.superseded_at is not None,
                "latest_lifecycle_event": None if audit is None else {
                    "audit_id": audit.id, "action": audit.action, "actor": audit.actor,
                    "created_at": audit.created_at.isoformat(),
                },
                "blocks_fresh_package_creation": False,
                "blocks_activation_readiness": fresh_count == 0,
                "classification": "expired_nonterminal_history",
            })
        return {
            "generated_at": now.isoformat(), "provider": provider, "environment": environment,
            "product": product, "stale_package_count": len(rows), "fresh_eligible_package_count": fresh_count, "packages": rows,
            "repair_required": False,
            "explanation": "Expired package history is ineligible for activation but does not prevent creation or selection of a newer unexpired package.",
            "read_only": True,
        }


async def mandate_evaluation_identity_diagnostic(*, cycle_id: UUID, decision_record_id: UUID) -> dict[str, Any]:
    from app.services.orchestration.automatic_package_inspection import inspect_mandate_evaluation_identity_propagation

    try:
        async with AsyncSessionLocal() as db:
            return await inspect_mandate_evaluation_identity_propagation(
                db=db, cycle_id=cycle_id, decision_record_id=decision_record_id,
            )
    except Exception as exc:
        return {
            "verdict": "FAILED_CLOSED", "requested_cycle_id": str(cycle_id),
            "requested_decision_record_id": str(decision_record_id),
            "reason_codes": ["identity_diagnostic_failed"], "error_type": type(exc).__name__,
            "read_only": True,
        }


async def automatic_mandate_activation_proof(*, package_id: UUID) -> dict[str, Any]:
    from app.services.orchestration.automatic_package_inspection import inspect_automatic_mandate_activation_proof

    try:
        async with AsyncSessionLocal() as db:
            return await inspect_automatic_mandate_activation_proof(db=db, package_id=package_id)
    except Exception as exc:
        return {
            "verdict": "FAILED_CLOSED",
            "package_id": str(package_id),
            "reason_codes": ["proof_inspection_failed"],
            "error_type": type(exc).__name__,
            "read_only": True,
        }


async def inspect_legacy_campaign_transition(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_legacy_campaign_transition(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def transition_legacy_campaign_to_canonical_successor(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _transition_legacy_campaign_to_canonical_successor(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def rollback_legacy_campaign_transition(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _rollback_legacy_campaign_transition(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_legacy_campaign_transition_audit(*, legacy_campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_legacy_campaign_transition_audit(db=db, legacy_campaign_id=legacy_campaign_id, limit=limit)


async def inspect_campaign_aggregator_activation(*, campaign_id: UUID, campaign_version: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_campaign_aggregator_activation(db=db, campaign_id=campaign_id, campaign_version=campaign_version)
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def execute_campaign_aggregator_activation(
    *,
    campaign_id: UUID,
    campaign_version: int,
    actor: str,
    reason: str,
    idempotency_key: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _execute_campaign_aggregator_activation(
            db=db,
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            confirm=confirm,
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_campaign_aggregator_activation_audit(*, campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        records = await _fetch_campaign_aggregator_activation_audit(db=db, campaign_id=campaign_id, limit=limit)
    return {"campaign_id": str(campaign_id), "records": records}


async def canonical_proving_account_transition_preview(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    old_paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_canonical_proving_account_transition(
            db=db,
            request=CanonicalProvingAccountTransitionRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                old_paper_account_id=old_paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
                idempotency_key=None,
            ),
        )
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def canonical_proving_account_transition_execute(
    *,
    campaign_id: UUID,
    campaign_version: int,
    runtime_campaign_id: int,
    old_paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
    idempotency_key: str,
    expected_evidence_source_id: str | None = None,
    expected_evidence_observed_at: str | None = None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _transition_canonical_proving_account(
            db=db,
            request=CanonicalProvingAccountTransitionRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                runtime_campaign_id=runtime_campaign_id,
                old_paper_account_id=old_paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
                idempotency_key=idempotency_key,
                expected_evidence_source_id=expected_evidence_source_id,
                expected_evidence_observed_at=expected_evidence_observed_at,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def refresh_provider_balance_evidence(
    *,
    provider: str,
    environment: str,
    actor: str,
) -> dict[str, Any]:
    normalized_provider = provider.strip().lower()
    normalized_environment = environment.strip().lower()

    async with AsyncSessionLocal() as db:
        connection = await db.scalar(
            select(ExchangeConnection)
            .where(ExchangeConnection.provider == normalized_provider)
            .where(ExchangeConnection.environment == normalized_environment)
            .order_by(desc(ExchangeConnection.updated_at), desc(ExchangeConnection.exchange_connection_id))
            .limit(1)
        )
        if connection is None:
            raise LookupError(
                f"exchange connection not found for provider={normalized_provider} environment={normalized_environment}"
            )

        refreshed = await _refresh_exchange_balances(
            db=db,
            exchange_connection_id=connection.exchange_connection_id,
            actor=actor,
        )

        return {
            "provider": refreshed.provider,
            "environment": refreshed.environment,
            "exchange_connection_id": str(refreshed.exchange_connection_id),
            "status": refreshed.status,
            "readiness_verdict": refreshed.readiness.verdict,
            "total_equity_usd": None if refreshed.total_equity_usd is None else format(refreshed.total_equity_usd, "f"),
            "last_successful_sync_at": None
            if refreshed.last_successful_sync_at is None
            else refreshed.last_successful_sync_at.isoformat(),
            "last_verified_at": refreshed.readiness.checked_at.isoformat(),
            "invariants": {
                "no_order_submission": True,
                "sanctioned_refresh_path": "exchange_connections.refresh_exchange_balances",
            },
        }


async def canonical_proving_cap_transition_preview(
    *,
    campaign_id: UUID,
    campaign_version: int,
) -> dict[str, Any]:
    blockers: list[str] = []
    async with AsyncSessionLocal() as db:
        definition = await db.scalar(
            select(CapitalCampaignDefinition)
            .where(CapitalCampaignDefinition.campaign_id == campaign_id)
            .where(CapitalCampaignDefinition.version == campaign_version)
            .limit(1)
        )
        runtime = await db.scalar(
            select(CapitalCampaign)
            .where(CapitalCampaign.uuid == campaign_id)
            .limit(1)
        )
        if definition is None:
            blockers.append("definition_not_found")
        if runtime is None:
            blockers.append("runtime_campaign_not_found")
        if definition is not None and runtime is not None and runtime.definition_version != definition.version:
            blockers.append("runtime_definition_version_mismatch")
        if definition is not None and Decimal(str(definition.minimum_position_size)) != _PROVING_CAP_TARGET_USD:
            blockers.append("minimum_position_size_must_equal_5")

        active_package_count = 0
        active_activation_count = 0
        open_live_order_count = 0
        unresolved_reconciliation_count = 0
        non_compliant_activation_count = 0
        if definition is not None:
            active_package_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CanonicalPreviewPackage)
                    .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                    .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                    .where(CanonicalPreviewPackage.package_state.notin_(sorted(_TERMINAL_PACKAGE_STATES)))
                )
                or 0
            )
            non_compliant_activation_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CanonicalProvingActivation)
                    .where(CanonicalProvingActivation.campaign_id == campaign_id)
                    .where(CanonicalProvingActivation.campaign_version == campaign_version)
                    .where(CanonicalProvingActivation.no_leverage.is_(False))
                )
                or 0
            )
        if definition is not None and runtime is not None:
            runtime_provider, runtime_environment = _runtime_exchange_scope(getattr(runtime, "exchange", None))
            active_activation_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CanonicalProvingActivation)
                    .where(CanonicalProvingActivation.campaign_id == campaign_id)
                    .where(CanonicalProvingActivation.campaign_version == campaign_version)
                    .where(CanonicalProvingActivation.activation_state.notin_(sorted(_TERMINAL_ACTIVATION_STATES)))
                )
                or 0
            )
            open_live_order_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(LiveCryptoOrder.provider == runtime_provider if runtime_provider is not None else True)
                    .where(LiveCryptoOrder.environment == runtime_environment if runtime_environment is not None else True)
                    .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                )
                or 0
            )
            unresolved_reconciliation_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.capital_campaign_id == runtime.id)
                    .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATES)))
                )
                or 0
            )

        if active_package_count > 0:
            blockers.append("no_active_canonical_package")
        if active_activation_count > 0:
            blockers.append("no_active_proving_activation")
        if non_compliant_activation_count > 0:
            blockers.append("no_leverage_boundary_violated")
        if definition is not None and Decimal(str(definition.deployed_capital)) > Decimal("0"):
            blockers.append("no_deployed_capital")
        if open_live_order_count > 0:
            blockers.append("no_open_live_orders")
        if unresolved_reconciliation_count > 0:
            blockers.append("no_unresolved_reconciliation_state")

        before = {
            "maximum_position_size": None if definition is None else format(Decimal(str(definition.maximum_position_size)), "f"),
            "maximum_total_exposure": None if definition is None else format(Decimal(str(definition.maximum_total_exposure)), "f"),
            "minimum_position_size": None if definition is None else format(Decimal(str(definition.minimum_position_size)), "f"),
            "maximum_open_positions": None if definition is None else int(definition.maximum_open_positions),
            "deployed_capital": None if definition is None else format(Decimal(str(definition.deployed_capital)), "f"),
        }
        after = {
            "maximum_open_positions": 1,
            "maximum_position_size": format(_PROVING_CAP_TARGET_USD, "f"),
            "maximum_total_exposure": format(_PROVING_CAP_TARGET_USD, "f"),
        }
        already_exact = (
            definition is not None
            and int(definition.maximum_open_positions) == 1
            and Decimal(str(definition.maximum_position_size)) == _PROVING_CAP_TARGET_USD
            and Decimal(str(definition.maximum_total_exposure)) == _PROVING_CAP_TARGET_USD
        )

        return {
            "ready": len(blockers) == 0,
            "blockers": blockers,
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "before": before,
            "proposed": after,
            "already_exact": already_exact,
            "invariants": {
                "exact_proving_cap_usd": format(_PROVING_CAP_TARGET_USD, "f"),
                "no_order_submission": True,
                "active_package_count": active_package_count,
                "active_activation_count": active_activation_count,
                "open_live_order_count": open_live_order_count,
                "unresolved_reconciliation_count": unresolved_reconciliation_count,
                "non_compliant_activation_count": non_compliant_activation_count,
            },
        }


async def canonical_proving_cap_transition_execute(
    *,
    campaign_id: UUID,
    campaign_version: int,
    actor: str,
    confirm: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    if not confirm:
        raise PermissionError("confirm=true is required")
    if not idempotency_key.strip():
        raise PermissionError("idempotency_key is required")

    async with AsyncSessionLocal() as db:
        async with db.begin():
            definition = await db.scalar(
                select(CapitalCampaignDefinition)
                .where(CapitalCampaignDefinition.campaign_id == campaign_id)
                .where(CapitalCampaignDefinition.version == campaign_version)
                .with_for_update()
                .limit(1)
            )
            runtime = await db.scalar(
                select(CapitalCampaign)
                .where(CapitalCampaign.uuid == campaign_id)
                .with_for_update()
                .limit(1)
            )
            blockers: list[str] = []
            if definition is None:
                blockers.append("definition_not_found")
            if runtime is None:
                blockers.append("runtime_campaign_not_found")
            if definition is not None and runtime is not None and runtime.definition_version != definition.version:
                blockers.append("runtime_definition_version_mismatch")
            if definition is not None and Decimal(str(definition.minimum_position_size)) != _PROVING_CAP_TARGET_USD:
                blockers.append("minimum_position_size_must_equal_5")

            active_package_count = 0
            active_activation_count = 0
            open_live_order_count = 0
            unresolved_reconciliation_count = 0
            non_compliant_activation_count = 0
            if definition is not None:
                active_package_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CanonicalPreviewPackage)
                        .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                        .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                        .where(CanonicalPreviewPackage.package_state.notin_(sorted(_TERMINAL_PACKAGE_STATES)))
                    )
                    or 0
                )
                non_compliant_activation_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CanonicalProvingActivation)
                        .where(CanonicalProvingActivation.campaign_id == campaign_id)
                        .where(CanonicalProvingActivation.campaign_version == campaign_version)
                        .where(CanonicalProvingActivation.no_leverage.is_(False))
                    )
                    or 0
                )
            if definition is not None and runtime is not None:
                runtime_provider, runtime_environment = _runtime_exchange_scope(getattr(runtime, "exchange", None))
                active_activation_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CanonicalProvingActivation)
                        .where(CanonicalProvingActivation.campaign_id == campaign_id)
                        .where(CanonicalProvingActivation.campaign_version == campaign_version)
                        .where(CanonicalProvingActivation.activation_state.notin_(sorted(_TERMINAL_ACTIVATION_STATES)))
                    )
                    or 0
                )
                open_live_order_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(LiveCryptoOrder)
                        .where(LiveCryptoOrder.provider == runtime_provider if runtime_provider is not None else True)
                        .where(LiveCryptoOrder.environment == runtime_environment if runtime_environment is not None else True)
                        .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATES)))
                    )
                    or 0
                )
                unresolved_reconciliation_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(LiveReconciliationEvent)
                        .where(LiveReconciliationEvent.capital_campaign_id == runtime.id)
                        .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATES)))
                    )
                    or 0
                )

            if active_package_count > 0:
                blockers.append("no_active_canonical_package")
            if active_activation_count > 0:
                blockers.append("no_active_proving_activation")
            if non_compliant_activation_count > 0:
                blockers.append("no_leverage_boundary_violated")
            if definition is not None and Decimal(str(definition.deployed_capital)) > Decimal("0"):
                blockers.append("no_deployed_capital")
            if open_live_order_count > 0:
                blockers.append("no_open_live_orders")
            if unresolved_reconciliation_count > 0:
                blockers.append("no_unresolved_reconciliation_state")

            before_preview = {
                "maximum_position_size": None if definition is None else format(Decimal(str(definition.maximum_position_size)), "f"),
                "maximum_total_exposure": None if definition is None else format(Decimal(str(definition.maximum_total_exposure)), "f"),
                "minimum_position_size": None if definition is None else format(Decimal(str(definition.minimum_position_size)), "f"),
                "maximum_open_positions": None if definition is None else int(definition.maximum_open_positions),
                "deployed_capital": None if definition is None else format(Decimal(str(definition.deployed_capital)), "f"),
            }
            proposed_preview = {
                "maximum_open_positions": 1,
                "maximum_position_size": format(_PROVING_CAP_TARGET_USD, "f"),
                "maximum_total_exposure": format(_PROVING_CAP_TARGET_USD, "f"),
            }
            preview = {
                "ready": len(blockers) == 0,
                "blockers": blockers,
                "before": before_preview,
                "proposed": proposed_preview,
            }
            if not preview["ready"]:
                raise PermissionError("proving cap transition prerequisites failed: " + ", ".join(preview["blockers"]))
            if definition is None:
                raise LookupError("campaign definition not found")

            latest_audit = await db.scalar(
                select(AuditLog)
                .where(AuditLog.entity_type == "capital_campaign")
                .where(AuditLog.entity_id == campaign_id)
                .where(AuditLog.action == "capital_campaign.proving_cap_transition")
                .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
                .with_for_update()
                .limit(1)
            )

            if latest_audit is not None and isinstance(latest_audit.after_state, dict):
                prior_key = str(latest_audit.after_state.get("idempotency_key") or "")
                prior_cap = str(latest_audit.after_state.get("maximum_position_size") or "")
                if prior_key == idempotency_key and prior_cap == format(_PROVING_CAP_TARGET_USD, "f"):
                    return {
                        "changed": False,
                        "idempotent": True,
                        "audit_created": False,
                        "campaign_id": str(campaign_id),
                        "campaign_version": campaign_version,
                        "before": preview["before"],
                        "after": preview["proposed"],
                    }
                if prior_key and prior_key != idempotency_key:
                    raise PermissionError("conflicting retry blocked: proving cap transition already executed")

            before = dict(preview["before"])
            definition.maximum_open_positions = 1
            definition.maximum_position_size = _PROVING_CAP_TARGET_USD
            definition.maximum_total_exposure = _PROVING_CAP_TARGET_USD
            definition.updated_at = datetime.now(timezone.utc)

            after = {
                "maximum_open_positions": 1,
                "maximum_position_size": format(_PROVING_CAP_TARGET_USD, "f"),
                "maximum_total_exposure": format(_PROVING_CAP_TARGET_USD, "f"),
                "idempotency_key": idempotency_key,
                "runtime_campaign_id": None if runtime is None else runtime.id,
            }
            db.add(
                AuditLog(
                    actor=actor,
                    action="capital_campaign.proving_cap_transition",
                    entity_type="capital_campaign",
                    entity_id=campaign_id,
                    before_state=before,
                    after_state=after,
                )
            )

        return {
            "changed": True,
            "idempotent": False,
            "audit_created": True,
            "campaign_id": str(campaign_id),
            "campaign_version": campaign_version,
            "before": before,
            "after": after,
        }
