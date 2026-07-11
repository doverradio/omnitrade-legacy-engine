from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.asset import Asset
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.exchange_connection import ExchangeConnection
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.schemas.capital_campaigns import CapitalCampaignCreateRequest
from app.schemas.crypto_order_previews import CryptoOrderPreviewCreateRequest
from app.schemas.exchange_connections import SaveExchangeConnectionRequest
from app.schemas.live_crypto_orders import LiveCryptoOrderDryRunRequest
from app.services import live_crypto_orders as live_crypto_orders_service
from app.services.exchange_connections.providers.registry import provider_mock_mode_enabled
from app.services.exchange_connections.providers.registry import get_exchange_provider_metadata
from app.services.assets_service import EnsureCoinbaseAssetRequest, ensure_coinbase_crypto_asset
from app.services.capital_campaigns.service import create_capital_campaign
from app.services.crypto_order_previews.service import create_crypto_order_preview
from app.services.exchange_connections.service import (
    create_exchange_connection,
    get_decrypted_credentials_for_connection,
    refresh_exchange_balances,
)
from app.services.live.approval import record_live_approval_checkpoint
from app.services.live.contracts import LiveAccountRegistrationRequest, LiveApprovalCheckpointRequest
from app.services.live.registration import register_live_account


DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
_READY_CONNECTION_VERDICTS = {"READY_FOR_PREVIEW", "READY_FOR_DRY_RUN", "READY_FOR_OPERATOR_REVIEW", "INITIALIZED_BUT_UNFUNDED"}


def _annotate_failure_stage(exc: Exception, *, stage: str) -> None:
    # Keep the original exception type/message and attach only safe stage metadata.
    if hasattr(exc, "add_note"):
        notes = getattr(exc, "__notes__", [])
        marker = f"failure_stage={stage}"
        if marker in notes:
            return
        exc.add_note(marker)
        return
    try:
        setattr(exc, "_failure_stage", stage)
    except Exception:
        return


