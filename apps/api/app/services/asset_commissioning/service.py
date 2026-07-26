from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.asset_commissioning_run import AssetCommissioningRun
from app.models.candle import Candle
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.live_trading_profile import LiveTradingProfile
from app.models.strategy_roster_run import StrategyRosterRun
from app.schemas.capital_campaign_domain import CapitalCampaignDraftCreateRequest
from app.services.capital_campaign_domain import create_campaign_draft, get_governing_campaign_definition
from app.services.capital_campaign_domain.repository import CapitalCampaignDomainRepository
from app.services.canonical_campaign_binding import (
    _MINIMUM_CANDLE_HISTORY_FOR_BINDING,
    CanonicalCampaignStatusTransitionRequest,
    transition_canonical_campaign_status,
)
from app.services.data.binance_client import NormalizedCandle
from app.services.data.candle_writer import upsert_candles
from app.services.data.http_client import AsyncHTTPClient
from app.services.data.kraken_client import KrakenClientError, KrakenSpotClient as KrakenMarketDataClient
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient as KrakenProviderClient
from app.services.mandates.contracts import MandateAuthorizationRequest, MandateVersionCreateRequest
from app.services.mandates.lifecycle import (
    authorize_mandate_version,
    create_mandate_version,
    get_governing_authorized_mandate_version,
    get_mandate,
)
from app.services.orchestration import asset_roster

logger = logging.getLogger(__name__)

_CANDLE_INTERVAL = "15m"
_MINIMUM_CANDLE_COUNT = _MINIMUM_CANDLE_HISTORY_FOR_BINDING
_BACKFILL_LOOKBACK = timedelta(hours=20)
_MARKET_DATA_FRESHNESS_MAX_AGE = timedelta(minutes=45)

_STAGE_PROVIDER_VERIFIED = "PROVIDER_VERIFIED"
_STAGE_ASSET_REGISTERED = "ASSET_REGISTERED"
_STAGE_MARKET_DATA_READY = "MARKET_DATA_READY"
_STAGE_CAMPAIGN_AUTHORIZED = "CAMPAIGN_AUTHORIZED"
_STAGE_MANDATE_SUCCESSOR_CREATED = "MANDATE_SUCCESSOR_CREATED"
_STAGE_MANDATE_AUTHORIZED_AND_PROMOTED = "MANDATE_AUTHORIZED_AND_PROMOTED"
_STAGE_RUNTIME_DISCOVERABLE = "RUNTIME_DISCOVERABLE"

_STAGE_ORDER = [
    _STAGE_PROVIDER_VERIFIED,
    _STAGE_ASSET_REGISTERED,
    _STAGE_MARKET_DATA_READY,
    _STAGE_CAMPAIGN_AUTHORIZED,
    _STAGE_MANDATE_SUCCESSOR_CREATED,
    _STAGE_MANDATE_AUTHORIZED_AND_PROMOTED,
    _STAGE_RUNTIME_DISCOVERABLE,
]

# Stages beyond this point are only ever attempted when the caller requests
# activate=True -- everything up to and including MANDATE_SUCCESSOR_CREATED
# and CAMPAIGN_AUTHORIZED is safe to run unconditionally (it grants no new
# trading authority by itself); MANDATE_AUTHORIZED_AND_PROMOTED is the single
# stage that actually promotes a new mandate version to governing, so it is
# the activation boundary.
_ACTIVATION_GATED_STAGES = {_STAGE_MANDATE_AUTHORIZED_AND_PROMOTED, _STAGE_RUNTIME_DISCOVERABLE}


@dataclass(slots=True)
class _StageResult:
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_product_id(product_id: str) -> str:
    return product_id.strip().upper().replace("/", "-")


def _is_fresh(observed_at: datetime | None) -> bool:
    if observed_at is None:
        return False
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return (_utcnow() - observed_at) <= _MARKET_DATA_FRESHNESS_MAX_AGE


def _known_product_symbols(product_id: str) -> tuple[str, ...] | None:
    """Reuses asset_roster's own hand-maintained symbol-alias table -- the same
    table the worker itself consults for candle lookups -- rather than
    inventing a second one. Returns None when the product has no known alias
    entry (BTC-USD's own constants count as known)."""
    if product_id == asset_roster.AUTONOMOUS_CYCLE_PRODUCT_ID:
        return asset_roster.AUTONOMOUS_CYCLE_ASSET_SYMBOLS
    return asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS.get(product_id)


def _canonical_symbol_for_product(product_id: str) -> str:
    return product_id.split("-")[0]


def _stage_snapshot(stage: str, result: _StageResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "evidence": result.evidence,
        "completed_at": _utcnow().isoformat() if result.status == "COMPLETED" else None,
        "error": result.error,
    }


# --- Stage 1: provider verification -----------------------------------------------

