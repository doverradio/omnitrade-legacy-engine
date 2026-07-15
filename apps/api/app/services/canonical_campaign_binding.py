from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.exchange_connection import ExchangeConnection
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount


_TERMINAL_LIVE_ORDER_STATUSES = {"DRY_RUN_READY", "DRY_RUN_BLOCKED", "FILLED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED", "COMPLETED"}
_UNRESOLVED_RECONCILIATION_STATUSES = {"open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch"}
_CANONICAL_SUCCESSOR_ELIGIBLE_STATUSES = {"DRAFT", "READY", "PAUSED", "RUNNING"}


@dataclass(frozen=True, slots=True)
class CanonicalCampaignBindingRequest:
    campaign_id: UUID
    campaign_version: int
    paper_account_id: UUID
    live_trading_profile_id: UUID
    provider: str
    environment: str
    product_id: str
    actor: str
    confirm: bool


@dataclass(frozen=True, slots=True)
class BindingCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class BindingReadinessResult:
    ready: bool
    blockers: list[str]
    checks: list[BindingCheck]
    snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BindingMutationResult:
    changed: bool
    idempotent: bool
    before: dict[str, Any]
    after: dict[str, Any]
    readiness: BindingReadinessResult
    audit_created: bool


@dataclass(frozen=True, slots=True)
class LegacyCampaignTransitionRequest:
    legacy_campaign_id: UUID
    canonical_campaign_id: UUID
    canonical_campaign_version: int
    paper_account_id: UUID
    live_trading_profile_id: UUID
    provider: str
    environment: str
    product_id: str
    actor: str
    confirm: bool


@dataclass(frozen=True, slots=True)
class LegacyCampaignTransitionReadinessResult:
    ready: bool
    blockers: list[str]
    checks: list[BindingCheck]
    snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LegacyCampaignTransitionMutationResult:
    changed: bool
    idempotent: bool
    before: dict[str, Any]
    after: dict[str, Any]
    readiness: LegacyCampaignTransitionReadinessResult
    audit_created: bool


def _normalize_exchange_environment(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized not in {"production", "sandbox"}:
        raise ValueError(f"unsupported exchange environment: {environment}")
    return normalized


def _exchange_label(*, provider: str, environment: str) -> str:
    normalized = _normalize_exchange_environment(environment)
    return provider.strip().lower() if normalized == "production" else f"{provider.strip().lower()}_sandbox"


def _current_binding_snapshot(*, campaign: CapitalCampaign, definition: CapitalCampaignDefinition | None, paper_account: PaperAccount | None, live_profile: LiveTradingProfile | None, connection: ExchangeConnection | None, asset: Asset | None, conflicting_campaigns: list[CapitalCampaign], open_order_count: int, unresolved_reconciliation_count: int) -> dict[str, Any]:
    return {
        "campaign": {
            "id": str(campaign.uuid),
            "status": campaign.status,
            "paper_account_id": None if campaign.paper_account_id is None else str(campaign.paper_account_id),
            "exchange": campaign.exchange,
            "definition_campaign_id": None if campaign.definition_campaign_id is None else str(campaign.definition_campaign_id),
            "definition_version": campaign.definition_version,
            "starting_capital": format(Decimal(campaign.starting_capital), "f"),
            "current_equity": format(Decimal(campaign.current_equity), "f"),
        },
        "definition": None if definition is None else {
            "campaign_id": str(definition.campaign_id),
            "version": definition.version,
            "deployed_capital": format(Decimal(definition.deployed_capital), "f"),
            "current_campaign_equity": format(Decimal(definition.current_campaign_equity), "f"),
        },
        "paper_account": None if paper_account is None else {
            "paper_account_id": str(paper_account.id),
            "starting_balance": format(Decimal(paper_account.starting_balance), "f"),
            "current_cash_balance": format(Decimal(paper_account.current_cash_balance), "f"),
            "asset_class": paper_account.asset_class,
            "is_active": bool(paper_account.is_active),
        },
        "live_profile": None if live_profile is None else {
            "live_trading_profile_id": str(live_profile.id),
            "paper_account_id": str(live_profile.paper_account_id),
            "operating_mode": live_profile.operating_mode,
            "lifecycle_state": live_profile.lifecycle_state,
            "approval_state": live_profile.approval_state,
            "provenance_metadata": live_profile.provenance_metadata,
        },
        "connection": None if connection is None else {
            "exchange_connection_id": str(connection.exchange_connection_id),
            "provider": connection.provider,
            "environment": connection.environment,
            "credentials_valid": bool(connection.credentials_valid),
        },
        "asset": None if asset is None else {
            "asset_id": str(asset.id),
            "symbol": asset.symbol,
            "exchange": asset.exchange,
            "is_active": bool(asset.is_active),
        },
        "conflicting_campaigns": [
            {
                "campaign_uuid": str(item.uuid),
                "status": item.status,
                "paper_account_id": None if item.paper_account_id is None else str(item.paper_account_id),
                "exchange": item.exchange,
            }
            for item in conflicting_campaigns
        ],
        "open_order_count": open_order_count,
        "unresolved_reconciliation_count": unresolved_reconciliation_count,
    }


async def _load_definition(*, db: AsyncSession, campaign_id: UUID, version: int) -> CapitalCampaignDefinition | None:
    return await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == version)
        .limit(1)
    )


async def _load_definition_for_update(*, db: AsyncSession, campaign_id: UUID, version: int) -> CapitalCampaignDefinition | None:
    return await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == version)
        .with_for_update()
        .limit(1)
    )


async def _load_runtime(*, db: AsyncSession, campaign_id: UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))


