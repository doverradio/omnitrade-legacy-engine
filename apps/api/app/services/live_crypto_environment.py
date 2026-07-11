from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.asset import Asset
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.exchange_connection import ExchangeConnection
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.schemas.capital_campaigns import CapitalCampaignCreateRequest
from app.schemas.crypto_order_previews import CryptoOrderPreviewCreateRequest
from app.schemas.exchange_connections import SaveExchangeConnectionRequest
from app.services.assets_service import EnsureCoinbaseAssetRequest, ensure_coinbase_crypto_asset
from app.services.capital_campaigns.service import create_capital_campaign
from app.services.crypto_order_previews.service import create_crypto_order_preview
from app.services.exchange_connections.service import create_exchange_connection, refresh_exchange_balances
from app.services.live.approval import record_live_approval_checkpoint
from app.services.live.contracts import LiveAccountRegistrationRequest, LiveApprovalCheckpointRequest
from app.services.live.registration import register_live_account


DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
_READY_CONNECTION_VERDICTS = {"READY_FOR_PREVIEW", "READY_FOR_DRY_RUN", "READY_FOR_OPERATOR_REVIEW"}


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    key: str
    label: str
    ready: bool
    detail: str


@dataclass(frozen=True, slots=True)
class LiveCryptoEnvironmentReadiness:
    ready: bool
    exchange_connection_id: UUID | None
    live_trading_profile_id: UUID | None
    paper_account_id: UUID | None
    capital_campaign_id: int | None
    crypto_order_preview_id: UUID | None
    approval_event_id: UUID | None
    items: tuple[ReadinessItem, ...]


@dataclass(frozen=True, slots=True)
class InitializeLiveCryptoEnvironmentRequest:
    actor: str
    paper_account_id: UUID = DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID
    exchange_environment: str = "production"
    exchange_connection_name: str = "coinbase-production-primary"
    exchange_api_key_name: str | None = None
    exchange_private_key: str | None = None
    exchange_passphrase: str | None = None
    registration_source: str = "human_production_initializer"
    campaign_owner: str = "operator"


@dataclass(frozen=True, slots=True)
class InitializeLiveCryptoEnvironmentResult:
    created_exchange_connection: bool
    created_asset: bool
    created_live_trading_profile: bool
    created_capital_campaign: bool
    readiness: LiveCryptoEnvironmentReadiness


@dataclass(frozen=True, slots=True)
class GeneratePreviewHelperRequest:
    actor: str
    exchange_connection_id: UUID


@dataclass(frozen=True, slots=True)
class GeneratePreviewHelperResult:
    crypto_order_preview_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class RecordApprovalHelperRequest:
    actor: str
    live_trading_profile_id: UUID


@dataclass(frozen=True, slots=True)
class RecordApprovalHelperResult:
    approval_event_id: UUID
    approval_state: str


async def _load_coinbase_connection(*, db: AsyncSession, environment: str) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == "coinbase_advanced")
        .where(ExchangeConnection.environment == environment)
        .order_by(ExchangeConnection.created_at.desc())
        .limit(1)
    )


async def _load_selected_crypto_paper_account(*, db: AsyncSession, paper_account_id: UUID) -> PaperAccount | None:
    paper_account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.id == paper_account_id)
        .limit(1)
    )
    if paper_account is None:
        return None
    if paper_account.asset_class != "crypto":
        raise ValueError(f"Selected paper account is not crypto: {paper_account_id}")
    if not bool(paper_account.is_active):
        raise ValueError(f"Selected paper account is inactive: {paper_account_id}")
    return paper_account


async def _load_live_profile_for_account(*, db: AsyncSession, paper_account_id: UUID | None) -> LiveTradingProfile | None:
    if paper_account_id is None:
        return None
    return await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc())
        .limit(1)
    )