async def _stage_provider_verified(*, provider: str, product_id: str, environment: str) -> _StageResult:
    if provider != "kraken":
        return _StageResult(status="FAILED", error=f"unsupported_provider:{provider}")
    known_symbols = _known_product_symbols(product_id)
    if known_symbols is None:
        return _StageResult(
            status="FAILED",
            error=(
                f"product_not_in_known_roster_table:{product_id}; add it to "
                "asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS before commissioning"
            ),
        )
    client = KrakenProviderClient()
    product = await client.fetch_product(credentials={}, environment=environment, product_id=product_id)
    if not product.available or not product.trading_enabled:
        return _StageResult(
            status="FAILED",
            evidence={"available": product.available, "trading_enabled": product.trading_enabled},
            error="provider_reports_product_not_tradable",
        )
    return _StageResult(
        status="COMPLETED",
        evidence={
            "available": product.available,
            "trading_enabled": product.trading_enabled,
            "min_order_notional": str(product.min_order_notional),
            "quantity_increment": str(product.quantity_increment),
            "known_symbols": list(known_symbols),
        },
    )


# --- Stage 2: canonical asset creation (idempotent, mirrors scripts/commission_kraken_asset.py) --

async def _stage_asset_registered(*, db: AsyncSession, product_id: str, provider_evidence: dict[str, Any]) -> tuple[_StageResult, uuid.UUID | None]:
    canonical_symbol = _canonical_symbol_for_product(product_id)
    exchange = "kraken_spot"
    existing = await db.scalar(select(Asset).where(Asset.symbol == canonical_symbol).where(Asset.exchange == exchange))
    if existing is not None:
        return _StageResult(status="COMPLETED", evidence={"asset_id": str(existing.id), "created": False}), existing.id

    asset = Asset(
        symbol=canonical_symbol,
        asset_class="crypto",
        exchange=exchange,
        base_currency="USD",
        supports_fractional=True,
        min_order_notional=Decimal(provider_evidence["min_order_notional"]),
        qty_step_size=Decimal(provider_evidence["quantity_increment"]),
        is_active=True,
    )
    db.add(asset)
    await db.flush()
    return _StageResult(status="COMPLETED", evidence={"asset_id": str(asset.id), "created": True}), asset.id


# --- Stage 3: market-data backfill + freshness (mirrors scripts/backfill_historical.py) --