async def _load_runtime_for_update(*, db: AsyncSession, campaign_id: UUID) -> CapitalCampaign | None:
    return await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == campaign_id)
        .with_for_update()
        .limit(1)
    )


async def _load_paper_account(*, db: AsyncSession, paper_account_id: UUID) -> PaperAccount | None:
    return await db.scalar(select(PaperAccount).where(PaperAccount.id == paper_account_id).limit(1))


async def _load_live_profile(*, db: AsyncSession, live_trading_profile_id: UUID) -> LiveTradingProfile | None:
    return await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1))


async def _load_connection(*, db: AsyncSession, provider: str, environment: str) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == provider)
        .where(ExchangeConnection.environment == environment)
        .order_by(ExchangeConnection.created_at.desc(), ExchangeConnection.exchange_connection_id.desc())
        .limit(1)
    )


async def _load_asset(*, db: AsyncSession, provider: str, environment: str, product_id: str) -> Asset | None:
    exchange = _exchange_label(provider=provider, environment=environment)
    return await db.scalar(
        select(Asset)
        .where(Asset.symbol == product_id.split("-")[0])
        .where(Asset.exchange == exchange)
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(1)
    )


async def _load_conflicting_campaigns(*, db: AsyncSession, paper_account_id: UUID, exchange: str, campaign_id: UUID) -> list[CapitalCampaign]:
    rows = (
        await db.execute(
            select(CapitalCampaign)
            .where(CapitalCampaign.paper_account_id == paper_account_id)
            .where(CapitalCampaign.exchange == exchange)
            .where(CapitalCampaign.uuid != campaign_id)
            .where(CapitalCampaign.status.notin_(["ARCHIVED", "COMPLETED"]))
            .order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
        )
    ).scalars().all()
    return list(rows)


async def _load_transition_conflicting_campaigns(
    *,
    db: AsyncSession,
    paper_account_id: UUID,
    exchange: str,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
) -> list[CapitalCampaign]:
    rows = (
        await db.execute(
            select(CapitalCampaign)
            .where(CapitalCampaign.paper_account_id == paper_account_id)
            .where(CapitalCampaign.exchange == exchange)
            .where(CapitalCampaign.uuid.notin_([legacy_campaign_id, canonical_campaign_id]))
            .where(CapitalCampaign.status.notin_(["ARCHIVED", "COMPLETED"]))
            .order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
        )
    ).scalars().all()
    return list(rows)


async def _count_open_live_orders(*, db: AsyncSession, provider: str, environment: str, product_id: str) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(LiveCryptoOrder)
        .where(LiveCryptoOrder.provider == provider)
        .where(LiveCryptoOrder.environment == environment)
        .where(LiveCryptoOrder.product_id == product_id)
        .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATUSES)))
    )
    return int(count or 0)


async def _count_unresolved_reconciliation_events(*, db: AsyncSession, live_trading_profile_id: UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.live_trading_profile_id == live_trading_profile_id)
        .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATUSES)))
    )
    return int(count or 0)


async def _count_unresolved_reconciliation_events_for_campaign(*, db: AsyncSession, capital_campaign_id: int) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.capital_campaign_id == capital_campaign_id)
        .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATUSES)))
    )
    return int(count or 0)


async def _count_pending_accounting_closure_for_campaign(*, db: AsyncSession, capital_campaign_id: int) -> int:
    rows = list(
        (
            await db.execute(
                select(LiveReconciliationEvent.id)
                .where(LiveReconciliationEvent.capital_campaign_id == capital_campaign_id)
                .where(LiveReconciliationEvent.reconciliation_status.in_(["filled", "partially_filled"]))
            )
        ).scalars().all()
    )
    if not rows:
        return 0
    accounted_ids = set(
        (
            await db.execute(
                select(LiveAccountingRecord.reconciliation_event_id)
                .where(LiveAccountingRecord.capital_campaign_id == capital_campaign_id)
                .where(LiveAccountingRecord.reconciliation_event_id.in_(rows))
            )
        ).scalars().all()
    )
    return int(len([item for item in rows if item not in accounted_ids]))


async def _count_open_positions_for_campaign(*, db: AsyncSession, capital_campaign_id: int) -> int:
    rows = list(
        (
            await db.execute(
                select(
                    LiveAccountingRecord.symbol,
                    LiveAccountingRecord.side,
                    LiveAccountingRecord.filled_quantity,
                )
                .where(LiveAccountingRecord.capital_campaign_id == capital_campaign_id)
                .where(LiveAccountingRecord.record_type.in_(["fill_accounting", "partial_fill_accounting"]))
                .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.id.asc())
            )
        ).all()
    )
    if not rows:
        return 0

    net_by_symbol: dict[str, Decimal] = {}
    for symbol, side, qty in rows:
        key = str(symbol).upper()
        current = net_by_symbol.get(key, Decimal("0"))
        quantity = Decimal(qty)
        if str(side).lower() == "buy":
            net_by_symbol[key] = current + quantity
        elif str(side).lower() == "sell":
            net_by_symbol[key] = current - quantity

    return int(sum(1 for value in net_by_symbol.values() if value > Decimal("0")))