def _normalize_exchange_environment(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized not in {"production", "sandbox"}:
        raise ValueError(f"Unsupported exchange environment: {environment}")
    return normalized


def _exchange_label(*, provider: str, environment: str) -> str:
    normalized = _normalize_exchange_environment(environment)
    if normalized == "production":
        return provider
    return f"{provider}_sandbox"


def _default_connection_name(*, provider: str, environment: str) -> str:
    return f"{provider}-{environment}-primary"


def _profile_environment(profile: LiveTradingProfile | None) -> str | None:
    if profile is None:
        return None
    provenance = profile.provenance_metadata if isinstance(profile.provenance_metadata, dict) else {}
    explicit = provenance.get("exchange_environment") or provenance.get("environment")
    if explicit is not None:
        try:
            return _normalize_exchange_environment(str(explicit))
        except ValueError:
            return None
    registration_source = str(provenance.get("registration_source") or "").lower()
    if "sandbox" in registration_source:
        return "sandbox"
    if "production" in registration_source or registration_source.startswith("human_"):
        return "production"
    return "production"


def _profile_provider(profile: LiveTradingProfile | None) -> str | None:
    if profile is None:
        return None
    provenance = profile.provenance_metadata if isinstance(profile.provenance_metadata, dict) else {}
    provider = provenance.get("provider")
    if provider is None:
        return "coinbase_advanced"
    value = str(provider).strip().lower()
    return value or "coinbase_advanced"


def _profile_matches_context(profile: LiveTradingProfile | None, *, provider: str, environment: str) -> bool:
    return _profile_environment(profile) == _normalize_exchange_environment(environment) and _profile_provider(profile) == provider


def _approval_environment(approval: LiveApprovalEvent | None) -> str | None:
    if approval is None or not isinstance(approval.approval_scope, dict):
        return None
    explicit = approval.approval_scope.get("environment")
    if explicit is None:
        return None
    try:
        return _normalize_exchange_environment(str(explicit))
    except ValueError:
        return None


def _approval_provider(approval: LiveApprovalEvent | None) -> str | None:
    if approval is None or not isinstance(approval.approval_scope, dict):
        return None
    provider = approval.approval_scope.get("provider")
    if provider is None:
        return "coinbase_advanced"
    value = str(provider).strip().lower()
    return value or "coinbase_advanced"


def _rehearsal_mode_for_environment(*, provider: str, environment: str) -> str:
    normalized = _normalize_exchange_environment(environment)
    if normalized != "sandbox":
        return "production_live"
    return "controlled_provider_mock" if provider_mock_mode_enabled(provider) else f"{provider}_sandbox"


def _report_check_status(*, report, code: str) -> str | None:
    checks = getattr(report, "checks", None) or []
    for item in checks:
        item_code = getattr(item, "code", None)
        if item_code != code:
            continue
        status = getattr(item, "status", None)
        return None if status is None else str(status)
    return None


def _readiness_reason_codes(*, report) -> tuple[list[str], list[str]]:
    checks = getattr(report, "checks", None) or []
    fail_codes: list[str] = []
    warn_codes: list[str] = []
    for item in checks:
        code = getattr(item, "code", None)
        status = getattr(item, "status", None)
        if not isinstance(code, str):
            continue
        if status == "fail":
            fail_codes.append(code)
        elif status == "warn":
            warn_codes.append(code)
    return fail_codes, warn_codes


def _usd_balance_details(*, balances: list[object]) -> tuple[bool, str | None]:
    for item in balances:
        if not hasattr(item, "currency"):
            continue
        if str(getattr(item, "currency", "")).upper() != "USD":
            continue
        available = getattr(item, "available", None)
        if available is None:
            return True, None
        return True, format(Decimal(str(available)), "f")
    return False, None


def _safe_auth_error_details(raw_error: object) -> dict[str, object] | None:
    if not isinstance(raw_error, str) or not raw_error.strip():
        return None
    try:
        parsed = json.loads(raw_error)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    allowed_keys = {
        "kraken_endpoint",
        "kraken_http_status",
        "kraken_provider_error",
        "kraken_error_category",
        "kraken_transport_error_type",
        "kraken_auth_category",
    }
    details = {key: parsed.get(key) for key in allowed_keys if key in parsed}
    return details or None


def _build_readiness_failure_details(*, refreshed, requested_provider: str, requested_environment: str) -> dict[str, object]:
    report = getattr(refreshed, "readiness", None)
    fail_codes, warn_codes = _readiness_reason_codes(report=report)
    balances = list(getattr(refreshed, "balances", []) or [])
    usd_balance_known, usd_balance_amount = _usd_balance_details(balances=balances)

    now = datetime.now(timezone.utc)

    def _age_seconds(value: datetime | None) -> int | None:
        if value is None:
            return None
        observed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return max(0, int((now - observed).total_seconds()))

    readiness_checked_at = None if report is None else getattr(report, "checked_at", None)
    last_successful_sync_at = getattr(refreshed, "last_successful_sync_at", None)
    last_heartbeat_at = getattr(refreshed, "last_heartbeat_at", None)

    details: dict[str, object] = {
        "verdict": None if report is None else getattr(report, "verdict", None),
        "reason_codes": {
            "fail": fail_codes,
            "warn": warn_codes,
        },
        "authentication_status": _report_check_status(report=report, code="authentication_valid"),
        "permissions_status": {
            "permissions_retrieved": _report_check_status(report=report, code="permissions_retrieved"),
            "trade_permission_present": _report_check_status(report=report, code="trade_permission_present"),
            "dangerous_permissions_detected": _report_check_status(report=report, code="dangerous_permissions_detected"),
        },
        "balance_readable_status": {
            "balances_retrieved": _report_check_status(report=report, code="balances_retrieved"),
            "usd_balance_retrieved": _report_check_status(report=report, code="usd_balance_retrieved"),
            "btc_balance_retrieved": _report_check_status(report=report, code="btc_balance_retrieved"),
        },
        "usd_balance_known": usd_balance_known,
        "product_readiness_status": {
            "product_btc_usd_available": _report_check_status(report=report, code="product_btc_usd_available"),
            "product_trading_enabled": _report_check_status(report=report, code="product_trading_enabled"),
        },
        "provider_environment_match": {
            "provider": str(getattr(refreshed, "provider", "")) == requested_provider,
            "environment": str(getattr(refreshed, "environment", "")) == requested_environment,
        },
        "timestamps": {
            "readiness_checked_at": None if readiness_checked_at is None else readiness_checked_at.isoformat(),
            "last_successful_sync_at": None if last_successful_sync_at is None else last_successful_sync_at.isoformat(),
            "last_heartbeat_at": None if last_heartbeat_at is None else last_heartbeat_at.isoformat(),
        },
        "freshness_seconds": {
            "readiness_checked_at": _age_seconds(readiness_checked_at),
            "last_successful_sync_at": _age_seconds(last_successful_sync_at),
            "last_heartbeat_at": _age_seconds(last_heartbeat_at),
        },
        "credentials_valid": bool(getattr(refreshed, "credentials_valid", False)),
    }
    safe_auth_details = _safe_auth_error_details(getattr(refreshed, "last_api_error", None))
    if safe_auth_details is not None:
        details["authentication_diagnostics"] = safe_auth_details
    if usd_balance_known and usd_balance_amount is not None:
        details["usd_balance_amount"] = usd_balance_amount
    return details


async def _credential_consistency_with_request(
    *,
    db: AsyncSession,
    exchange_connection_id: UUID,
    request: InitializeLiveCryptoEnvironmentRequest,
) -> dict[str, bool | None]:
    connection = await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.exchange_connection_id == exchange_connection_id)
        .limit(1)
    )
    if connection is None:
        return {
            "stored_api_key_matches_env": None,
            "stored_api_secret_matches_env": None,
        }

    try:
        decrypted = get_decrypted_credentials_for_connection(connection)
    except Exception:
        return {
            "stored_api_key_matches_env": None,
            "stored_api_secret_matches_env": None,
        }

    stored_key = str(decrypted.get("api_key") or "").strip() or None
    stored_secret = str(decrypted.get("api_secret") or "").strip() or None
    requested_key = str(request.exchange_api_key_name or "").strip() or None
    requested_secret = str(request.exchange_private_key or "").strip() or None

    return {
        "stored_api_key_matches_env": None if requested_key is None else stored_key == requested_key,
        "stored_api_secret_matches_env": None if requested_secret is None else stored_secret == requested_secret,
    }



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
    provider: str = "coinbase_advanced"
    paper_account_id: UUID = DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID
    exchange_environment: str = "production"
    exchange_connection_name: str | None = None
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
    exchange_environment: str = "production"