async def _stage_market_data_ready(*, db: AsyncSession, asset_id: uuid.UUID, product_id: str) -> _StageResult:
    count = (
        await db.execute(
            select(Candle.id).where(Candle.asset_id == asset_id, Candle.interval == _CANDLE_INTERVAL)
        )
    ).scalars().all()
    latest = await db.scalar(
        select(Candle.close_time)
        .where(Candle.asset_id == asset_id, Candle.interval == _CANDLE_INTERVAL)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    is_fresh = _is_fresh(latest)

    if len(count) >= _MINIMUM_CANDLE_COUNT and is_fresh:
        return _StageResult(
            status="COMPLETED",
            evidence={"candle_count": len(count), "latest_close_time": latest.isoformat() if latest else None, "backfilled": False},
        )

    end_time = _utcnow()
    start_time = end_time - _BACKFILL_LOOKBACK
    async with AsyncHTTPClient() as http_client:
        kraken_client = KrakenMarketDataClient(http_client)
        try:
            candles: list[NormalizedCandle] = await kraken_client.fetch_klines(
                symbol=product_id, interval=_CANDLE_INTERVAL, start_time=start_time, end_time=end_time,
            )
        except KrakenClientError as exc:
            return _StageResult(status="FAILED", error=f"candle_backfill_failed:{exc}")
    written = await upsert_candles(db, asset_id, _CANDLE_INTERVAL, candles)
    await db.flush()

    final_count = (
        await db.execute(
            select(Candle.id).where(Candle.asset_id == asset_id, Candle.interval == _CANDLE_INTERVAL)
        )
    ).scalars().all()
    final_latest = await db.scalar(
        select(Candle.close_time)
        .where(Candle.asset_id == asset_id, Candle.interval == _CANDLE_INTERVAL)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    final_fresh = _is_fresh(final_latest)
    if len(final_count) < _MINIMUM_CANDLE_COUNT or not final_fresh:
        return _StageResult(
            status="FAILED",
            evidence={"candle_count": len(final_count), "rows_written": written},
            error="insufficient_or_stale_candle_history_after_backfill",
        )
    return _StageResult(
        status="COMPLETED",
        evidence={
            "candle_count": len(final_count),
            "rows_written": written,
            "latest_close_time": final_latest.isoformat() if final_latest else None,
            "backfilled": True,
        },
    )


# --- Stage 4: campaign membership --------------------------------------------------
#
# Fully automated per Correction 1: when the product is missing from the
# governing campaign's allowed_instruments, clones the governing definition
# verbatim (via create_campaign_draft -- the existing campaign-domain
# lifecycle service, never a second one) with only allowed_instruments
# extended, then drives the DRAFT -> READY transition through
# transition_canonical_campaign_status -- the same governed transition
# canonical-campaign-status-transition-execute uses, including its real
# per-instrument safety checks. Never mutates the prior governing definition
# row; the successor is always a brand new, immutable version.
#
# GOVERNING vs LATEST-BY-VERSION-NUMBER, and why both are needed:
# create_campaign_draft() deliberately does NOT repin a currently-governing
# (READY) runtime's definition_version onto a brand new, unvalidated DRAFT
# successor -- that eager repin was the root of the original production-
# governance gap, and the fix lives in the reused platform primitive itself
# (app.services.capital_campaign_domain._ensure_runtime_campaign_pin), not
# as a workaround here. The runtime pin only ever moves as part of
# transition_canonical_campaign_status's own atomic, row-locked mutation,
# and only once that transition actually succeeds. Consequently:
#   - GOVERNING lookups (is this product *currently* authorized to trade)
#     use capital_campaign_domain.get_governing_campaign_definition, which
#     resolves strictly through the runtime's own pin (not "the highest
#     version number that happens to be READY"). This means the previously
#     governing version keeps resolving as governing for the entire time a
#     successor is being created and validated -- never nothing, never the
#     unvalidated successor. This is the same governing-membership lookup
#     the rest of the platform (including the worker's own roster
#     resolution) uses, not a second definition.
#   - RESUME detection (did a prior attempt already create a DRAFT successor
#     to continue from) needs the raw, pin-independent "latest version by
#     number" row -- exactly what CapitalCampaignDomainRepository.get()
#     (version=None) returns, reused directly since a DRAFT successor is, by
#     design, never what the governing lookup above can see.
# The clone source for a genuinely new successor is always the GOVERNING
# definition, never "latest by version number", so an abandoned/unrelated
# DRAFT can never be cloned from.

async def _stage_campaign_authorized(
    *, db: AsyncSession, campaign_id: uuid.UUID, product_id: str, actor: str, idempotency_key: str,
) -> _StageResult:
    governing = await get_governing_campaign_definition(db=db, campaign_id=campaign_id)
    if governing is not None and product_id in governing.allowed_instruments:
        return _StageResult(
            status="COMPLETED",
            evidence={
                "source_campaign_version": governing.version,
                "successor_campaign_version": governing.version,
                "mutation_required": False,
            },
        )

    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))
    if runtime is None:
        return _StageResult(status="FAILED", error="runtime_campaign_not_found")

    live_profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == runtime.paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc())
        .limit(1)
    )
    if live_profile is None:
        return _StageResult(status="FAILED", error="live_trading_profile_not_found_for_paper_account")

    # Resume detection only: does a prior attempt already have a DRAFT
    # successor with the product added? Deliberately the raw,
    # pin-independent repository lookup -- get_governing_campaign_definition
    # can never see a DRAFT successor by design, and get_campaign_definition
    # would now raise (it requires the runtime to already be pinned to
    # whatever it returns, which an unpromoted successor never is).
    latest_row = await CapitalCampaignDomainRepository(db).get(campaign_id=campaign_id, version=None)
    resuming = (
        latest_row is not None
        and latest_row.status == "DRAFT"
        and product_id in (latest_row.allowed_instruments or [])
    )
    definition = latest_row if resuming else governing
    if definition is None:
        return _StageResult(status="FAILED", error="no_governing_campaign_definition_to_clone_from")

    before_evidence = {
        "campaign_id": str(campaign_id), "source_campaign_version": definition.version,
        "source_allowed_instruments": list(definition.allowed_instruments), "source_status": definition.status,
    }

    if product_id not in definition.allowed_instruments:
        new_allowed_instruments = list(definition.allowed_instruments) + [product_id]
        try:
            created = await create_campaign_draft(
                db=db,
                request=CapitalCampaignDraftCreateRequest(
                    campaign_id=campaign_id,
                    name=definition.name, description=definition.description, owner_identity=definition.owner_identity,
                    status="DRAFT",
                    capital_budget=definition.capital_budget,
                    remaining_unallocated_capital=definition.remaining_unallocated_capital,
                    base_currency=definition.base_currency,
                    allowed_asset_classes=list(definition.allowed_asset_classes),
                    allowed_venues=list(definition.allowed_venues),
                    allowed_instruments=new_allowed_instruments,
                    campaign_modes=list(definition.campaign_modes),
                    maximum_open_positions=definition.maximum_open_positions,
                    maximum_position_size=definition.maximum_position_size,
                    minimum_position_size=definition.minimum_position_size,
                    maximum_total_exposure=definition.maximum_total_exposure,
                    profitability_policy_id=definition.profitability_policy_id,
                    profitability_policy_version=definition.profitability_policy_version,
                    risk_policy_id=definition.risk_policy_id,
                    risk_policy_version=definition.risk_policy_version,
                    compounding_policy=definition.compounding_policy,
                    profit_distribution_policy=definition.profit_distribution_policy,
                    aggression_mode=definition.aggression_mode,
                    # Preserves the campaign's actual current runtime equity/P&L
                    # (get_governing_campaign_definition already derives
                    # accounting_state from the live runtime row, not a static
                    # snapshot) -- omitting this would silently reset
                    # accounting to a fresh-start state, per
                    # _resolve_accounting_state's default-when-None behavior.
                    accounting_state=definition.accounting_state,
                    metadata_evidence=dict(definition.metadata_evidence),
                    non_live_only=True,
                ),
            )
        except InvalidRequestError as exc:
            return _StageResult(status="FAILED", evidence=before_evidence, error=f"campaign_successor_creation_failed:{exc}")
        for field_name in ("capital_budget", "base_currency", "maximum_open_positions", "maximum_position_size", "minimum_position_size", "maximum_total_exposure", "profitability_policy_id", "risk_policy_id", "aggression_mode"):
            if getattr(created, field_name) != getattr(definition, field_name):
                raise InvalidRequestError(
                    message="campaign successor cloning invariant violated",
                    details={"field": field_name, "before": str(getattr(definition, field_name)), "after": str(getattr(created, field_name))},
                )
        if set(definition.allowed_instruments) - set(created.allowed_instruments):
            raise InvalidRequestError(
                message="campaign successor dropped a previously authorized instrument",
                details={"missing": sorted(set(definition.allowed_instruments) - set(created.allowed_instruments))},
            )
        successor_version = created.version
        runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))
    else:
        # Resuming: a successor already exists (from a prior partial attempt)
        # with the product already added, still DRAFT -- only the transition
        # step remains.
        successor_version = definition.version

    transition_request = CanonicalCampaignStatusTransitionRequest(
        campaign_id=campaign_id, campaign_version=successor_version, runtime_campaign_id=runtime.id,
        expected_current_status="DRAFT", target_status="READY",
        paper_account_id=runtime.paper_account_id, live_trading_profile_id=live_profile.id,
        provider="kraken_spot", environment="production", product_id=asset_roster.AUTONOMOUS_CYCLE_PRODUCT_ID,
        actor=actor, idempotency_key=f"{idempotency_key}:campaign-transition", confirm=True,
    )
    try:
        transition_result = await transition_canonical_campaign_status(db=db, request=transition_request)
    except (PermissionError, LookupError) as exc:
        return _StageResult(
            status="FAILED",
            evidence={**before_evidence, "successor_campaign_version": successor_version},
            error=f"campaign_transition_failed:{exc}",
        )

    return _StageResult(
        status="COMPLETED",
        evidence={
            **before_evidence,
            "successor_campaign_version": successor_version,
            "transition_before": transition_result.before,
            "transition_after": transition_result.after,
            "mutation_required": True,
        },
    )