async def _count_open_live_orders_for_campaign(
    *,
    db: AsyncSession,
    capital_campaign_id: int,
    provider: str,
    environment: str,
    product_id: str,
) -> int:
    rows = list(
        (
            await db.execute(
                select(LiveCryptoOrder)
                .where(LiveCryptoOrder.provider == provider)
                .where(LiveCryptoOrder.environment == environment)
                .where(LiveCryptoOrder.product_id == product_id)
                .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATUSES)))
                .order_by(LiveCryptoOrder.created_at.desc(), LiveCryptoOrder.live_crypto_order_id.desc())
            )
        ).scalars().all()
    )
    if not rows:
        return 0

    order_ids = [item.live_crypto_order_id for item in rows]
    reconciled_order_ids = set(
        (
            await db.execute(
                select(LiveReconciliationEvent.live_crypto_order_id)
                .where(LiveReconciliationEvent.live_crypto_order_id.in_(order_ids))
                .where(LiveReconciliationEvent.capital_campaign_id == capital_campaign_id)
            )
        ).scalars().all()
    )

    count = 0
    for order in rows:
        payload = order.safe_provider_response if isinstance(order.safe_provider_response, dict) else {}
        raw_campaign_id = payload.get("capital_campaign_id")
        campaign_match = False
        if raw_campaign_id is not None:
            try:
                campaign_match = int(raw_campaign_id) == capital_campaign_id
            except (TypeError, ValueError):
                campaign_match = False
        if campaign_match or order.live_crypto_order_id in reconciled_order_ids:
            count += 1
    return count


def _check(condition: bool, code: str, detail: str) -> BindingCheck:
    return BindingCheck(code=code, passed=condition, detail=detail)


async def inspect_canonical_campaign_binding(*, db: AsyncSession, request: CanonicalCampaignBindingRequest) -> BindingReadinessResult:
    environment = _normalize_exchange_environment(request.environment)
    exchange = _exchange_label(provider=request.provider, environment=environment)

    definition = await _load_definition(db=db, campaign_id=request.campaign_id, version=request.campaign_version)
    runtime = await _load_runtime(db=db, campaign_id=request.campaign_id)
    paper_account = await _load_paper_account(db=db, paper_account_id=request.paper_account_id)
    live_profile = await _load_live_profile(db=db, live_trading_profile_id=request.live_trading_profile_id)
    connection = await _load_connection(db=db, provider=request.provider, environment=environment)
    asset = await _load_asset(db=db, provider=request.provider, environment=environment, product_id=request.product_id)

    conflicting_campaigns: list[CapitalCampaign] = []
    open_order_count = 0
    unresolved_reconciliation_count = 0

    checks: list[BindingCheck] = []
    blockers: list[str] = []

    checks.append(_check(definition is not None, "definition_exists", f"definition_version={request.campaign_version}"))
    if definition is not None:
        checks.append(_check(definition.campaign_id == request.campaign_id, "definition_campaign_id_matches", f"definition_campaign_id={definition.campaign_id}"))
        checks.append(_check(definition.version == request.campaign_version, "definition_version_matches", f"definition_version={definition.version}"))
        checks.append(_check(Decimal(definition.deployed_capital) == Decimal("0"), "definition_has_no_deployed_capital", f"deployed_capital={definition.deployed_capital}"))
    checks.append(_check(runtime is not None, "runtime_exists", f"runtime_campaign_uuid={request.campaign_id}"))
    if runtime is not None:
        checks.append(_check(runtime.definition_campaign_id == request.campaign_id, "runtime_definition_pin_matches", f"runtime_definition_campaign_id={runtime.definition_campaign_id}"))
        checks.append(_check(runtime.definition_version == request.campaign_version, "runtime_definition_version_matches", f"runtime_definition_version={runtime.definition_version}"))
        checks.append(_check(Decimal(runtime.current_equity) == Decimal(runtime.starting_capital), "runtime_zero_deployed_capital", f"current_equity={runtime.current_equity} starting_capital={runtime.starting_capital}"))
    checks.append(_check(paper_account is not None, "paper_account_exists", f"paper_account_id={request.paper_account_id}"))
    if paper_account is not None:
        checks.append(_check(paper_account.asset_class == "crypto", "paper_account_crypto", f"asset_class={paper_account.asset_class}"))
        checks.append(_check(bool(paper_account.is_active), "paper_account_active", f"is_active={paper_account.is_active}"))
    checks.append(_check(live_profile is not None, "live_profile_exists", f"live_trading_profile_id={request.live_trading_profile_id}"))
    if live_profile is not None:
        checks.append(_check(live_profile.paper_account_id == request.paper_account_id, "live_profile_owns_paper_account", f"profile_paper_account_id={live_profile.paper_account_id}"))
        provenance = live_profile.provenance_metadata if isinstance(live_profile.provenance_metadata, dict) else {}
        checks.append(_check(str(provenance.get("provider") or "").strip().lower() == request.provider.strip().lower(), "live_profile_provider_matches", f"profile_provider={provenance.get('provider')!s}"))
        checks.append(_check(_normalize_exchange_environment(str(provenance.get("exchange_environment") or provenance.get("environment") or environment)) == environment, "live_profile_environment_matches", f"profile_environment={provenance.get('exchange_environment') or provenance.get('environment')!s}"))
    checks.append(_check(connection is not None, "connection_exists", f"provider={request.provider} environment={environment}"))
    if connection is not None:
        checks.append(_check(connection.provider == request.provider, "connection_provider_matches", f"connection_provider={connection.provider}"))
        checks.append(_check(connection.environment == environment, "connection_environment_matches", f"connection_environment={connection.environment}"))
    checks.append(_check(asset is not None, "asset_exists", f"product_id={request.product_id}"))
    if asset is not None:
        checks.append(_check(asset.symbol == request.product_id.split("-")[0], "asset_symbol_matches", f"asset_symbol={asset.symbol}"))
        checks.append(_check(asset.exchange == exchange, "asset_exchange_matches", f"asset_exchange={asset.exchange}"))

    if paper_account is not None:
        conflicting_campaigns = await _load_conflicting_campaigns(
            db=db,
            paper_account_id=paper_account.id,
            exchange=exchange,
            campaign_id=request.campaign_id,
        )
    open_order_count = await _count_open_live_orders(db=db, provider=request.provider, environment=environment, product_id=request.product_id)
    unresolved_reconciliation_count = await _count_unresolved_reconciliation_events(db=db, live_trading_profile_id=request.live_trading_profile_id)

    checks.append(_check(not conflicting_campaigns, "no_conflicting_active_campaign", f"conflicting_campaign_count={len(conflicting_campaigns)}"))
    checks.append(_check(open_order_count == 0, "no_open_provider_order_uncertainty", f"open_live_order_count={open_order_count}"))
    checks.append(_check(unresolved_reconciliation_count == 0, "clean_reconciliation_state", f"unresolved_reconciliation_count={unresolved_reconciliation_count}"))

    if request.confirm:
        checks.append(_check(True, "operator_confirmation_present", "confirm=true"))

    blockers = [item.code for item in checks if not item.passed]
    snapshot = _current_binding_snapshot(
        campaign=runtime if runtime is not None else CapitalCampaign(
            uuid=request.campaign_id,
            owner="",
            name="",
            status="DRAFT",
            campaign_type="definition_pinned_runtime",
            exchange=None,
            paper_account_id=None,
            validation_run_id=None,
            strategy_id=None,
            definition_campaign_id=request.campaign_id,
            definition_version=request.campaign_version,
            starting_capital=Decimal("0"),
            current_equity=Decimal("0"),
            realized_profit=Decimal("0"),
            unrealized_profit=Decimal("0"),
            fees=Decimal("0"),
            roi=Decimal("0"),
        ),
        definition=definition,
        paper_account=paper_account,
        live_profile=live_profile,
        connection=connection,
        asset=asset,
        conflicting_campaigns=conflicting_campaigns,
        open_order_count=open_order_count,
        unresolved_reconciliation_count=unresolved_reconciliation_count,
    )

    return BindingReadinessResult(ready=not blockers, blockers=blockers, checks=checks, snapshot=snapshot)