@dataclass(frozen=True, slots=True)
class GeneratePreviewHelperResult:
    crypto_order_preview_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class RecordApprovalHelperRequest:
    actor: str
    live_trading_profile_id: UUID
    provider: str = "coinbase_advanced"
    exchange_environment: str = "production"


@dataclass(frozen=True, slots=True)
class RecordApprovalHelperResult:
    approval_event_id: UUID
    approval_state: str


@dataclass(frozen=True, slots=True)
class LiveCryptoRehearsalResult:
    rehearsal_mode: str
    readiness: LiveCryptoEnvironmentReadiness
    preview_created: bool
    approval_created: bool
    preview_id: UUID
    approval_event_id: UUID
    live_crypto_order_id: UUID
    audit_correlation_id: UUID
    dry_run_status: str
    review_passed: bool
    review_check_count: int
    production_ready: bool


async def _load_exchange_connection_for_provider(*, db: AsyncSession, provider: str, environment: str) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == provider)
        .where(ExchangeConnection.environment == environment)
        .order_by(ExchangeConnection.created_at.desc())
        .limit(1)
    )


async def _load_coinbase_connection(*, db: AsyncSession, environment: str) -> ExchangeConnection | None:
    return await _load_exchange_connection_for_provider(db=db, provider="coinbase_advanced", environment=environment)


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


async def _load_live_profile_for_account(*, db: AsyncSession, paper_account_id: UUID | None, provider: str, environment: str) -> LiveTradingProfile | None:
    if paper_account_id is None:
        return None
    if hasattr(db, "execute"):
        rows = (
            (
                await db.execute(
                    select(LiveTradingProfile)
                    .where(LiveTradingProfile.paper_account_id == paper_account_id)
                    .order_by(LiveTradingProfile.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for profile in rows:
            if _profile_matches_context(profile, provider=provider, environment=environment):
                return profile
        return None
    profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc())
        .limit(1)
    )
    return profile if _profile_matches_context(profile, provider=provider, environment=environment) else None


async def _load_live_profile_by_id(*, db: AsyncSession, live_trading_profile_id: UUID) -> LiveTradingProfile | None:
    return await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.id == live_trading_profile_id)
        .limit(1)
    )