# --- Stage 5: mandate successor version (clone-and-extend, never invents limits) --
#
# Governing-version resolution is centralized in
# app.services.mandates.lifecycle.get_governing_authorized_mandate_version --
# the exact function backing production activation/promotion -- rather than a
# second, locally re-derived definition of "governing".


_CLONED_NUMERIC_FIELDS = (
    "authorized_capital_usd",
    "max_order_notional_usd",
    "max_open_exposure_usd",
    "max_daily_deployed_usd",
    "max_daily_realized_loss_usd",
    "max_campaign_drawdown_usd",
    "max_consecutive_losses",
    "position_limit",
    "price_evidence_max_age_seconds",
    "max_slippage_bps",
    "max_fee_bps",
)


async def _stage_mandate_successor_created(
    *, db: AsyncSession, mandate_id: uuid.UUID, product_id: str, actor: str, idempotency_key: str,
) -> tuple[_StageResult, uuid.UUID | None]:
    mandate = await get_mandate(db=db, mandate_id=mandate_id)
    current = await get_governing_authorized_mandate_version(db=db, mandate_id=mandate_id)
    if current is None:
        return _StageResult(status="FAILED", error="no_active_authorized_governing_mandate_version_found"), None

    if product_id in current.allowed_products:
        return _StageResult(
            status="COMPLETED",
            evidence={"mandate_version_id": str(current.mandate_version_id), "mutation_required": False},
        ), current.mandate_version_id

    new_allowed_products = tuple(current.allowed_products) + (product_id,)
    request = MandateVersionCreateRequest(
        mandate_id=mandate.mandate_id,
        actor=actor,
        base_currency=current.base_currency,
        authorized_capital_usd=current.authorized_capital_usd,
        max_order_notional_usd=current.max_order_notional_usd,
        max_open_exposure_usd=current.max_open_exposure_usd,
        max_daily_deployed_usd=current.max_daily_deployed_usd,
        max_daily_realized_loss_usd=current.max_daily_realized_loss_usd,
        max_campaign_drawdown_usd=current.max_campaign_drawdown_usd,
        max_consecutive_losses=current.max_consecutive_losses,
        position_limit=current.position_limit,
        price_evidence_max_age_seconds=current.price_evidence_max_age_seconds,
        max_slippage_bps=current.max_slippage_bps,
        max_fee_bps=current.max_fee_bps,
        allowed_products=new_allowed_products,
        allowed_order_sides=tuple(current.allowed_order_sides),
        allowed_strategy_versions=tuple(current.allowed_strategy_versions),
        entry_policy=dict(current.entry_policy),
        exit_policy=dict(current.exit_policy),
        cooldown_policy=dict(current.cooldown_policy),
        operating_schedule=dict(current.operating_schedule),
        approval_policy=current.approval_policy,
        reconciliation_policy=dict(current.reconciliation_policy),
        kill_switch_policy=dict(current.kill_switch_policy),
        owner_acknowledgements={
            "generated_by": "asset_commissioning_service",
            "action": f"add {product_id} to mandate {mandate_id} allowed_products",
            "cloned_from_mandate_version_id": str(current.mandate_version_id),
            "actor": actor,
        },
        authorization_evidence_summary={
            "generated_by": "asset_commissioning_service",
            "note": "every numeric/risk field cloned verbatim from the prior governing version; only allowed_products changed",
        },
        idempotency_key=f"{idempotency_key}:mandate-version",
    )
    new_version = await create_mandate_version(db=db, request=request)

    # Fail closed if the clone somehow diverged on any preserved limit -- this
    # must never happen, but an assertion here is the difference between a
    # loud failure and a silent capital-limit change.
    for field_name in _CLONED_NUMERIC_FIELDS:
        if getattr(new_version, field_name) != getattr(current, field_name):
            raise InvalidRequestError(
                message="mandate successor cloning invariant violated",
                details={"field": field_name, "before": str(getattr(current, field_name)), "after": str(getattr(new_version, field_name))},
            )
    if set(current.allowed_products) - set(new_version.allowed_products):
        raise InvalidRequestError(
            message="mandate successor dropped a previously authorized product",
            details={"missing": sorted(set(current.allowed_products) - set(new_version.allowed_products))},
        )

    return _StageResult(
        status="COMPLETED",
        evidence={
            "mandate_version_id": str(new_version.mandate_version_id),
            "version_number": new_version.version_number,
            "allowed_products": list(new_version.allowed_products),
            "mutation_required": True,
        },
    ), new_version.mandate_version_id