async def _load_coinbase_btc_asset(*, db: AsyncSession) -> Asset | None:
    return await db.scalar(
        select(Asset)
        .where(Asset.symbol == "BTC")
        .where(Asset.asset_class == "crypto")
        .where(Asset.exchange == "coinbase_advanced")
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc())
        .limit(1)
    )


async def _load_campaign_for_account(*, db: AsyncSession, paper_account_id: UUID | None) -> CapitalCampaign | None:
    if paper_account_id is None:
        return None
    return await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.paper_account_id == paper_account_id)
        .where(CapitalCampaign.exchange == "coinbase_advanced")
        .order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
        .limit(1)
    )


async def _load_latest_preview(*, db: AsyncSession, exchange_connection_id: UUID | None) -> CryptoOrderPreview | None:
    if exchange_connection_id is None:
        return None
    return await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.exchange_connection_id == exchange_connection_id)
        .where(CryptoOrderPreview.product_id == "BTC-USD")
        .order_by(CryptoOrderPreview.created_at.desc())
        .limit(1)
    )


async def _load_latest_approval(*, db: AsyncSession, live_trading_profile_id: UUID | None) -> LiveApprovalEvent | None:
    if live_trading_profile_id is None:
        return None
    return await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
        .where(LiveApprovalEvent.checkpoint_type == "first_live_enablement")
        .where(LiveApprovalEvent.approval_state == "approved")
        .order_by(LiveApprovalEvent.sequence_number.desc())
        .limit(1)
    )


def _is_approval_active(approval: LiveApprovalEvent | None, *, now: datetime) -> bool:
    if approval is None:
        return False
    if approval.expires_at is None:
        return True
    return approval.expires_at > now


def _dry_run_gate_ready() -> tuple[bool, str]:
    settings = get_settings()
    if settings.live_crypto_order_submission_enabled:
        return False, "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false"
    if not settings.live_crypto_dry_run_enabled:
        return False, "LIVE_CRYPTO_DRY_RUN_ENABLED must be true"
    if not settings.live_crypto_preparation_enabled:
        return False, "LIVE_CRYPTO_PREPARATION_ENABLED must be true"
    if Decimal(str(settings.live_crypto_max_order_usd)) != Decimal("5"):
        return False, "LIVE_CRYPTO_MAX_ORDER_USD must equal 5"
    return True, "dry-run guard configuration is valid"