async def _load_coinbase_btc_asset(*, db: AsyncSession) -> Asset | None:
    raise RuntimeError("_load_coinbase_btc_asset requires explicit exchange label")


async def _load_coinbase_btc_asset_for_exchange(*, db: AsyncSession, exchange: str) -> Asset | None:
    return await db.scalar(
        select(Asset)
        .where(Asset.symbol == "BTC")
        .where(Asset.asset_class == "crypto")
        .where(Asset.exchange == exchange)
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc())
        .limit(1)
    )


async def _load_campaign_for_account(*, db: AsyncSession, paper_account_id: UUID | None) -> CapitalCampaign | None:
    raise RuntimeError("_load_campaign_for_account requires explicit exchange label")


async def _load_campaign_for_account_exchange(*, db: AsyncSession, paper_account_id: UUID | None, exchange: str) -> CapitalCampaign | None:
    if paper_account_id is None:
        return None
    return await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.paper_account_id == paper_account_id)
        .where(CapitalCampaign.exchange == exchange)
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


async def _load_latest_approval(*, db: AsyncSession, live_trading_profile_id: UUID | None, provider: str, environment: str) -> LiveApprovalEvent | None:
    if live_trading_profile_id is None:
        return None
    if hasattr(db, "execute"):
        rows = (
            (
                await db.execute(
                    select(LiveApprovalEvent)
                    .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
                    .where(LiveApprovalEvent.checkpoint_type == "first_live_enablement")
                    .where(LiveApprovalEvent.approval_state == "approved")
                    .order_by(LiveApprovalEvent.sequence_number.desc())
                )
            )
            .scalars()
            .all()
        )
        for approval in rows:
            if _approval_environment(approval) == _normalize_exchange_environment(environment) and _approval_provider(approval) == provider:
                return approval
        return None
    approval = await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
        .where(LiveApprovalEvent.checkpoint_type == "first_live_enablement")
        .where(LiveApprovalEvent.approval_state == "approved")
        .order_by(LiveApprovalEvent.sequence_number.desc())
        .limit(1)
    )
    return approval if _approval_environment(approval) == _normalize_exchange_environment(environment) and _approval_provider(approval) == provider else None


async def _load_latest_dry_run_order(*, db: AsyncSession, environment: str) -> LiveCryptoOrder | None:
    return await db.scalar(
        select(LiveCryptoOrder)
        .where(LiveCryptoOrder.environment == _normalize_exchange_environment(environment))
        .where(LiveCryptoOrder.status.in_(["DRY_RUN_READY", "DRY_RUN_BLOCKED"]))
        .order_by(LiveCryptoOrder.created_at.desc())
        .limit(1)
    )


def _connection_balance(connection: ExchangeConnection | None, currency: str) -> Decimal | None:
    if connection is None:
        return None
    for item in (connection.balances or []):
        if str(item.get("currency", "")).upper() != currency.upper():
            continue
        return Decimal(str(item.get("available", "0")))
    return None