# --- Stage 6: authorize + promote (activation-gated) --------------------------------

async def _stage_mandate_authorized_and_promoted(
    *, db: AsyncSession, mandate_id: uuid.UUID, mandate_version_id: uuid.UUID, product_id: str, actor: str, idempotency_key: str,
) -> _StageResult:
    version = await db.get(AutonomousCapitalMandateVersion, mandate_version_id)
    if version is not None and version.is_active and version.is_authorized:
        return _StageResult(
            status="COMPLETED",
            evidence={"mandate_version_id": str(mandate_version_id), "mutation_required": False},
        )
    request = MandateAuthorizationRequest(
        mandate_id=mandate_id,
        mandate_version_id=mandate_version_id,
        actor=actor,
        authorization_method="asset_commissioning_service:automated",
        owner_acknowledgements={
            "generated_by": "asset_commissioning_service",
            "action": f"authorize and promote mandate version {mandate_version_id} to add {product_id}",
            "actor": actor,
        },
        authorization_evidence=({
            "generated_by": "asset_commissioning_service",
            "note": "numeric/risk limits verified unchanged from prior governing version at MANDATE_SUCCESSOR_CREATED stage",
        }),
        deterministic_explanation={
            "generated_by": "asset_commissioning_service",
            "rule": "authorize_mandate_version() promotes automatically when mandate.status == ACTIVE",
        },
        expires_at=None,
        idempotency_key=f"{idempotency_key}:authorize",
        audit_correlation_id=None,
    )
    authorization = await authorize_mandate_version(db=db, request=request)
    return _StageResult(
        status="COMPLETED",
        evidence={
            "mandate_authorization_id": str(authorization.mandate_authorization_id),
            "approval_result": authorization.approval_result,
            "mutation_required": True,
        },
    )


# --- Stage 7: runtime discoverability (activation-gated) ----------------------------

async def _stage_runtime_discoverable(*, db: AsyncSession, product_id: str) -> _StageResult:
    settings = get_settings()
    if getattr(settings, "asset_discovery_mode", "env") == "campaign_db":
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(db=db, settings=settings)
        if product_id in products:
            return _StageResult(status="COMPLETED", evidence={"mode": "campaign_db", "resolved_products": products, "mutation_required": False})
        return _StageResult(
            status="FAILED",
            evidence={"mode": "campaign_db", "resolved_products": products},
            error="product_not_yet_resolved_by_campaign_db_discovery -- verify campaign/asset state and retry",
        )
    return _StageResult(
        status="FAILED",
        evidence={"mode": "env"},
        error=(
            "asset_discovery_mode is 'env': set AUTONOMOUS_CYCLE_ADDITIONAL_PRODUCTS to include "
            f"{product_id} and restart omnitrade-orchestration.service manually -- not automated by this service"
        ),
    )


# --- Preview: read-only, never mutates ---------------------------------------------