async def inspect_live_crypto_environment(
    *,
    db: AsyncSession,
    exchange_environment: str = "production",
    paper_account_id: UUID = DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID,
) -> LiveCryptoEnvironmentReadiness:
    now = datetime.now(timezone.utc)
    connection = await _load_coinbase_connection(db=db, environment=exchange_environment)
    paper_account = await _load_selected_crypto_paper_account(db=db, paper_account_id=paper_account_id)
    profile = await _load_live_profile_for_account(db=db, paper_account_id=paper_account.id if paper_account is not None else None)
    asset = await _load_coinbase_btc_asset(db=db)
    campaign = await _load_campaign_for_account(db=db, paper_account_id=paper_account.id if paper_account is not None else None)
    preview = await _load_latest_preview(db=db, exchange_connection_id=connection.exchange_connection_id if connection is not None else None)
    approval = await _load_latest_approval(db=db, live_trading_profile_id=profile.id if profile is not None else None)

    dry_run_ready, dry_run_detail = _dry_run_gate_ready()
    approval_ready = _is_approval_active(approval, now=now)

    items: list[ReadinessItem] = [
        ReadinessItem(
            key="database",
            label="Database",
            ready=True,
            detail="Database ready",
        ),
        ReadinessItem(
            key="exchange_connection",
            label="Exchange",
            ready=connection is not None,
            detail=(
                f"Coinbase production connection ready ({connection.exchange_connection_id})"
                if connection is not None
                else "Coinbase production connection missing"
            ),
        ),
        ReadinessItem(
            key="live_trading_profile",
            label="Trading Profile",
            ready=profile is not None,
            detail=(
                f"Live trading profile ready ({profile.id})"
                if profile is not None
                else "Live trading profile missing"
            ),
        ),
        ReadinessItem(
            key="capital_campaign",
            label="Campaign",
            ready=campaign is not None,
            detail=(
                f"Capital campaign ready ({campaign.uuid})"
                if campaign is not None
                else "Capital campaign missing"
            ),
        ),
        ReadinessItem(
            key="asset",
            label="Asset",
            ready=asset is not None,
            detail=(
                f"Coinbase BTC asset ready ({asset.id})"
                if asset is not None
                else "Coinbase BTC asset missing"
            ),
        ),
        ReadinessItem(
            key="preview",
            label="Preview",
            ready=preview is not None,
            detail=(
                f"Crypto order preview available ({preview.crypto_order_preview_id})"
                if preview is not None
                else "Crypto order preview missing"
            ),
        ),
        ReadinessItem(
            key="approval",
            label="Approval",
            ready=approval_ready,
            detail=(
                f"First live enablement approval ready ({approval.id})"
                if approval_ready and approval is not None
                else "First live enablement approval missing or expired"
            ),
        ),
        ReadinessItem(
            key="dry_run",
            label="Dry Run",
            ready=dry_run_ready,
            detail=dry_run_detail,
        ),
    ]

    overall_ready = all(item.ready for item in items)
    return LiveCryptoEnvironmentReadiness(
        ready=overall_ready,
        exchange_connection_id=None if connection is None else connection.exchange_connection_id,
        live_trading_profile_id=None if profile is None else profile.id,
        paper_account_id=None if paper_account is None else paper_account.id,
        capital_campaign_id=None if campaign is None else campaign.id,
        crypto_order_preview_id=None if preview is None else preview.crypto_order_preview_id,
        approval_event_id=None if approval is None else approval.id,
        items=tuple(items),
    )


async def initialize_live_crypto_environment(
    *,
    db: AsyncSession,
    request: InitializeLiveCryptoEnvironmentRequest,
) -> InitializeLiveCryptoEnvironmentResult:
    initial = await inspect_live_crypto_environment(
        db=db,
        exchange_environment=request.exchange_environment,
        paper_account_id=request.paper_account_id,
    )

    created_exchange = False
    created_asset = False
    created_profile = False
    created_campaign = False

    if initial.exchange_connection_id is None:
        if not request.exchange_api_key_name or not request.exchange_private_key:
            raise ValueError("Coinbase credentials are required when the exchange connection is missing")
        await create_exchange_connection(
            db=db,
            payload=SaveExchangeConnectionRequest(
                provider="coinbase_advanced",
                connection_name=request.exchange_connection_name,
                environment=request.exchange_environment,
                api_key_name=request.exchange_api_key_name,
                private_key=request.exchange_private_key,
                passphrase=request.exchange_passphrase,
            ),
            actor=request.actor,
        )
        created_exchange = True

    refreshed_readiness = await inspect_live_crypto_environment(
        db=db,
        exchange_environment=request.exchange_environment,
        paper_account_id=request.paper_account_id,
    )
    if refreshed_readiness.exchange_connection_id is not None:
        refreshed = await refresh_exchange_balances(
            db=db,
            exchange_connection_id=refreshed_readiness.exchange_connection_id,
            actor=request.actor,
        )
        if refreshed.readiness.verdict not in _READY_CONNECTION_VERDICTS:
            raise ValueError("Coinbase readiness check failed; refresh_exchange_balances did not reach ready state")

    asset_result = await ensure_coinbase_crypto_asset(
        db=db,
        request=EnsureCoinbaseAssetRequest(symbol="BTC", base_currency="USD", actor=request.actor),
    )
    created_asset = asset_result.created

    current = await inspect_live_crypto_environment(
        db=db,
        exchange_environment=request.exchange_environment,
        paper_account_id=request.paper_account_id,
    )
    if current.paper_account_id is None:
        raise ValueError("Active crypto paper account is required before initialization can continue")

    if current.live_trading_profile_id is None:
        registration = await register_live_account(
            db=db,
            request=LiveAccountRegistrationRequest(
                paper_account_id=current.paper_account_id,
                requested_by=request.actor,
                registration_source=request.registration_source,
                live_opt_in=True,
                governance_approved=True,
                human_approval_recorded=False,
                provenance_metadata={"source": "initialize_live_crypto_environment"},
                idempotency_key=f"init-live-profile:{current.paper_account_id}",
            ),
        )
        if not registration.accepted:
            raise ValueError(f"Live trading profile registration rejected: {registration.rejection_reason}")
        created_profile = True

    current = await inspect_live_crypto_environment(
        db=db,
        exchange_environment=request.exchange_environment,
        paper_account_id=request.paper_account_id,
    )
    if current.capital_campaign_id is None:
        paper_account = await _load_selected_crypto_paper_account(db=db, paper_account_id=request.paper_account_id)
        if paper_account is None:
            raise ValueError("Active crypto paper account is required for campaign initialization")
        await create_capital_campaign(
            db=db,
            request=CapitalCampaignCreateRequest(
                owner=request.campaign_owner,
                name="Production Small Account Mode",
                description="Initialized for live-crypto dry-run readiness",
                status="READY",
                campaign_type="small_account_mode",
                exchange="coinbase_advanced",
                paper_account_id=paper_account.id,
                validation_run_id=None,
                strategy_id=None,
                starting_capital=paper_account.starting_balance,
                current_equity=paper_account.starting_balance,
                realized_profit=Decimal("0"),
                unrealized_profit=Decimal("0"),
                fees=Decimal("0"),
            ),
        )
        created_campaign = True

    final_readiness = await inspect_live_crypto_environment(
        db=db,
        exchange_environment=request.exchange_environment,
        paper_account_id=request.paper_account_id,
    )
    return InitializeLiveCryptoEnvironmentResult(
        created_exchange_connection=created_exchange,
        created_asset=created_asset,
        created_live_trading_profile=created_profile,
        created_capital_campaign=created_campaign,
        readiness=final_readiness,
    )