async def bind_canonical_campaign_runtime(*, db: AsyncSession, request: CanonicalCampaignBindingRequest) -> BindingMutationResult:
    if not request.confirm:
        raise PermissionError("confirm=true is required")

    readiness = await inspect_canonical_campaign_binding(db=db, request=request)
    if not readiness.ready:
        raise PermissionError("canonical campaign binding prerequisites failed: " + ", ".join(readiness.blockers))

    environment = _normalize_exchange_environment(request.environment)
    exchange = _exchange_label(provider=request.provider, environment=environment)
    runtime = await _load_runtime(db=db, campaign_id=request.campaign_id)
    if runtime is None:
        raise LookupError("runtime campaign not found")

    before = {
        "paper_account_id": None if runtime.paper_account_id is None else str(runtime.paper_account_id),
        "exchange": runtime.exchange,
        "definition_campaign_id": None if runtime.definition_campaign_id is None else str(runtime.definition_campaign_id),
        "definition_version": runtime.definition_version,
    }

    already_bound = before["paper_account_id"] == str(request.paper_account_id) and before["exchange"] == exchange
    if already_bound:
        return BindingMutationResult(changed=False, idempotent=True, before=before, after=before, readiness=readiness, audit_created=False)

    async with db.begin():
        runtime.paper_account_id = request.paper_account_id
        runtime.exchange = exchange
        runtime.updated_at = datetime.now(timezone.utc)
        await db.flush()

        after = {
            "paper_account_id": str(runtime.paper_account_id) if runtime.paper_account_id is not None else None,
            "exchange": runtime.exchange,
            "definition_campaign_id": str(runtime.definition_campaign_id) if runtime.definition_campaign_id is not None else None,
            "definition_version": runtime.definition_version,
        }
        db.add(
            AuditLog(
                actor=request.actor,
                action="capital_campaign.bind_runtime",
                entity_type="capital_campaign",
                entity_id=runtime.uuid,
                before_state=before,
                after_state=after,
            )
        )

    return BindingMutationResult(changed=True, idempotent=False, before=before, after=after, readiness=readiness, audit_created=True)


async def fetch_canonical_campaign_binding_audit(*, db: AsyncSession, campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    rows = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "capital_campaign")
                .where(AuditLog.entity_id == campaign_id)
                .where(AuditLog.action == "capital_campaign.bind_runtime")
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    )
    return {
        "campaign_id": str(campaign_id),
        "limit": limit,
        "total": len(rows),
        "items": [
            {
                "actor": item.actor,
                "action": item.action,
                "before_state": item.before_state,
                "after_state": item.after_state,
                "created_at": item.created_at,
            }
            for item in rows
        ],
    }


async def inspect_legacy_campaign_transition(
    *,
    db: AsyncSession,
    request: LegacyCampaignTransitionRequest,
) -> LegacyCampaignTransitionReadinessResult:
    legacy = await _load_runtime(db=db, campaign_id=request.legacy_campaign_id)
    canonical = await _load_runtime(db=db, campaign_id=request.canonical_campaign_id)
    canonical_definition = await _load_definition(
        db=db,
        campaign_id=request.canonical_campaign_id,
        version=request.canonical_campaign_version,
    )
    return await _inspect_legacy_campaign_transition_locked(
        db=db,
        request=request,
        legacy=legacy,
        canonical=canonical,
        canonical_definition=canonical_definition,
    )