async def preview_asset_commissioning(
    *, db: AsyncSession, provider: str, product_id: str, campaign_id: uuid.UUID, environment: str,
) -> dict[str, Any]:
    product_id = _normalize_product_id(product_id)
    blockers: list[str] = []
    expected_changes: list[str] = []
    plan: list[str] = []

    known_symbols = _known_product_symbols(product_id)
    provider_supported = False
    provider_evidence: dict[str, Any] = {}
    if provider != "kraken":
        blockers.append(f"unsupported_provider:{provider}")
    elif known_symbols is None:
        blockers.append(f"product_not_in_known_roster_table:{product_id}")
    else:
        client = KrakenProviderClient()
        product = await client.fetch_product(credentials={}, environment=environment, product_id=product_id)
        provider_supported = bool(product.available and product.trading_enabled)
        provider_evidence = {
            "min_order_notional": str(product.min_order_notional),
            "quantity_increment": str(product.quantity_increment),
        }
        if not provider_supported:
            blockers.append("provider_reports_product_not_tradable")
        else:
            plan.append(f"PROVIDER_VERIFIED: {product_id} tradable on {provider}/{environment}")

    canonical_symbol = _canonical_symbol_for_product(product_id)
    existing_asset = await db.scalar(select(Asset).where(Asset.symbol == canonical_symbol).where(Asset.exchange == "kraken_spot"))
    asset_registered = existing_asset is not None
    candle_count = 0
    market_data_current = False
    if existing_asset is not None:
        rows = (await db.execute(
            select(Candle.id).where(Candle.asset_id == existing_asset.id, Candle.interval == _CANDLE_INTERVAL)
        )).scalars().all()
        candle_count = len(rows)
        latest = await db.scalar(
            select(Candle.close_time).where(Candle.asset_id == existing_asset.id, Candle.interval == _CANDLE_INTERVAL)
            .order_by(Candle.open_time.desc()).limit(1)
        )
        market_data_current = _is_fresh(latest)
        plan.append(f"ASSET_REGISTERED: reuse existing asset_id={existing_asset.id}")
    else:
        expected_changes.append(f"create Asset row for {canonical_symbol}/kraken_spot")
        plan.append("ASSET_REGISTERED: create new canonical asset row")
    if candle_count < _MINIMUM_CANDLE_COUNT or not market_data_current:
        expected_changes.append(f"backfill {_CANDLE_INTERVAL} candles to reach >= {_MINIMUM_CANDLE_COUNT} and current freshness")
        plan.append("MARKET_DATA_READY: backfill required")
    else:
        plan.append("MARKET_DATA_READY: already satisfied")

    governing_campaign = await get_governing_campaign_definition(db=db, campaign_id=campaign_id)
    if governing_campaign is None:
        blockers.append("no_governing_campaign_definition_found")
        campaign_mutation_required = True
        plan.append("CAMPAIGN_AUTHORIZED: no currently-governing (READY) campaign version found")
    else:
        campaign_mutation_required = product_id not in governing_campaign.allowed_instruments
        if campaign_mutation_required:
            expected_changes.append(
                f"create a successor campaign definition version adding {product_id} to allowed_instruments "
                "(every other field cloned unchanged) and transition it DRAFT -> READY via the existing governed "
                "canonical campaign transition"
            )
            plan.append("CAMPAIGN_AUTHORIZED: successor campaign version + governed transition required (automated)")
        else:
            plan.append("CAMPAIGN_AUTHORIZED: already satisfied")

    settings = get_settings()
    mandate_id = settings.automatic_mandate_package_activation_mandate_id
    preserved_constraints: dict[str, Any] = {}
    mandate_successor_required = True
    if mandate_id is None:
        blockers.append("no automatic_mandate_package_activation_mandate_id configured")
    else:
        governing = await get_governing_authorized_mandate_version(db=db, mandate_id=mandate_id)
        if governing is None:
            blockers.append("no active authorized governing mandate version found")
        else:
            mandate_successor_required = product_id not in governing.allowed_products
            preserved_constraints = {field_name: str(getattr(governing, field_name)) for field_name in _CLONED_NUMERIC_FIELDS}
            preserved_constraints["allowed_products"] = list(governing.allowed_products)
            if mandate_successor_required:
                expected_changes.append(f"create successor mandate version adding {product_id} to allowed_products, all limits preserved")
                plan.append("MANDATE_SUCCESSOR_CREATED + MANDATE_AUTHORIZED_AND_PROMOTED: required (activate=true only)")
            else:
                plan.append("MANDATE_SUCCESSOR_CREATED: already satisfied")

    runtime_discovery_mutation_required = getattr(settings, "asset_discovery_mode", "env") != "campaign_db"
    if runtime_discovery_mutation_required:
        expected_changes.append("manual AUTONOMOUS_CYCLE_ADDITIONAL_PRODUCTS update + service restart still required (env discovery mode)")
        plan.append("RUNTIME_DISCOVERABLE: manual step required")
    else:
        plan.append("RUNTIME_DISCOVERABLE: automatic once campaign+asset conditions are met")

    return {
        "provider": provider,
        "canonical_product_id": product_id,
        "provider_symbol": product_id,
        "provider_supported": provider_supported,
        "provider_evidence": provider_evidence,
        "asset_registered": asset_registered,
        "asset_id": existing_asset.id if existing_asset is not None else None,
        "candle_count": candle_count,
        "candle_count_required": _MINIMUM_CANDLE_COUNT,
        "market_data_current": market_data_current,
        "campaign_mutation_required": campaign_mutation_required,
        "mandate_successor_required": mandate_successor_required,
        "preserved_risk_constraints": preserved_constraints,
        "runtime_discovery_mutation_required": runtime_discovery_mutation_required,
        "expected_changes": expected_changes,
        "blockers": blockers,
        "plan": plan,
    }