async def generate_fresh_btc_dry_run_preview(
    *,
    db: AsyncSession,
    request: GeneratePreviewHelperRequest,
) -> GeneratePreviewHelperResult:
    response = await create_crypto_order_preview(
        db=db,
        request=CryptoOrderPreviewCreateRequest(
            exchange_connection_id=request.exchange_connection_id,
            environment="production",
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            quote_size=Decimal("5"),
            base_size=None,
            requested_amount_currency="USD",
            decision_record_id=None,
            validation_run_id=None,
            strategy_id=None,
            strategy_name="production_init_preview_helper",
            generated_by="operator",
            client_request_id=None,
        ),
        actor=request.actor,
    )
    return GeneratePreviewHelperResult(
        crypto_order_preview_id=response.crypto_order_preview_id,
        status=response.status,
    )


async def record_first_live_enablement_approval(
    *,
    db: AsyncSession,
    request: RecordApprovalHelperRequest,
) -> RecordApprovalHelperResult:
    result = await record_live_approval_checkpoint(
        db=db,
        request=LiveApprovalCheckpointRequest(
            live_trading_profile_id=request.live_trading_profile_id,
            checkpoint_type="first_live_enablement",
            approver_id=request.actor,
            approver_role="operator",
            rationale="Production dry-run readiness checkpoint",
            approval_scope={
                "product": "BTC-USD",
                "side": "BUY",
                "max_order_usd": "5",
                "environment": "production",
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            renewal_condition="operator_reapproval_required",
            requested_by=request.actor,
            provenance_metadata={"source": "initialize_live_crypto_environment"},
            idempotency_key=f"init-approval:{request.live_trading_profile_id}",
        ),
    )
    return RecordApprovalHelperResult(
        approval_event_id=result.approval_event_id,
        approval_state=result.approval_state,
    )