async def _inspect_legacy_campaign_transition_locked(
    *,
    db: AsyncSession,
    request: LegacyCampaignTransitionRequest,
    legacy: CapitalCampaign | None,
    canonical: CapitalCampaign | None,
    canonical_definition: CapitalCampaignDefinition | None,
) -> LegacyCampaignTransitionReadinessResult:
    environment = _normalize_exchange_environment(request.environment)
    exchange = _exchange_label(provider=request.provider, environment=environment)
    checks: list[BindingCheck] = []

    paper_account = await _load_paper_account(db=db, paper_account_id=request.paper_account_id)
    live_profile = await _load_live_profile(db=db, live_trading_profile_id=request.live_trading_profile_id)
    connection = await _load_connection(db=db, provider=request.provider, environment=environment)
    asset = await _load_asset(db=db, provider=request.provider, environment=environment, product_id=request.product_id)

    checks.append(_check(legacy is not None, "legacy_campaign_exists", f"legacy_campaign_id={request.legacy_campaign_id}"))
    checks.append(_check(canonical is not None, "canonical_campaign_exists", f"canonical_campaign_id={request.canonical_campaign_id}"))
    checks.append(
        _check(
            canonical_definition is not None,
            "canonical_definition_exists",
            f"canonical_campaign_version={request.canonical_campaign_version}",
        )
    )
    checks.append(
        _check(
            request.legacy_campaign_id != request.canonical_campaign_id,
            "legacy_and_canonical_distinct",
            "legacy and canonical campaign ids must differ",
        )
    )

    checks.append(_check(paper_account is not None, "paper_account_exists", f"paper_account_id={request.paper_account_id}"))
    checks.append(_check(live_profile is not None, "live_profile_exists", f"live_trading_profile_id={request.live_trading_profile_id}"))
    checks.append(_check(connection is not None, "intended_provider_environment_compatible", f"provider={request.provider} environment={environment}"))
    checks.append(_check(asset is not None, "intended_provider_environment_product_compatible", f"product_id={request.product_id} exchange={exchange}"))
    if live_profile is not None:
        checks.append(
            _check(
                live_profile.paper_account_id == request.paper_account_id,
                "live_profile_owns_paper_account",
                f"profile_paper_account_id={live_profile.paper_account_id}",
            )
        )

    open_live_order_count = 0
    unresolved_reconciliation_count = 0
    open_position_count = 0
    pending_accounting_closure_count = 0

    canonical_open_live_order_count = 0
    canonical_unresolved_reconciliation_count = 0
    canonical_open_position_count = 0
    canonical_pending_accounting_closure_count = 0
    conflicting_campaign_count = 0
    legacy_successor_audit: dict[str, Any] | None = None

    if legacy is not None:
        checks.append(
            _check(
                legacy.paper_account_id == request.paper_account_id,
                "legacy_paper_account_matches",
                f"legacy_paper_account_id={legacy.paper_account_id}",
            )
        )
        checks.append(
            _check(
                legacy.exchange == exchange,
                "legacy_exchange_matches",
                f"legacy_exchange={legacy.exchange}",
            )
        )
        checks.append(
            _check(
                Decimal(legacy.current_equity) == Decimal(legacy.starting_capital),
                "legacy_zero_deployed_capital",
                f"legacy_current_equity={legacy.current_equity} legacy_starting_capital={legacy.starting_capital}",
            )
        )

        open_live_order_count = await _count_open_live_orders_for_campaign(
            db=db,
            capital_campaign_id=legacy.id,
            provider=request.provider,
            environment=environment,
            product_id=request.product_id,
        )
        unresolved_reconciliation_count = await _count_unresolved_reconciliation_events_for_campaign(
            db=db,
            capital_campaign_id=legacy.id,
        )
        open_position_count = await _count_open_positions_for_campaign(
            db=db,
            capital_campaign_id=legacy.id,
        )
        pending_accounting_closure_count = await _count_pending_accounting_closure_for_campaign(
            db=db,
            capital_campaign_id=legacy.id,
        )

        latest_transition = await db.scalar(
            select(AuditLog)
            .where(AuditLog.entity_type == "capital_campaign")
            .where(AuditLog.entity_id == legacy.uuid)
            .where(AuditLog.action == "capital_campaign.transition_to_successor")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        if latest_transition is not None:
            legacy_successor_audit = {
                "actor": latest_transition.actor,
                "before_state": latest_transition.before_state,
                "after_state": latest_transition.after_state,
                "created_at": latest_transition.created_at,
            }

        checks.append(
            _check(
                open_live_order_count == 0,
                "legacy_no_open_provider_order",
                f"open_live_order_count={open_live_order_count}",
            )
        )
        checks.append(
            _check(
                unresolved_reconciliation_count == 0,
                "legacy_clean_reconciliation_state",
                f"unresolved_reconciliation_count={unresolved_reconciliation_count}",
            )
        )
        checks.append(
            _check(
                open_position_count == 0,
                "legacy_no_open_live_position",
                f"open_position_count={open_position_count}",
            )
        )
        checks.append(
            _check(
                pending_accounting_closure_count == 0,
                "legacy_no_pending_accounting_closure",
                f"pending_accounting_closure_count={pending_accounting_closure_count}",
            )
        )

    if canonical is not None:
        checks.append(
            _check(
                canonical.definition_campaign_id == request.canonical_campaign_id,
                "canonical_runtime_definition_identity_matches",
                f"canonical_definition_campaign_id={canonical.definition_campaign_id}",
            )
        )
        checks.append(
            _check(
                canonical.definition_version == request.canonical_campaign_version,
                "canonical_runtime_definition_version_matches",
                f"canonical_definition_version={canonical.definition_version}",
            )
        )
        checks.append(
            _check(
                canonical.paper_account_id is None or canonical.paper_account_id == request.paper_account_id,
                "canonical_paper_account_unbound_or_matches",
                f"canonical_paper_account_id={canonical.paper_account_id}",
            )
        )
        checks.append(
            _check(
                canonical.exchange is None or canonical.exchange == exchange,
                "canonical_exchange_unbound_or_matches",
                f"canonical_exchange={canonical.exchange}",
            )
        )
        checks.append(
            _check(
                canonical.status in _CANONICAL_SUCCESSOR_ELIGIBLE_STATUSES,
                "canonical_status_eligible_for_successor",
                f"canonical_status={canonical.status}",
            )
        )
        checks.append(
            _check(
                Decimal(canonical.current_equity) == Decimal(canonical.starting_capital),
                "canonical_runtime_zero_deployed_capital",
                f"canonical_current_equity={canonical.current_equity} canonical_starting_capital={canonical.starting_capital}",
            )
        )

        canonical_open_live_order_count = await _count_open_live_orders_for_campaign(
            db=db,
            capital_campaign_id=canonical.id,
            provider=request.provider,
            environment=environment,
            product_id=request.product_id,
        )
        canonical_unresolved_reconciliation_count = await _count_unresolved_reconciliation_events_for_campaign(
            db=db,
            capital_campaign_id=canonical.id,
        )
        canonical_open_position_count = await _count_open_positions_for_campaign(
            db=db,
            capital_campaign_id=canonical.id,
        )
        canonical_pending_accounting_closure_count = await _count_pending_accounting_closure_for_campaign(
            db=db,
            capital_campaign_id=canonical.id,
        )

        checks.append(
            _check(
                canonical_open_live_order_count == 0,
                "canonical_no_open_provider_order",
                f"canonical_open_live_order_count={canonical_open_live_order_count}",
            )
        )
        checks.append(
            _check(
                canonical_unresolved_reconciliation_count == 0,
                "canonical_clean_reconciliation_state",
                f"canonical_unresolved_reconciliation_count={canonical_unresolved_reconciliation_count}",
            )
        )
        checks.append(
            _check(
                canonical_open_position_count == 0,
                "canonical_no_open_live_position",
                f"canonical_open_position_count={canonical_open_position_count}",
            )
        )
        checks.append(
            _check(
                canonical_pending_accounting_closure_count == 0,
                "canonical_no_pending_accounting_closure",
                f"canonical_pending_accounting_closure_count={canonical_pending_accounting_closure_count}",
            )
        )

    if canonical_definition is not None:
        checks.append(
            _check(
                Decimal(canonical_definition.deployed_capital) == Decimal("0"),
                "canonical_definition_zero_deployed_capital",
                f"canonical_definition_deployed_capital={canonical_definition.deployed_capital}",
            )
        )

    canonical_successor_required_codes = {
        "canonical_campaign_exists",
        "canonical_definition_exists",
        "canonical_runtime_definition_identity_matches",
        "canonical_runtime_definition_version_matches",
        "canonical_paper_account_unbound_or_matches",
        "canonical_exchange_unbound_or_matches",
        "canonical_status_eligible_for_successor",
        "canonical_runtime_zero_deployed_capital",
        "canonical_definition_zero_deployed_capital",
        "canonical_no_open_provider_order",
        "canonical_clean_reconciliation_state",
        "canonical_no_open_live_position",
        "canonical_no_pending_accounting_closure",
        "intended_provider_environment_compatible",
        "intended_provider_environment_product_compatible",
    }
    canonical_successor_eligible = all(
        item.passed for item in checks if item.code in canonical_successor_required_codes
    )
    checks.append(
        _check(
            canonical_successor_eligible,
            "canonical_successor_eligible_for_binding",
            f"eligible={canonical_successor_eligible}",
        )
    )

    if paper_account is not None:
        conflicting_campaigns = await _load_transition_conflicting_campaigns(
            db=db,
            paper_account_id=paper_account.id,
            exchange=exchange,
            legacy_campaign_id=request.legacy_campaign_id,
            canonical_campaign_id=request.canonical_campaign_id,
        )
        conflicting_campaign_count = len(conflicting_campaigns)
        checks.append(
            _check(
                conflicting_campaign_count == 0,
                "no_other_active_campaign_conflict",
                f"conflicting_campaign_count={conflicting_campaign_count}",
            )
        )

    if request.confirm:
        checks.append(_check(True, "operator_confirmation_present", "confirm=true"))

    blockers = [item.code for item in checks if not item.passed]
    if legacy is not None and legacy.status == "ARCHIVED":
        if legacy_successor_audit is None:
            blockers.append("legacy_archived_without_successor_audit")
        else:
            after = legacy_successor_audit.get("after_state") if isinstance(legacy_successor_audit, dict) else {}
            successor_id = str((after or {}).get("successor_campaign_id") or "")
            successor_version = int((after or {}).get("successor_campaign_version") or 0)
            if successor_id == str(request.canonical_campaign_id) and successor_version == request.canonical_campaign_version:
                blockers.append("legacy_already_superseded_by_requested_successor")
            else:
                blockers.append("legacy_already_superseded_by_different_successor")

    snapshot = {
        "legacy_campaign": None if legacy is None else {
            "id": str(legacy.uuid),
            "status": legacy.status,
            "paper_account_id": None if legacy.paper_account_id is None else str(legacy.paper_account_id),
            "exchange": legacy.exchange,
            "starting_capital": format(Decimal(legacy.starting_capital), "f"),
            "current_equity": format(Decimal(legacy.current_equity), "f"),
        },
        "canonical_campaign": None if canonical is None else {
            "id": str(canonical.uuid),
            "status": canonical.status,
            "definition_campaign_id": None if canonical.definition_campaign_id is None else str(canonical.definition_campaign_id),
            "definition_version": canonical.definition_version,
            "paper_account_id": None if canonical.paper_account_id is None else str(canonical.paper_account_id),
            "exchange": canonical.exchange,
            "starting_capital": format(Decimal(canonical.starting_capital), "f"),
            "current_equity": format(Decimal(canonical.current_equity), "f"),
        },
        "canonical_definition": None if canonical_definition is None else {
            "campaign_id": str(canonical_definition.campaign_id),
            "version": canonical_definition.version,
            "deployed_capital": format(Decimal(canonical_definition.deployed_capital), "f"),
        },
        "open_live_order_count": open_live_order_count,
        "unresolved_reconciliation_count": unresolved_reconciliation_count,
        "open_position_count": open_position_count,
        "pending_accounting_closure_count": pending_accounting_closure_count,
        "canonical_open_live_order_count": canonical_open_live_order_count,
        "canonical_unresolved_reconciliation_count": canonical_unresolved_reconciliation_count,
        "canonical_open_position_count": canonical_open_position_count,
        "canonical_pending_accounting_closure_count": canonical_pending_accounting_closure_count,
        "conflicting_campaign_count": conflicting_campaign_count,
        "latest_transition_audit": legacy_successor_audit,
    }
    ready = not blockers
    return LegacyCampaignTransitionReadinessResult(ready=ready, blockers=sorted(set(blockers)), checks=checks, snapshot=snapshot)


def _session_in_transaction(db: AsyncSession) -> bool:
    return bool(db.in_transaction()) if hasattr(db, "in_transaction") else False


async def _transition_legacy_campaign_to_canonical_successor_without_begin(
    *,
    db: AsyncSession,
    request: LegacyCampaignTransitionRequest,
) -> LegacyCampaignTransitionMutationResult:
    legacy = await _load_runtime_for_update(db=db, campaign_id=request.legacy_campaign_id)
    canonical = await _load_runtime_for_update(db=db, campaign_id=request.canonical_campaign_id)
    canonical_definition = await _load_definition_for_update(
        db=db,
        campaign_id=request.canonical_campaign_id,
        version=request.canonical_campaign_version,
    )
    readiness = await _inspect_legacy_campaign_transition_locked(
        db=db,
        request=request,
        legacy=legacy,
        canonical=canonical,
        canonical_definition=canonical_definition,
    )
    if not readiness.ready:
        if readiness.snapshot.get("legacy_campaign") and readiness.snapshot["legacy_campaign"].get("status") == "ARCHIVED":
            latest_transition = readiness.snapshot.get("latest_transition_audit")
            after = latest_transition.get("after_state") if isinstance(latest_transition, dict) else {}
            if (
                str((after or {}).get("successor_campaign_id") or "") == str(request.canonical_campaign_id)
                and int((after or {}).get("successor_campaign_version") or 0) == request.canonical_campaign_version
            ):
                before = {
                    "status": "ARCHIVED",
                    "paper_account_id": str(request.paper_account_id),
                }
                return LegacyCampaignTransitionMutationResult(
                    changed=False,
                    idempotent=True,
                    before=before,
                    after=before,
                    readiness=readiness,
                    audit_created=False,
                )
        raise PermissionError("legacy transition prerequisites failed: " + ", ".join(readiness.blockers))

    if legacy is None:
        raise LookupError("legacy campaign not found")

    before = {
        "status": legacy.status,
        "paper_account_id": None if legacy.paper_account_id is None else str(legacy.paper_account_id),
        "exchange": legacy.exchange,
    }

    legacy.status = "ARCHIVED"
    legacy.updated_at = datetime.now(timezone.utc)
    await db.flush()

    after = {
        "status": legacy.status,
        "paper_account_id": None if legacy.paper_account_id is None else str(legacy.paper_account_id),
        "exchange": legacy.exchange,
        "successor_campaign_id": str(request.canonical_campaign_id),
        "successor_campaign_version": request.canonical_campaign_version,
        "transition_reason": "superseded_by_canonical_campaign",
    }
    db.add(
        AuditLog(
            actor=request.actor,
            action="capital_campaign.transition_to_successor",
            entity_type="capital_campaign",
            entity_id=legacy.uuid,
            before_state=before,
            after_state=after,
        )
    )

    return LegacyCampaignTransitionMutationResult(
        changed=True,
        idempotent=False,
        before=before,
        after=after,
        readiness=readiness,
        audit_created=True,
    )


async def transition_legacy_campaign_to_canonical_successor(
    *,
    db: AsyncSession,
    request: LegacyCampaignTransitionRequest,
) -> LegacyCampaignTransitionMutationResult:
    if not request.confirm:
        raise PermissionError("confirm=true is required")

    if _session_in_transaction(db):
        return await _transition_legacy_campaign_to_canonical_successor_without_begin(db=db, request=request)

    async with db.begin():
        return await _transition_legacy_campaign_to_canonical_successor_without_begin(db=db, request=request)


async def fetch_legacy_campaign_transition_audit(*, db: AsyncSession, legacy_campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    rows = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "capital_campaign")
                .where(AuditLog.entity_id == legacy_campaign_id)
                .where(AuditLog.action.in_(["capital_campaign.transition_to_successor", "capital_campaign.transition_rollback"]))
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    )
    return {
        "legacy_campaign_id": str(legacy_campaign_id),
        "limit": limit,
        "total": len(rows),
        "items": [
            {
                "actor": item.actor,
                "action": item.action,
                "before_state": item.before_state,
                "after_state": item.after_state,
                "created_at": item.created_at,
            }
            for item in rows
        ],
    }