# --- Commission: mutating, idempotent, resumable, fail-closed ----------------------

async def commission_asset(
    *, db: AsyncSession, provider: str, product_id: str, campaign_id: uuid.UUID, environment: str,
    activate: bool, idempotency_key: str, actor: str,
) -> AssetCommissioningRun:
    product_id = _normalize_product_id(product_id)
    run = await db.scalar(select(AssetCommissioningRun).where(AssetCommissioningRun.idempotency_key == idempotency_key))
    if run is None:
        run = AssetCommissioningRun(
            provider=provider, product_id=product_id, campaign_id=campaign_id, environment=environment,
            actor=actor, idempotency_key=idempotency_key, activate=activate, status="IN_PROGRESS", stages={},
        )
        db.add(run)
        await db.flush()
    elif run.status == "COMPLETED":
        return run
    elif run.provider != provider or run.product_id != product_id or run.campaign_id != campaign_id or run.environment != environment:
        raise InvalidRequestError(
            message="idempotency_key reused with different commissioning parameters",
            details={"commissioning_id": str(run.commissioning_id)},
        )

    stages = dict(run.stages)
    settings = get_settings()
    mandate_id = settings.automatic_mandate_package_activation_mandate_id

    try:
        provider_evidence: dict[str, Any] = stages.get(_STAGE_PROVIDER_VERIFIED, {}).get("evidence", {})
        if stages.get(_STAGE_PROVIDER_VERIFIED, {}).get("status") != "COMPLETED":
            result = await _stage_provider_verified(provider=provider, product_id=product_id, environment=environment)
            stages[_STAGE_PROVIDER_VERIFIED] = _stage_snapshot(_STAGE_PROVIDER_VERIFIED, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="provider verification failed", details={"error": result.error})
            provider_evidence = result.evidence

        asset_id = run.asset_id
        if stages.get(_STAGE_ASSET_REGISTERED, {}).get("status") != "COMPLETED":
            result, asset_id = await _stage_asset_registered(db=db, product_id=product_id, provider_evidence=provider_evidence)
            stages[_STAGE_ASSET_REGISTERED] = _stage_snapshot(_STAGE_ASSET_REGISTERED, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="asset registration failed", details={"error": result.error})
            run.asset_id = asset_id
            await db.flush()

        if stages.get(_STAGE_MARKET_DATA_READY, {}).get("status") != "COMPLETED":
            result = await _stage_market_data_ready(db=db, asset_id=asset_id, product_id=product_id)
            stages[_STAGE_MARKET_DATA_READY] = _stage_snapshot(_STAGE_MARKET_DATA_READY, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="market data readiness failed", details={"error": result.error})

        if stages.get(_STAGE_CAMPAIGN_AUTHORIZED, {}).get("status") != "COMPLETED":
            result = await _stage_campaign_authorized(
                db=db, campaign_id=campaign_id, product_id=product_id, actor=actor, idempotency_key=idempotency_key,
            )
            stages[_STAGE_CAMPAIGN_AUTHORIZED] = _stage_snapshot(_STAGE_CAMPAIGN_AUTHORIZED, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="campaign authorization failed", details={"error": result.error})

        if not activate:
            run.status = "IN_PROGRESS"
            run.stages = stages
            run.updated_at = _utcnow()
            await db.flush()
            return run

        if mandate_id is None:
            raise InvalidRequestError(message="no automatic_mandate_package_activation_mandate_id configured")

        mandate_version_id = run.mandate_version_id
        if stages.get(_STAGE_MANDATE_SUCCESSOR_CREATED, {}).get("status") != "COMPLETED":
            result, mandate_version_id = await _stage_mandate_successor_created(
                db=db, mandate_id=mandate_id, product_id=product_id, actor=actor, idempotency_key=idempotency_key,
            )
            stages[_STAGE_MANDATE_SUCCESSOR_CREATED] = _stage_snapshot(_STAGE_MANDATE_SUCCESSOR_CREATED, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="mandate successor creation failed", details={"error": result.error})
            run.mandate_version_id = mandate_version_id
            await db.flush()

        if stages.get(_STAGE_MANDATE_AUTHORIZED_AND_PROMOTED, {}).get("status") != "COMPLETED":
            result = await _stage_mandate_authorized_and_promoted(
                db=db, mandate_id=mandate_id, mandate_version_id=mandate_version_id, product_id=product_id,
                actor=actor, idempotency_key=idempotency_key,
            )
            stages[_STAGE_MANDATE_AUTHORIZED_AND_PROMOTED] = _stage_snapshot(_STAGE_MANDATE_AUTHORIZED_AND_PROMOTED, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="mandate authorization/promotion failed", details={"error": result.error})

        if stages.get(_STAGE_RUNTIME_DISCOVERABLE, {}).get("status") != "COMPLETED":
            result = await _stage_runtime_discoverable(db=db, product_id=product_id)
            stages[_STAGE_RUNTIME_DISCOVERABLE] = _stage_snapshot(_STAGE_RUNTIME_DISCOVERABLE, result)
            if result.status != "COMPLETED":
                raise InvalidRequestError(message="runtime discoverability failed", details={"error": result.error})

        run.status = "COMPLETED"
        run.stages = stages
        run.updated_at = _utcnow()
        await db.commit()
        return run
    except Exception as exc:
        run.status = "FAILED"
        run.stages = stages
        run.failure_reason = str(exc)
        run.updated_at = _utcnow()
        await db.commit()
        raise