def _readiness_check_status(connection: ExchangeConnection | None, code: str) -> str | None:
    if connection is None:
        return None
    for item in connection.last_readiness_report or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("code", "")) == code:
            return str(item.get("status", ""))
    return None


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
    provider: str = "coinbase_advanced",
    exchange_environment: str = "production",
    paper_account_id: UUID = DEFAULT_PRODUCTION_CRYPTO_PAPER_ACCOUNT_ID,
) -> LiveCryptoEnvironmentReadiness:
    now = datetime.now(timezone.utc)
    exchange_environment = _normalize_exchange_environment(exchange_environment)
    exchange = _exchange_label(provider=provider, environment=exchange_environment)
    if provider == "coinbase_advanced":
        connection = await _load_coinbase_connection(db=db, environment=exchange_environment)
    else:
        connection = await _load_exchange_connection_for_provider(db=db, provider=provider, environment=exchange_environment)
    paper_account = await _load_selected_crypto_paper_account(db=db, paper_account_id=paper_account_id)
    profile = await _load_live_profile_for_account(
        db=db,
        paper_account_id=paper_account.id if paper_account is not None else None,
        provider=provider,
        environment=exchange_environment,
    )
    asset = await _load_coinbase_btc_asset_for_exchange(db=db, exchange=exchange)
    provider_name = provider
    try:
        provider_name = get_exchange_provider_metadata(provider).display_name
    except Exception:
        provider_name = provider

    campaign = await _load_campaign_for_account_exchange(db=db, paper_account_id=paper_account.id if paper_account is not None else None, exchange=exchange)
    preview = await _load_latest_preview(db=db, exchange_connection_id=connection.exchange_connection_id if connection is not None else None)
    approval = await _load_latest_approval(
        db=db,
        live_trading_profile_id=profile.id if profile is not None else None,
        provider=provider,
        environment=exchange_environment,
    )
    usd_available = _connection_balance(connection, "USD")
    balance_readable = _readiness_check_status(connection, "usd_balance_retrieved") == "pass"
    product_ready = _readiness_check_status(connection, "product_btc_usd_available") == "pass"
    readiness_ready = connection is not None and connection.credentials_valid and connection.last_readiness_verdict in _READY_CONNECTION_VERDICTS.union({"INITIALIZED_BUT_UNFUNDED"})
    price_evidence_fresh = preview is not None and preview.expires_at > now
    precision_known = asset is not None and asset.min_order_notional is not None and (asset.qty_step_size is not None or asset.supports_fractional)
    live_submission_disabled = not get_settings().live_crypto_order_submission_enabled

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
                f"{provider_name} {exchange_environment} connection ready ({connection.exchange_connection_id})"
                if connection is not None
                else f"{provider_name} {exchange_environment} connection missing"
            ),
        ),
        ReadinessItem(
            key="credentials_valid",
            label="Credentials",
            ready=connection is not None and bool(connection.credentials_valid),
            detail=(
                "Stored credentials validated on the last provider sync"
                if connection is not None and connection.credentials_valid
                else "Stored credentials missing or invalid"
            ),
        ),
        ReadinessItem(
            key="provider_readiness",
            label="Provider Readiness",
            ready=readiness_ready,
            detail=(
                f"Latest readiness verdict: {connection.last_readiness_verdict}"
                if connection is not None and connection.last_readiness_verdict is not None
                else "Readiness evidence unavailable"
            ),
        ),
        ReadinessItem(
            key="balance_readable",
            label="Balance Readable",
            ready=balance_readable,
            detail=(
                "USD balance visibility confirmed"
                if balance_readable
                else "USD balance visibility unavailable"
            ),
        ),
        ReadinessItem(
            key="usd_funded",
            label="USD Funded",
            ready=usd_available is not None and usd_available > Decimal("0"),
            detail=(
                f"USD available balance observed: {format(usd_available, 'f')}"
                if usd_available is not None
                else "USD available balance unknown"
            ),
        ),
        ReadinessItem(
            key="product_ready",
            label="BTC-USD Product",
            ready=product_ready,
            detail=(
                "BTC-USD product availability confirmed"
                if product_ready
                else "BTC-USD product availability unknown or unavailable"
            ),
        ),
        ReadinessItem(
            key="precision_constraints_known",
            label="Precision And Minimums",
            ready=precision_known,
            detail=(
                f"min_order_notional={format(Decimal(str(asset.min_order_notional)), 'f')} qty_step_size={format(Decimal(str(asset.qty_step_size)), 'f') if asset.qty_step_size is not None else 'fractional'}"
                if precision_known and asset is not None
                else "Precision or minimum-order metadata missing"
            ),
        ),
        ReadinessItem(
            key="price_evidence_fresh",
            label="Price Evidence",
            ready=price_evidence_fresh,
            detail=(
                f"Fresh preview-backed price evidence available ({preview.crypto_order_preview_id})"
                if price_evidence_fresh and preview is not None
                else "Fresh preview-backed price evidence missing"
            ),
        ),
        ReadinessItem(
            key="live_trading_profile",
            label="Trading Profile",
            ready=profile is not None,
            detail=(
                f"Live trading profile ready ({profile.id}) for {exchange_environment}"
                if profile is not None
                else f"Live trading profile missing for {exchange_environment}"
            ),
        ),
        ReadinessItem(
            key="capital_campaign",
            label="Campaign",
            ready=campaign is not None,
            detail=(
                f"Capital campaign ready ({campaign.uuid}) for {exchange_environment}"
                if campaign is not None
                else f"Capital campaign missing for {exchange_environment}"
            ),
        ),
        ReadinessItem(
            key="asset",
            label="Asset",
            ready=asset is not None,
            detail=(
                f"{provider_name} BTC asset ready ({asset.id}) on {exchange}"
                if asset is not None
                else f"{provider_name} BTC asset missing on {exchange}"
            ),
        ),
        ReadinessItem(
            key="preview",
            label="Preview",
            ready=preview is not None,
            detail=(
                f"Crypto order preview available ({preview.crypto_order_preview_id}) for {exchange_environment}"
                if preview is not None
                else f"Crypto order preview missing for {exchange_environment}"
            ),
        ),
        ReadinessItem(
            key="approval",
            label="Approval",
            ready=approval_ready,
            detail=(
                f"First live enablement approval ready ({approval.id}) for {exchange_environment}"
                if approval_ready and approval is not None
                else f"First live enablement approval missing or expired for {exchange_environment}"
            ),
        ),
        ReadinessItem(
            key="dry_run",
            label="Dry Run",
            ready=dry_run_ready,
            detail=dry_run_detail,
        ),
        ReadinessItem(
            key="submission_disabled",
            label="Submission Disabled",
            ready=live_submission_disabled,
            detail=(
                "Live submission feature flag remains disabled"
                if live_submission_disabled
                else "Live submission feature flag is enabled"
            ),
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
    stage = "normalize_exchange_environment"
    try:
        exchange_environment = _normalize_exchange_environment(request.exchange_environment)
        connection_name = request.exchange_connection_name or _default_connection_name(provider=request.provider, environment=exchange_environment)

        stage = "inspect_initial_environment"
        initial = await inspect_live_crypto_environment(
            db=db,
            provider=request.provider,
            exchange_environment=exchange_environment,
            paper_account_id=request.paper_account_id,
        )

        created_exchange = False
        created_asset = False
        created_profile = False
        created_campaign = False

        if initial.exchange_connection_id is None:
            stage = "validate_exchange_credentials"
            if not request.exchange_api_key_name or not request.exchange_private_key:
                if request.provider == "coinbase_advanced":
                    raise ValueError("Coinbase credentials are required when the exchange connection is missing")
                raise ValueError("Provider credentials are required when the exchange connection is missing")
            stage = "create_exchange_connection"
            await create_exchange_connection(
                db=db,
                payload=SaveExchangeConnectionRequest(
                    provider=request.provider,
                    connection_name=connection_name,
                    environment=exchange_environment,
                    api_key_name=request.exchange_api_key_name,
                    private_key=request.exchange_private_key,
                    passphrase=request.exchange_passphrase,
                ),
                actor=request.actor,
            )
            created_exchange = True

        stage = "inspect_post_connection_environment"
        refreshed_readiness = await inspect_live_crypto_environment(
            db=db,
            provider=request.provider,
            exchange_environment=exchange_environment,
            paper_account_id=request.paper_account_id,
        )
        if refreshed_readiness.exchange_connection_id is not None:
            stage = "refresh_exchange_balances"
            refreshed = await refresh_exchange_balances(
                db=db,
                exchange_connection_id=refreshed_readiness.exchange_connection_id,
                actor=request.actor,
            )
            stage = "validate_provider_readiness"
            if refreshed.readiness.verdict not in _READY_CONNECTION_VERDICTS:
                safe_details = _build_readiness_failure_details(
                    refreshed=refreshed,
                    requested_provider=request.provider,
                    requested_environment=exchange_environment,
                )
                safe_details.update(
                    await _credential_consistency_with_request(
                        db=db,
                        exchange_connection_id=refreshed_readiness.exchange_connection_id,
                        request=request,
                    )
                )
                if request.provider == "coinbase_advanced":
                    raise ValueError(
                        "Coinbase readiness check failed; refresh_exchange_balances did not reach ready state"
                        f"; readiness_details={json.dumps(safe_details, sort_keys=True, separators=(',', ':'))}"
                    )
                raise ValueError(
                    "Provider readiness check failed; refresh_exchange_balances did not reach ready state"
                    f"; readiness_details={json.dumps(safe_details, sort_keys=True, separators=(',', ':'))}"
                )

        stage = "ensure_btc_asset"
        asset_result = await ensure_coinbase_crypto_asset(
            db=db,
            request=EnsureCoinbaseAssetRequest(
                symbol="BTC",
                base_currency="USD",
                exchange=_exchange_label(provider=request.provider, environment=exchange_environment),
                actor=request.actor,
            ),
        )
        created_asset = asset_result.created

        stage = "inspect_post_asset_environment"
        current = await inspect_live_crypto_environment(
            db=db,
            provider=request.provider,
            exchange_environment=exchange_environment,
            paper_account_id=request.paper_account_id,
        )
        if current.paper_account_id is None:
            stage = "validate_active_crypto_paper_account"
            raise ValueError("Active crypto paper account is required before initialization can continue")

        if current.live_trading_profile_id is None:
            stage = "register_live_trading_profile"
            registration = await register_live_account(
                db=db,
                request=LiveAccountRegistrationRequest(
                    paper_account_id=current.paper_account_id,
                    requested_by=request.actor,
                    registration_source=request.registration_source,
                    live_opt_in=True,
                    governance_approved=True,
                    human_approval_recorded=False,
                    provenance_metadata={
                        "source": "initialize_live_crypto_environment",
                        "exchange_environment": exchange_environment,
                        "exchange_label": _exchange_label(provider=request.provider, environment=exchange_environment),
                        "provider": request.provider,
                    },
                    idempotency_key=f"init-live-profile:{request.provider}:{exchange_environment}:{current.paper_account_id}",
                ),
            )
            stage = "validate_live_trading_profile_registration"
            if not registration.accepted:
                raise ValueError(f"Live trading profile registration rejected: {registration.rejection_reason}")
            created_profile = True

        stage = "inspect_post_profile_environment"
        current = await inspect_live_crypto_environment(
            db=db,
            provider=request.provider,
            exchange_environment=exchange_environment,
            paper_account_id=request.paper_account_id,
        )
        if current.capital_campaign_id is None:
            stage = "load_campaign_paper_account"
            paper_account = await _load_selected_crypto_paper_account(db=db, paper_account_id=request.paper_account_id)
            if paper_account is None:
                stage = "validate_campaign_paper_account"
                raise ValueError("Active crypto paper account is required for campaign initialization")
            stage = "create_capital_campaign"
            await create_capital_campaign(
                db=db,
                request=CapitalCampaignCreateRequest(
                    owner=request.campaign_owner,
                    name=(
                        f"{request.provider} Production Small Account Mode"
                        if exchange_environment == "production"
                        else f"{request.provider} Sandbox Small Account Mode"
                    ),
                    description="Initialized for live-crypto dry-run readiness",
                    status="READY",
                    campaign_type="small_account_mode",
                    exchange=_exchange_label(provider=request.provider, environment=exchange_environment),
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

        stage = "inspect_final_environment"
        final_readiness = await inspect_live_crypto_environment(
            db=db,
            provider=request.provider,
            exchange_environment=exchange_environment,
            paper_account_id=request.paper_account_id,
        )
        return InitializeLiveCryptoEnvironmentResult(
            created_exchange_connection=created_exchange,
            created_asset=created_asset,
            created_live_trading_profile=created_profile,
            created_capital_campaign=created_campaign,
            readiness=final_readiness,
        )
    except Exception as exc:
        _annotate_failure_stage(exc, stage=stage)
        raise


async def generate_fresh_btc_dry_run_preview(
    *,
    db: AsyncSession,
    request: GeneratePreviewHelperRequest,
) -> GeneratePreviewHelperResult:
    response = await create_crypto_order_preview(
        db=db,
        request=CryptoOrderPreviewCreateRequest(
            exchange_connection_id=request.exchange_connection_id,
            environment=request.exchange_environment,
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
    profile = await _load_live_profile_by_id(db=db, live_trading_profile_id=request.live_trading_profile_id)
    if profile is None:
        raise LookupError("live trading profile not found")
    requested_environment = _normalize_exchange_environment(request.exchange_environment)
    if _profile_environment(profile) != requested_environment:
        raise ValueError(
            f"live trading profile environment mismatch: profile={_profile_environment(profile) or 'missing'} requested={requested_environment}"
        )
    if _profile_provider(profile) != request.provider:
        raise ValueError(
            f"live trading profile provider mismatch: profile={_profile_provider(profile) or 'missing'} requested={request.provider}"
        )

    result = await record_live_approval_checkpoint(
        db=db,
        request=LiveApprovalCheckpointRequest(
            live_trading_profile_id=request.live_trading_profile_id,
            checkpoint_type="first_live_enablement",
            approver_id=request.actor,
            approver_role="operator",
            rationale="Dry-run readiness checkpoint",
            approval_scope={
                "product": "BTC-USD",
                "side": "BUY",
                "max_order_usd": "5",
                "provider": request.provider,
                "environment": requested_environment,
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            renewal_condition="operator_reapproval_required",
            requested_by=request.actor,
            provenance_metadata={
                "source": "initialize_live_crypto_environment",
                "provider": request.provider,
                "exchange_environment": requested_environment,
            },
            idempotency_key=f"init-approval:{request.provider}:{requested_environment}:{request.live_trading_profile_id}",
        ),
    )
    return RecordApprovalHelperResult(
        approval_event_id=result.approval_event_id,
        approval_state=result.approval_state,
    )


async def run_live_crypto_rehearsal(
    *,
    db: AsyncSession,
    request: InitializeLiveCryptoEnvironmentRequest,
    verify_rehearsal_evidence,
) -> LiveCryptoRehearsalResult:
    exchange_environment = _normalize_exchange_environment(request.exchange_environment)
    if exchange_environment != "sandbox":
        raise ValueError("live crypto rehearsal is sandbox-only; production environment is rejected")

    await initialize_live_crypto_environment(db=db, request=request)
    readiness = await inspect_live_crypto_environment(
        db=db,
        provider=request.provider,
        exchange_environment=exchange_environment,
        paper_account_id=request.paper_account_id,
    )
    if readiness.exchange_connection_id is None or readiness.live_trading_profile_id is None:
        raise ValueError("sandbox rehearsal prerequisites incomplete after initialization")

    preview_created = False
    preview_id = readiness.crypto_order_preview_id
    if preview_id is None:
        preview = await generate_fresh_btc_dry_run_preview(
            db=db,
            request=GeneratePreviewHelperRequest(
                actor=request.actor,
                exchange_connection_id=readiness.exchange_connection_id,
                exchange_environment=exchange_environment,
            ),
        )
        preview_id = preview.crypto_order_preview_id
        preview_created = True

    approval_created = False
    approval_event_id = readiness.approval_event_id
    if approval_event_id is None:
        approval = await record_first_live_enablement_approval(
            db=db,
            request=RecordApprovalHelperRequest(
                actor=request.actor,
                live_trading_profile_id=readiness.live_trading_profile_id,
                provider=request.provider,
                exchange_environment=exchange_environment,
            ),
        )
        approval_event_id = approval.approval_event_id
        approval_created = True

    dry_run = await live_crypto_orders_service.service.dry_run(
        db=db,
        request=LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=readiness.live_trading_profile_id,
            crypto_order_preview_id=preview_id,
            operator_identity=request.actor,
            idempotency_token=(
                f"rehearsal:{_rehearsal_mode_for_environment(provider=request.provider, environment=exchange_environment)}:"
                f"{readiness.live_trading_profile_id}:{preview_id}"
            ),
        ),
    )
    report = await verify_rehearsal_evidence(
        db=db,
        live_crypto_order_id=dry_run.live_crypto_order.live_crypto_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )
    production_readiness = await inspect_live_crypto_environment(
        db=db,
        provider=request.provider,
        exchange_environment="production",
        paper_account_id=request.paper_account_id,
    )
    if production_readiness.ready:
        raise ValueError("sandbox rehearsal must not mark production ready")

    return LiveCryptoRehearsalResult(
        rehearsal_mode=_rehearsal_mode_for_environment(provider=request.provider, environment=exchange_environment),
        readiness=readiness,
        preview_created=preview_created,
        approval_created=approval_created,
        preview_id=preview_id,
        approval_event_id=approval_event_id,
        live_crypto_order_id=dry_run.live_crypto_order.live_crypto_order_id,
        audit_correlation_id=dry_run.live_crypto_order.audit_correlation_id,
        dry_run_status=dry_run.dry_run_status,
        review_passed=report.passed,
        review_check_count=len(report.checks),
        production_ready=production_readiness.ready,
    )