async def rollback_legacy_campaign_transition(
    *,
    db: AsyncSession,
    request: LegacyCampaignTransitionRequest,
) -> LegacyCampaignTransitionMutationResult:
    if not request.confirm:
        raise PermissionError("confirm=true is required")

    if _session_in_transaction(db):
        return await _rollback_legacy_campaign_transition_without_begin(db=db, request=request)

    async with db.begin():
        return await _rollback_legacy_campaign_transition_without_begin(db=db, request=request)


async def _rollback_legacy_campaign_transition_without_begin(
    *,
    db: AsyncSession,
    request: LegacyCampaignTransitionRequest,
) -> LegacyCampaignTransitionMutationResult:
    legacy = await _load_runtime_for_update(db=db, campaign_id=request.legacy_campaign_id)
    canonical = await _load_runtime_for_update(db=db, campaign_id=request.canonical_campaign_id)
    canonical_definition = await _load_definition_for_update(
        db=db,
        campaign_id=request.canonical_campaign_id,
        version=request.canonical_campaign_version,
    )
    if legacy is None:
        raise LookupError("legacy campaign not found")
    if legacy.status != "ARCHIVED":
        raise PermissionError("rollback supported only when legacy campaign is ARCHIVED")

    latest_transition = await db.scalar(
        select(AuditLog)
        .where(AuditLog.entity_type == "capital_campaign")
        .where(AuditLog.entity_id == request.legacy_campaign_id)
        .where(AuditLog.action == "capital_campaign.transition_to_successor")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if latest_transition is None or not isinstance(latest_transition.after_state, dict):
        raise PermissionError("rollback unavailable without prior transition evidence")

    successor_id = str(latest_transition.after_state.get("successor_campaign_id") or "")
    successor_version = int(latest_transition.after_state.get("successor_campaign_version") or 0)
    if successor_id != str(request.canonical_campaign_id) or successor_version != request.canonical_campaign_version:
        raise PermissionError("rollback successor identity mismatch")

    readiness = await _inspect_legacy_campaign_transition_locked(
        db=db,
        request=request,
        legacy=legacy,
        canonical=canonical,
        canonical_definition=canonical_definition,
    )
    blocking = [
        code
        for code in readiness.blockers
        if code in {
            "legacy_no_open_provider_order",
            "legacy_clean_reconciliation_state",
            "legacy_no_open_live_position",
            "legacy_no_pending_accounting_closure",
            "legacy_paper_account_matches",
            "legacy_exchange_matches",
        }
    ]
    if blocking:
        raise PermissionError("rollback prerequisites failed: " + ", ".join(sorted(blocking)))

    if canonical is None or canonical_definition is None:
        raise PermissionError("rollback unavailable without canonical runtime and definition")

    canonical_open_live_order_count = await _count_open_live_orders_for_campaign(
        db=db,
        capital_campaign_id=canonical.id,
        provider=request.provider,
        environment=_normalize_exchange_environment(request.environment),
        product_id=request.product_id,
    )
    canonical_unresolved_reconciliation_count = await _count_unresolved_reconciliation_events_for_campaign(
        db=db,
        capital_campaign_id=canonical.id,
    )
    canonical_open_position_count = await _count_open_positions_for_campaign(
        db=db,
        capital_campaign_id=canonical.id,
    )
    canonical_pending_accounting_closure_count = await _count_pending_accounting_closure_for_campaign(
        db=db,
        capital_campaign_id=canonical.id,
    )

    canonical_activity_blockers: list[str] = []
    if Decimal(canonical_definition.deployed_capital) != Decimal("0"):
        canonical_activity_blockers.append("canonical_definition_has_deployed_capital")
    if Decimal(canonical.current_equity) != Decimal(canonical.starting_capital):
        canonical_activity_blockers.append("canonical_runtime_nonzero_deployed_capital")
    if canonical_open_live_order_count > 0:
        canonical_activity_blockers.append("canonical_has_open_provider_order")
    if canonical_unresolved_reconciliation_count > 0:
        canonical_activity_blockers.append("canonical_has_unresolved_reconciliation")
    if canonical_open_position_count > 0:
        canonical_activity_blockers.append("canonical_has_open_live_position")
    if canonical_pending_accounting_closure_count > 0:
        canonical_activity_blockers.append("canonical_has_pending_accounting_closure")
    if canonical_activity_blockers:
        raise PermissionError(
            "rollback blocked after canonical campaign activity: "
            + ", ".join(sorted(canonical_activity_blockers))
        )

    before_status = str((latest_transition.before_state or {}).get("status") or "READY")
    if before_status not in {"DRAFT", "READY", "RUNNING", "PAUSED", "TARGET_REACHED", "COMPLETED"}:
        raise PermissionError("rollback target status is unsupported")

    before = {
        "status": legacy.status,
        "paper_account_id": None if legacy.paper_account_id is None else str(legacy.paper_account_id),
        "exchange": legacy.exchange,
    }
    if legacy.status == before_status:
        return LegacyCampaignTransitionMutationResult(
            changed=False,
            idempotent=True,
            before=before,
            after=before,
            readiness=readiness,
            audit_created=False,
        )

    legacy.status = before_status
    legacy.updated_at = datetime.now(timezone.utc)
    await db.flush()

    after = {
        "status": legacy.status,
        "paper_account_id": None if legacy.paper_account_id is None else str(legacy.paper_account_id),
        "exchange": legacy.exchange,
        "rollback_successor_campaign_id": str(request.canonical_campaign_id),
        "rollback_successor_campaign_version": request.canonical_campaign_version,
    }
    db.add(
        AuditLog(
            actor=request.actor,
            action="capital_campaign.transition_rollback",
            entity_type="capital_campaign",
            entity_id=legacy.uuid,
            before_state=before,
            after_state=after,
        )
    )

    return LegacyCampaignTransitionMutationResult(
        changed=True,
        idempotent=False,
        before=before,
        after=after,
        readiness=readiness,
        audit_created=True,
    )