# --- Status ------------------------------------------------------------------------

async def get_commissioning_status(*, db: AsyncSession, commissioning_id: uuid.UUID) -> AssetCommissioningRun:
    run = await db.get(AssetCommissioningRun, commissioning_id)
    if run is None:
        raise NotFoundError(message="commissioning run not found", details={"commissioning_id": str(commissioning_id)})
    return run


# --- Readiness -----------------------------------------------------------------------

async def get_asset_readiness(*, db: AsyncSession, product_id: str, campaign_id: uuid.UUID) -> dict[str, Any]:
    product_id = _normalize_product_id(product_id)
    blockers: list[str] = []
    warnings: list[str] = []

    known_symbols = _known_product_symbols(product_id)
    provider_supported = known_symbols is not None

    canonical_symbol = _canonical_symbol_for_product(product_id)
    asset = await db.scalar(select(Asset).where(Asset.symbol == canonical_symbol).where(Asset.exchange == "kraken_spot"))
    asset_registered = asset is not None and asset.is_active

    candle_count = 0
    market_data_current = False
    if asset is not None:
        rows = (await db.execute(
            select(Candle.id).where(Candle.asset_id == asset.id, Candle.interval == _CANDLE_INTERVAL)
        )).scalars().all()
        candle_count = len(rows)
        latest = await db.scalar(
            select(Candle.close_time).where(Candle.asset_id == asset.id, Candle.interval == _CANDLE_INTERVAL)
            .order_by(Candle.open_time.desc()).limit(1)
        )
        market_data_current = _is_fresh(latest)

    governing_campaign = await get_governing_campaign_definition(db=db, campaign_id=campaign_id)
    campaign_authorized = governing_campaign is not None and product_id in governing_campaign.allowed_instruments

    settings = get_settings()
    mandate_id = settings.automatic_mandate_package_activation_mandate_id
    mandate_authorized = False
    if mandate_id is not None:
        governing = await get_governing_authorized_mandate_version(db=db, mandate_id=mandate_id)
        mandate_authorized = governing is not None and product_id in governing.allowed_products

    runtime_selected = False
    if getattr(settings, "asset_discovery_mode", "env") == "campaign_db":
        products = await asset_roster.resolve_autonomous_cycle_products_from_campaign(db=db, settings=settings)
        runtime_selected = product_id in products
    else:
        parsed = settings.parsed_autonomous_cycle_additional_products
        runtime_selected = product_id in parsed or product_id == asset_roster.AUTONOMOUS_CYCLE_PRODUCT_ID

    strategy_evaluation_observed = False
    if asset is not None:
        roster_run = await db.scalar(
            select(StrategyRosterRun.roster_run_id)
            .where(StrategyRosterRun.asset_id == asset.id, StrategyRosterRun.product_id == product_id)
            .limit(1)
        )
        strategy_evaluation_observed = roster_run is not None

    if not provider_supported:
        blockers.append("provider_not_supported_or_not_in_known_roster_table")
    if not asset_registered:
        blockers.append("asset_not_registered")
    if candle_count < _MINIMUM_CANDLE_COUNT:
        blockers.append(f"insufficient_candle_history:{candle_count}/{_MINIMUM_CANDLE_COUNT}")
    elif not market_data_current:
        warnings.append("candle_history_present_but_stale")
    if not campaign_authorized:
        blockers.append("campaign_does_not_authorize_product")
    if not mandate_authorized:
        blockers.append("governing_mandate_does_not_authorize_product")
    if not runtime_selected:
        blockers.append("runtime_does_not_currently_select_product")
    if not strategy_evaluation_observed:
        blockers.append("no_strategy_roster_run_observed_for_this_asset_yet")

    live_execution_eligible = not blockers
    overall_status = "READY" if live_execution_eligible else "NOT_READY"

    return {
        "product_id": product_id,
        "provider_supported": provider_supported,
        "asset_registered": asset_registered,
        "market_data_current": market_data_current,
        "candle_count": candle_count,
        "campaign_authorized": campaign_authorized,
        "mandate_authorized": mandate_authorized,
        "runtime_selected": runtime_selected,
        "strategy_evaluation_observed": strategy_evaluation_observed,
        "live_execution_eligible": live_execution_eligible,
        "blockers": blockers,
        "warnings": warnings,
        "overall_status": overall_status,
    }
