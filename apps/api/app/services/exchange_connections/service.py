from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_profile import LiveTradingProfile
from app.schemas.exchange_connections import (
    DisconnectExchangeConnectionResponse,
    DisconnectExchangeConnectionRequest,
    ExchangeBalanceResponse,
    ExchangeConnectionListResponse,
    ExchangeConnectionResponse,
    ExchangeCredentialMaskResponse,
    ExchangeReadinessCheckResponse,
    ExchangeReadinessReportResponse,
    ReconcileExternalTradeRequest,
    ReconcileExternalTradeResponse,
    RotateExchangeCredentialsRequest,
    SaveExchangeConnectionRequest,
    TestExchangeConnectionRequest,
    TestExchangeConnectionResponse,
)
from app.services.exchange_connections.crypto import decrypt_credential_payload, encrypt_credential_payload
from app.services.exchange_connections.providers.base import ExchangeAuthResult
from app.services.exchange_connections.providers.kraken_spot import product_id_from_kraken_pair
from app.services.exchange_connections.providers.registry import get_exchange_provider, require_provider_capabilities
from app.services.exchange_connections.readiness import build_report, readiness_check


_DEFAULT_CLOCK_SKEW_FAIL_SECONDS = 30


def _mask_api_key(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def _mask_secret(_: str) -> str:
    return "********"


def _safe_decimal(value: str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _has_dangerous_permissions(permissions: list[str] | None) -> bool:
    lowered = [item.lower() for item in (permissions or [])]
    return any("withdraw" in item or "transfer" in item for item in lowered)


def _has_trade_permission(permissions: list[str] | None) -> bool:
    lowered = [item.lower() for item in (permissions or [])]
    return any("trade" in item or "order" in item or "preview" in item for item in lowered)


def _is_account_restricted(account_status: str | None) -> bool:
    if account_status is None:
        return False
    normalized = account_status.strip().lower()
    return normalized not in {"active", "enabled", "ok"}


def _balance_amount(connection: ExchangeConnection, currency: str) -> Decimal:
    for item in (connection.balances or []):
        if str(item.get("currency", "")).upper() != currency.upper():
            continue
        return _safe_decimal(item.get("available")) or Decimal("0")
    return Decimal("0")


def _has_balance_currency(connection: ExchangeConnection, currency: str) -> bool:
    for item in (connection.balances or []):
        if str(item.get("currency", "")).upper() == currency.upper():
            return True
    return False


def _provider_label(provider: str) -> str:
    try:
        return get_exchange_provider(provider).metadata.display_name
    except Exception:
        return provider


def _default_readiness() -> ExchangeReadinessReportResponse:
    return build_report(
        checks=[
            readiness_check(
                code="credentials_stored",
                label="Credentials Stored",
                status="fail",
                explanation="Credentials are not configured for this connection.",
                remediation="Save an API key name and private key to enable verification.",
            )
        ]
    )


def _readiness_from_connection(connection: ExchangeConnection) -> ExchangeReadinessReportResponse:
    raw = connection.last_readiness_report or []
    checks: list[ExchangeReadinessCheckResponse] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            checks.append(
                ExchangeReadinessCheckResponse(
                    code=str(item.get("code", "unknown")),
                    label=str(item.get("label", "Unknown")),
                    status=str(item.get("status", "warn")),
                    explanation=str(item.get("explanation", "Not available")),
                    checked_at=datetime.fromisoformat(str(item.get("checked_at"))),
                    remediation=str(item.get("remediation", "Not available")),
                )
            )
        except Exception:
            continue

    if checks:
        checked_at = connection.last_verified_at or datetime.now(timezone.utc)
        return ExchangeReadinessReportResponse(
            verdict=(connection.last_readiness_verdict or "UNKNOWN"),
            checked_at=checked_at,
            checks=checks,
        )

    return _default_readiness()


def _to_response(connection: ExchangeConnection) -> ExchangeConnectionResponse:
    provider_label = connection.provider
    try:
        provider_label = get_exchange_provider(connection.provider).metadata.display_name
    except Exception:
        provider_label = connection.provider

    balances_payload = connection.balances or []
    balances: list[ExchangeBalanceResponse] = []
    for item in balances_payload:
        if not isinstance(item, dict):
            continue
        balances.append(
            ExchangeBalanceResponse(
                currency=str(item.get("currency", "USD")),
                available=_safe_decimal(item.get("available")) or Decimal("0"),
                reserved=_safe_decimal(item.get("reserved")) or Decimal("0"),
                total=_safe_decimal(item.get("total")) or Decimal("0"),
            )
        )

    return ExchangeConnectionResponse(
        exchange_connection_id=connection.exchange_connection_id,
        provider=connection.provider,
        provider_label=provider_label,
        connection_name=connection.connection_name,
        environment=connection.environment,
        status=connection.status,
        credentials_valid=connection.credentials_valid,
        credential_mask=ExchangeCredentialMaskResponse(
            api_key_name=connection.api_key_masked,
            private_key=connection.api_secret_masked,
            passphrase="********" if connection.passphrase_configured else None,
        ),
        api_permissions=list(connection.api_permissions or []),
        account_status=connection.account_status,
        balances=balances,
        total_equity_usd=_safe_decimal(connection.total_equity_usd),
        last_successful_sync_at=connection.last_successful_sync_at,
        last_heartbeat_at=connection.last_heartbeat_at,
        last_api_error=connection.last_api_error,
        readiness=_readiness_from_connection(connection),
        updated_at=connection.updated_at,
    )


def _serialize_readiness(report: ExchangeReadinessReportResponse) -> list[dict[str, object]]:
    return [
        {
            "code": item.code,
            "label": item.label,
            "status": item.status,
            "explanation": item.explanation,
            "checked_at": item.checked_at.isoformat(),
            "remediation": item.remediation,
        }
        for item in report.checks
    ]


async def _record_audit(
    *,
    db: AsyncSession,
    action: str,
    entity_id: uuid.UUID,
    before_state: dict[str, object] | None,
    after_state: dict[str, object] | None,
    actor: str,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="exchange_connection",
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
        )
    )


async def list_exchange_connections(*, db: AsyncSession) -> ExchangeConnectionListResponse:
    rows = (await db.execute(select(ExchangeConnection).order_by(ExchangeConnection.created_at.asc()))).scalars().all()

    if not rows:
        synthetic = ExchangeConnection(
            exchange_connection_id=uuid.uuid4(),
            provider="coinbase_advanced",
            connection_name="Coinbase Advanced",
            environment="sandbox",
            status="disconnected",
            credentials_encrypted="",
            api_key_masked="Not configured",
            api_secret_masked="Not configured",
            passphrase_configured=False,
            credentials_valid=False,
            api_permissions=[],
            account_status=None,
            balances=[],
            total_equity_usd=None,
            last_successful_sync_at=None,
            last_heartbeat_at=None,
            last_api_error=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return ExchangeConnectionListResponse(items=[_to_response(synthetic)])

    return ExchangeConnectionListResponse(items=[_to_response(item) for item in rows])


async def test_exchange_credentials(
    *,
    payload: TestExchangeConnectionRequest,
) -> TestExchangeConnectionResponse:
    provider = get_exchange_provider(payload.provider)
    auth_result = await provider.test_authentication(
        credentials={
            "api_key": payload.api_key_name,
            "api_secret": payload.private_key,
            "passphrase": payload.passphrase or "",
        },
        environment=payload.environment,
    )

    return TestExchangeConnectionResponse(
        reachable=auth_result.reachable,
        authenticated=auth_result.authenticated,
        account_status=auth_result.account_status,
        permissions=auth_result.permissions,
        heartbeat_at=auth_result.heartbeat_at,
        error=auth_result.error,
    )


async def _load_connection(*, db: AsyncSession, exchange_connection_id: uuid.UUID) -> ExchangeConnection:
    connection = await db.scalar(
        select(ExchangeConnection).where(ExchangeConnection.exchange_connection_id == exchange_connection_id)
    )
    if connection is None:
        raise NotFoundError(message="Exchange connection not found", details={"exchange_connection_id": str(exchange_connection_id)})
    return connection


def _decrypt_credentials(connection: ExchangeConnection) -> dict[str, str]:
    payload = decrypt_credential_payload(connection.credentials_encrypted)
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise InvalidRequestError(message="Stored exchange credentials are malformed", details={})
    return {
        "api_key": str(parsed.get("api_key_name", parsed.get("api_key", ""))),
        "api_secret": str(parsed.get("private_key", parsed.get("api_secret", ""))),
        "passphrase": str(parsed.get("passphrase", "")),
    }


def get_decrypted_credentials_for_connection(connection: ExchangeConnection) -> dict[str, str]:
    return _decrypt_credentials(connection)


async def create_exchange_connection(
    *,
    db: AsyncSession,
    payload: SaveExchangeConnectionRequest,
    actor: str = "system",
) -> ExchangeConnectionResponse:
    now = datetime.now(timezone.utc)
    auth_result = await get_exchange_provider(payload.provider).test_authentication(
        credentials={
            "api_key": payload.api_key_name,
            "api_secret": payload.private_key,
            "passphrase": payload.passphrase or "",
        },
        environment=payload.environment,
    )

    initial_readiness = await _build_and_persist_readiness_for_auth_result(
        provider=payload.provider,
        auth_result=auth_result,
        credentials_stored=True,
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=False,
        permissions_retrieved=len(auth_result.permissions) > 0,
        usd_balance_retrieved=False,
        usd_balance_funded=False,
        btc_balance_retrieved=False,
        dangerous_permissions_detected=_has_dangerous_permissions(auth_result.permissions),
        product_btc_usd_available=False,
        product_trading_enabled=False,
        account_restricted=_is_account_restricted(auth_result.account_status),
        rate_limit_status_available=False,
        trade_permission_required=payload.provider != "kraken_spot",
    )

    credentials_encrypted = encrypt_credential_payload(
        json.dumps(
            {
                "api_key_name": payload.api_key_name,
                "private_key": payload.private_key,
                "passphrase": payload.passphrase or "",
            }
        )
    )

    connection = ExchangeConnection(
        provider=payload.provider,
        connection_name=payload.connection_name.strip(),
        environment=payload.environment,
        status="connected" if auth_result.authenticated else "error",
        credentials_encrypted=credentials_encrypted,
        api_key_masked=_mask_api_key(payload.api_key_name),
        api_secret_masked=_mask_secret(payload.private_key),
        passphrase_configured=bool(payload.passphrase),
        credentials_valid=auth_result.authenticated,
        api_permissions=auth_result.permissions,
        account_status=auth_result.account_status,
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=auth_result.heartbeat_at if auth_result.authenticated else None,
        last_heartbeat_at=auth_result.heartbeat_at,
        last_api_error=auth_result.error,
        last_verified_at=initial_readiness.checked_at,
        last_readiness_verdict=initial_readiness.verdict,
        last_readiness_report=_serialize_readiness(initial_readiness),
        created_at=now,
        updated_at=now,
    )
    db.add(connection)
    if hasattr(db, "flush"):
        await db.flush()

    await _record_audit(
        db=db,
        action="exchange_connection_created",
        entity_id=connection.exchange_connection_id,
        before_state=None,
        after_state={
            "provider": connection.provider,
            "connection_name": connection.connection_name,
            "environment": connection.environment,
            "status": connection.status,
            "credentials_valid": connection.credentials_valid,
            "readiness_verdict": connection.last_readiness_verdict,
        },
        actor=actor,
    )

    await db.commit()
    if hasattr(db, "refresh"):
        await db.refresh(connection)

    return _to_response(connection)


def _apply_auth_result(connection: ExchangeConnection, auth_result: ExchangeAuthResult) -> None:
    connection.last_heartbeat_at = auth_result.heartbeat_at
    connection.credentials_valid = bool(auth_result.authenticated)
    connection.status = "connected" if auth_result.authenticated else "error"
    connection.last_api_error = auth_result.error
    if auth_result.authenticated:
        connection.last_successful_sync_at = auth_result.heartbeat_at
    if auth_result.account_status is not None:
        connection.account_status = auth_result.account_status
    if auth_result.permissions:
        connection.api_permissions = auth_result.permissions


async def refresh_exchange_balances(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    actor: str = "system",
) -> ExchangeConnectionResponse:
    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    credentials = _decrypt_credentials(connection)
    provider = get_exchange_provider(connection.provider)

    before_state = {
        "balances": connection.balances,
        "total_equity_usd": connection.total_equity_usd,
    }

    auth_result = await provider.test_authentication(credentials=credentials, environment=connection.environment)
    _apply_auth_result(connection, auth_result)

    balances_retrieved = False
    product_btc_usd_available = False
    product_trading_enabled = False
    if auth_result.authenticated:
        snapshot = await provider.fetch_balances(credentials=credentials, environment=connection.environment)
        balances_retrieved = True
        connection.balances = [
            {
                "currency": item.currency,
                "available": format(item.available, "f"),
                "reserved": format(item.reserved, "f"),
                "total": format(item.total, "f"),
            }
            for item in snapshot.balances
        ]
        connection.total_equity_usd = None if snapshot.total_equity_usd is None else format(snapshot.total_equity_usd, "f")
        product_snapshot = await provider.fetch_product(credentials=credentials, environment=connection.environment, product_id="BTC-USD")
        product_btc_usd_available = product_snapshot.available
        product_trading_enabled = product_snapshot.trading_enabled

    readiness = await _build_and_persist_readiness_for_auth_result(
        provider=connection.provider,
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=balances_retrieved,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_has_balance_currency(connection, "USD"),
        usd_balance_funded=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_has_balance_currency(connection, "BTC"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=product_btc_usd_available,
        product_trading_enabled=product_trading_enabled,
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
        trade_permission_required=connection.provider != "kraken_spot",
    )
    connection.last_verified_at = readiness.checked_at
    connection.last_readiness_verdict = readiness.verdict
    connection.last_readiness_report = _serialize_readiness(readiness)

    await _record_audit(
        db=db,
        action="exchange_connection_balances_refreshed",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "balances": connection.balances,
            "total_equity_usd": connection.total_equity_usd,
            "status": connection.status,
            "readiness_verdict": connection.last_readiness_verdict,
        },
        actor=actor,
    )

    await db.commit()
    await db.refresh(connection)
    return _to_response(connection)


async def refresh_exchange_account(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    actor: str = "system",
) -> ExchangeConnectionResponse:
    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    credentials = _decrypt_credentials(connection)
    provider = get_exchange_provider(connection.provider)

    before_state = {
        "account_status": connection.account_status,
    }

    auth_result = await provider.test_authentication(credentials=credentials, environment=connection.environment)
    _apply_auth_result(connection, auth_result)

    if auth_result.authenticated:
        snapshot = await provider.fetch_account(credentials=credentials, environment=connection.environment)
        connection.account_status = snapshot.account_status
    product_snapshot = None
    if auth_result.authenticated:
        product_snapshot = await provider.fetch_product(credentials=credentials, environment=connection.environment, product_id="BTC-USD")

    readiness = await _build_and_persist_readiness_for_auth_result(
        provider=connection.provider,
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=len(connection.balances or []) > 0,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_has_balance_currency(connection, "USD"),
        usd_balance_funded=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_has_balance_currency(connection, "BTC"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=bool(product_snapshot and product_snapshot.available),
        product_trading_enabled=bool(product_snapshot and product_snapshot.trading_enabled),
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
        trade_permission_required=connection.provider != "kraken_spot",
    )
    connection.last_verified_at = readiness.checked_at
    connection.last_readiness_verdict = readiness.verdict
    connection.last_readiness_report = _serialize_readiness(readiness)

    await _record_audit(
        db=db,
        action="exchange_connection_account_refreshed",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "account_status": connection.account_status,
            "status": connection.status,
            "readiness_verdict": connection.last_readiness_verdict,
        },
        actor=actor,
    )

    await db.commit()
    await db.refresh(connection)
    return _to_response(connection)


async def refresh_exchange_permissions(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    actor: str = "system",
) -> ExchangeConnectionResponse:
    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    credentials = _decrypt_credentials(connection)
    provider = get_exchange_provider(connection.provider)

    before_state = {
        "api_permissions": list(connection.api_permissions or []),
    }

    auth_result = await provider.test_authentication(credentials=credentials, environment=connection.environment)
    _apply_auth_result(connection, auth_result)

    if auth_result.authenticated:
        snapshot = await provider.fetch_permissions(credentials=credentials, environment=connection.environment)
        connection.api_permissions = snapshot.permissions
    product_snapshot = None
    if auth_result.authenticated:
        product_snapshot = await provider.fetch_product(credentials=credentials, environment=connection.environment, product_id="BTC-USD")

    readiness = await _build_and_persist_readiness_for_auth_result(
        provider=connection.provider,
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=len(connection.balances or []) > 0,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_has_balance_currency(connection, "USD"),
        usd_balance_funded=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_has_balance_currency(connection, "BTC"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=bool(product_snapshot and product_snapshot.available),
        product_trading_enabled=bool(product_snapshot and product_snapshot.trading_enabled),
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
        trade_permission_required=connection.provider != "kraken_spot",
    )
    connection.last_verified_at = readiness.checked_at
    connection.last_readiness_verdict = readiness.verdict
    connection.last_readiness_report = _serialize_readiness(readiness)

    await _record_audit(
        db=db,
        action="exchange_connection_permissions_refreshed",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "api_permissions": list(connection.api_permissions or []),
            "status": connection.status,
            "readiness_verdict": connection.last_readiness_verdict,
        },
        actor=actor,
    )

    await db.commit()
    await db.refresh(connection)
    return _to_response(connection)


async def _build_and_persist_readiness_for_auth_result(
    *,
    provider: str,
    auth_result: ExchangeAuthResult,
    credentials_stored: bool,
    encryption_key_configured: bool,
    accounts_retrieved: bool,
    balances_retrieved: bool,
    permissions_retrieved: bool,
    usd_balance_retrieved: bool,
    usd_balance_funded: bool,
    btc_balance_retrieved: bool,
    dangerous_permissions_detected: bool,
    product_btc_usd_available: bool,
    product_trading_enabled: bool,
    account_restricted: bool,
    rate_limit_status_available: bool,
    trade_permission_required: bool,
) -> ExchangeReadinessReportResponse:
    provider_label = _provider_label(provider)
    checks: list[ExchangeReadinessCheckResponse] = []
    checks.append(
        readiness_check(
            code="credentials_stored",
            label="Credentials Stored",
            status="pass" if credentials_stored else "fail",
            explanation="Encrypted credentials are present." if credentials_stored else "No encrypted credentials are stored.",
            remediation=f"Save {provider_label} API credentials in Exchange Connections.",
        )
    )
    checks.append(
        readiness_check(
            code="encryption_key_configured",
            label="Encryption Key Configured",
            status="pass" if encryption_key_configured else "fail",
            explanation="Credential encryption key is configured." if encryption_key_configured else "Credential encryption key is missing.",
            remediation="Set EXCHANGE_CREDENTIALS_ENCRYPTION_KEY in backend environment.",
        )
    )
    checks.append(
        readiness_check(
            code="jwt_generation",
            label="Credential Signing",
            status="pass" if auth_result.error is None else "fail",
            explanation="Credential signing inputs generated for the current request binding." if auth_result.error is None else "Credential signing failed.",
            remediation=f"Confirm the stored {provider_label} credential format and secret encoding.",
        )
    )
    checks.append(
        readiness_check(
            code="api_reachable",
            label="API Reachable",
            status="pass" if auth_result.reachable else "fail",
            explanation=f"{provider_label} API endpoint reachable." if auth_result.reachable else f"{provider_label} API unreachable.",
            remediation=f"Check egress network access and {provider_label} status information.",
        )
    )
    checks.append(
        readiness_check(
            code="authentication_valid",
            label="Authentication Valid",
            status="pass" if auth_result.authenticated else "fail",
            explanation="Authenticated read-only request succeeded." if auth_result.authenticated else "Authentication failed.",
            remediation=f"Verify the stored {provider_label} credential pair and account access.",
        )
    )
    checks.append(
        readiness_check(
            code="accounts_retrieved",
            label="Accounts Retrieved",
            status="pass" if accounts_retrieved else "fail",
            explanation="Account list retrieval succeeded." if accounts_retrieved else "Account list retrieval failed.",
            remediation="Ensure account read permission is enabled for key.",
        )
    )
    checks.append(
        readiness_check(
            code="balances_retrieved",
            label="Balances Retrieved",
            status="pass" if balances_retrieved else "warn",
            explanation="Balances retrieved successfully." if balances_retrieved else "Balances were not retrieved in this verification step.",
            remediation="Run Verify Connection or Refresh Balances.",
        )
    )
    checks.append(
        readiness_check(
            code="permissions_retrieved",
            label="Permissions Retrieved",
            status="pass" if permissions_retrieved else "fail",
            explanation=(
                "Permissions were retrieved."
                if permissions_retrieved
                else "Permissions endpoint did not return values; permission state is unknown."
            ),
            remediation=f"Use a {provider_label} key that supports permission introspection where available, then rerun verification.",
        )
    )

    clock_ok = auth_result.clock_skew_seconds is None or auth_result.clock_skew_seconds <= _DEFAULT_CLOCK_SKEW_FAIL_SECONDS
    checks.append(
        readiness_check(
            code="clock_synchronized",
            label="Clock Synchronized",
            status="pass" if clock_ok else "fail",
            explanation=(
                "Clock skew within acceptable tolerance."
                if clock_ok
                else f"Clock skew too high ({auth_result.clock_skew_seconds}s)."
            ),
            remediation="Synchronize server time using NTP before production verification.",
        )
    )

    checks.append(
        readiness_check(
            code="dangerous_permissions_detected",
            label="Dangerous Permissions",
            status="fail" if dangerous_permissions_detected else "pass",
            explanation=(
                "No dangerous transfer or withdrawal permission detected."
                if not dangerous_permissions_detected
                else "Withdrawal or transfer permission detected; this key is not eligible for automatic readiness."
            ),
            remediation=f"Use a least-privilege {provider_label} key and remove dangerous funding scopes.",
        )
    )
    checks.append(
        readiness_check(
            code="trade_permission_present",
            label="Trade Permission Present",
            status=("pass" if auth_result.trade_permission_present else ("fail" if trade_permission_required else "warn")),
            explanation=(
                "Trade permission is present."
                if auth_result.trade_permission_present
                else (
                    "Trade permission could not be verified from provider-visible permission evidence."
                    if not trade_permission_required
                    else "Trade permission not present."
                )
            ),
            remediation=f"Enable the minimum {provider_label} order permission required for preview and dry-run readiness.",
        )
    )

    checks.append(
        readiness_check(
            code="usd_balance_retrieved",
            label="USD Balance Retrieved",
            status="pass" if usd_balance_retrieved else "fail",
            explanation="USD balance was retrieved; the balance may be zero." if usd_balance_retrieved else "USD balance could not be observed.",
            remediation=f"Confirm {provider_label} USD cash-balance visibility.",
        )
    )
    checks.append(
        readiness_check(
            code="usd_balance_funded",
            label="USD Balance Funded",
            status="pass" if usd_balance_funded else "fail",
            explanation="USD balance is funded for a small live trade." if usd_balance_funded else "USD balance is observable but unfunded or below the current live-trade threshold.",
            remediation=f"Fund the {provider_label} USD cash balance before live dry-run or trade execution.",
        )
    )
    checks.append(
        readiness_check(
            code="btc_balance_retrieved",
            label="BTC Balance Retrieved",
            status="pass" if btc_balance_retrieved else "fail",
            explanation="BTC balance endpoint returned successfully." if btc_balance_retrieved else "BTC balance is unavailable.",
            remediation=f"Confirm {provider_label} BTC balance visibility for read checks.",
        )
    )
    checks.append(
        readiness_check(
            code="product_btc_usd_available",
            label="BTC-USD Product Available",
            status="pass" if product_btc_usd_available else "fail",
            explanation=f"BTC-USD is available on {provider_label}." if product_btc_usd_available else "BTC-USD product endpoint unavailable.",
            remediation=f"Confirm BTC-USD product availability on the connected {provider_label} account.",
        )
    )
    checks.append(
        readiness_check(
            code="product_trading_enabled",
            label="BTC-USD Trading Enabled",
            status="pass" if product_trading_enabled else "fail",
            explanation="BTC-USD trading is enabled for this account." if product_trading_enabled else "BTC-USD trading appears disabled.",
            remediation="Resolve product trading restrictions before dry run.",
        )
    )
    checks.append(
        readiness_check(
            code="account_restricted",
            label="Account Not Restricted",
            status="pass" if not account_restricted else "fail",
            explanation="Account status is not restricted." if not account_restricted else "Account appears restricted.",
            remediation=f"Resolve account restrictions in {provider_label} before proceeding.",
        )
    )
    checks.append(
        readiness_check(
            code="rate_limit_status_available",
            label="Rate Limit Status",
            status="pass" if rate_limit_status_available else "warn",
            explanation="Rate-limit metadata is available from recent API checks." if rate_limit_status_available else "Rate-limit metadata not available from provider headers.",
            remediation="Re-run verification to capture latest provider headers.",
        )
    )

    return build_report(checks=checks)


async def verify_exchange_connection(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    actor: str = "system",
) -> ExchangeConnectionResponse:
    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    credentials = _decrypt_credentials(connection)
    provider = get_exchange_provider(connection.provider)

    before_state = {
        "status": connection.status,
        "readiness_verdict": connection.last_readiness_verdict,
    }

    auth_result = await provider.test_authentication(credentials=credentials, environment=connection.environment)
    _apply_auth_result(connection, auth_result)

    accounts_retrieved = auth_result.authenticated
    permissions_retrieved = len(auth_result.permissions) > 0
    balances_retrieved = False
    product_btc_usd_available = False
    product_trading_enabled = False

    if auth_result.authenticated:
        account_snapshot = await provider.fetch_account(credentials=credentials, environment=connection.environment)
        connection.account_status = account_snapshot.account_status

        permission_snapshot = await provider.fetch_permissions(credentials=credentials, environment=connection.environment)
        connection.api_permissions = permission_snapshot.permissions
        permissions_retrieved = len(permission_snapshot.permissions) > 0

        balances_snapshot = await provider.fetch_balances(credentials=credentials, environment=connection.environment)
        balances_retrieved = True
        connection.balances = [
            {
                "currency": item.currency,
                "available": format(item.available, "f"),
                "reserved": format(item.reserved, "f"),
                "total": format(item.total, "f"),
            }
            for item in balances_snapshot.balances
        ]
        connection.total_equity_usd = None if balances_snapshot.total_equity_usd is None else format(balances_snapshot.total_equity_usd, "f")
        product_snapshot = await provider.fetch_product(credentials=credentials, environment=connection.environment, product_id="BTC-USD")
        product_btc_usd_available = product_snapshot.available
        product_trading_enabled = product_snapshot.trading_enabled

    readiness = await _build_and_persist_readiness_for_auth_result(
        provider=connection.provider,
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=accounts_retrieved,
        balances_retrieved=balances_retrieved,
        permissions_retrieved=permissions_retrieved,
        usd_balance_retrieved=_has_balance_currency(connection, "USD"),
        usd_balance_funded=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_has_balance_currency(connection, "BTC"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=product_btc_usd_available,
        product_trading_enabled=product_trading_enabled,
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
        trade_permission_required=connection.provider != "kraken_spot",
    )
    connection.last_verified_at = readiness.checked_at
    connection.last_readiness_verdict = readiness.verdict
    connection.last_readiness_report = _serialize_readiness(readiness)

    await _record_audit(
        db=db,
        action="exchange_connection_tested",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "status": connection.status,
            "readiness_verdict": connection.last_readiness_verdict,
            "authenticated": auth_result.authenticated,
        },
        actor=actor,
    )

    if connection.last_readiness_verdict in {"READY_FOR_PREVIEW", "READY_FOR_DRY_RUN"}:
        await _record_audit(
            db=db,
            action="CONNECTION_VERIFIED",
            entity_id=connection.exchange_connection_id,
            before_state=None,
            after_state={
                "readiness_verdict": connection.last_readiness_verdict,
                "environment": connection.environment,
            },
            actor=actor,
        )

    await db.commit()
    await db.refresh(connection)
    return _to_response(connection)


async def get_exchange_readiness(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
) -> ExchangeReadinessReportResponse:
    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    return _readiness_from_connection(connection)


async def rotate_exchange_credentials(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    payload: RotateExchangeCredentialsRequest,
    actor: str = "system",
) -> ExchangeConnectionResponse:
    if payload.confirm_replace is not True:
        raise InvalidRequestError(message="Credential rotation requires confirm_replace=true", details={"confirm_replace": payload.confirm_replace})

    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    provider = get_exchange_provider(connection.provider)

    auth_result = await provider.test_authentication(
        credentials={
            "api_key": payload.api_key_name,
            "api_secret": payload.private_key,
            "passphrase": payload.passphrase or "",
        },
        environment=connection.environment,
    )

    before_state = {
        "api_key_masked": connection.api_key_masked,
        "status": connection.status,
    }

    connection.credentials_encrypted = encrypt_credential_payload(
        json.dumps(
            {
                "api_key_name": payload.api_key_name,
                "private_key": payload.private_key,
                "passphrase": payload.passphrase or "",
            }
        )
    )
    connection.api_key_masked = _mask_api_key(payload.api_key_name)
    connection.api_secret_masked = _mask_secret(payload.private_key)
    connection.passphrase_configured = bool(payload.passphrase)
    _apply_auth_result(connection, auth_result)

    readiness = await _build_and_persist_readiness_for_auth_result(
        provider=connection.provider,
        auth_result=auth_result,
        credentials_stored=True,
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=len(connection.balances or []) > 0,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_has_balance_currency(connection, "USD"),
        usd_balance_funded=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_has_balance_currency(connection, "BTC"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=False,
        product_trading_enabled=False,
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
        trade_permission_required=connection.provider != "kraken_spot",
    )
    connection.last_verified_at = readiness.checked_at
    connection.last_readiness_verdict = readiness.verdict
    connection.last_readiness_report = _serialize_readiness(readiness)

    await _record_audit(
        db=db,
        action="CREDENTIAL_ROTATED",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "api_key_masked": connection.api_key_masked,
            "status": connection.status,
            "readiness_verdict": connection.last_readiness_verdict,
        },
        actor=actor,
    )

    await db.commit()
    await db.refresh(connection)
    return _to_response(connection)


async def disconnect_exchange_connection(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    payload: DisconnectExchangeConnectionRequest,
    actor: str = "system",
) -> DisconnectExchangeConnectionResponse:
    if payload.confirm_disconnect is not True:
        raise InvalidRequestError(message="Disconnect requires confirm_disconnect=true", details={"confirm_disconnect": payload.confirm_disconnect})

    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)

    before_state = {
        "status": connection.status,
        "credentials_valid": connection.credentials_valid,
        "api_key_masked": connection.api_key_masked,
    }

    connection.credentials_encrypted = ""
    connection.api_key_masked = "Disconnected"
    connection.api_secret_masked = "Disconnected"
    connection.passphrase_configured = False
    connection.status = "disconnected"
    connection.credentials_valid = False
    connection.account_status = None
    connection.api_permissions = []
    connection.balances = []
    connection.total_equity_usd = None
    connection.last_api_error = None
    connection.last_successful_sync_at = None
    connection.last_heartbeat_at = None
    connection.last_verified_at = datetime.now(timezone.utc)
    connection.last_readiness_verdict = "NOT_CONFIGURED"
    connection.last_readiness_report = _serialize_readiness(_default_readiness())

    await _record_audit(
        db=db,
        action="CONNECTION_DISCONNECTED",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "status": connection.status,
            "credentials_valid": connection.credentials_valid,
            "api_key_masked": connection.api_key_masked,
        },
        actor=actor,
    )

    await db.commit()
    return DisconnectExchangeConnectionResponse(
        exchange_connection_id=connection.exchange_connection_id,
        disconnected=True,
        message="Credentials removed locally. Revoke the API key in Coinbase separately if needed.",
    )


async def _resolve_unique_live_trading_profile(*, db: AsyncSession) -> LiveTradingProfile:
    """External-trade reconciliation is deliberately narrow: this codebase has
    no explicit ExchangeConnection -> LiveTradingProfile link (LiveTradingProfile
    only stores paper_account_id, never provider/environment/exchange_connection_id),
    so the only safe, fail-closed resolution available is "there must be exactly
    one live-mode profile in the whole system" -- the same "exactly one, else
    reject" idiom instant_trades.py's _resolve_connection already uses for
    provider+environment. Building a general connection<->profile mapping is
    out of scope; if this ever needs to support more than one live profile,
    that mapping has to be designed and added deliberately, not inferred here."""
    rows = list(await db.scalars(select(LiveTradingProfile).where(LiveTradingProfile.operating_mode == "live")))
    if len(rows) != 1:
        raise InvalidRequestError(
            message="External trade cannot be associated with exactly one live trading profile",
            details={"candidate_live_trading_profile_count": len(rows)},
        )
    return rows[0]


async def reconcile_external_trade(
    *, db: AsyncSession, exchange_connection_id: uuid.UUID, payload: ReconcileExternalTradeRequest, actor: str,
) -> ReconcileExternalTradeResponse:
    """Operator-attested recovery bridge for a trade executed directly against
    the provider (e.g. manually in Kraken's own UI), never submitted through
    this app, and therefore invisible to every existing reconciliation path --
    all of which are scoped to LiveCryptoOrder rows OmniTrade itself created
    (see discover_reconciliation_candidates, lookup_order/list_fills callers
    throughout this codebase). This function creates exactly one such row,
    with provenance that can never be mistaken for Risk Engine, mandate, or
    Controlled Proof authority, then hands off to the same canonical
    reconcile_live_order_and_fills path every other order uses -- it never
    computes or inserts LiveAccountingRecord rows itself."""
    provider_order_id = payload.provider_order_id.strip()
    if not provider_order_id:
        raise InvalidRequestError(message="provider_order_id is required", details={})

    connection = await _load_connection(db=db, exchange_connection_id=exchange_connection_id)
    if connection.environment != "production":
        raise InvalidRequestError(
            message="External trade reconciliation requires a production exchange connection",
            details={"environment": connection.environment},
        )
    if connection.status != "connected":
        raise InvalidRequestError(message="Exchange connection is not connected", details={"status": connection.status})
    if not connection.credentials_valid:
        raise InvalidRequestError(message="Exchange connection credentials are not valid", details={})

    # get_exchange_provider raises InvalidRequestError itself for an
    # unregistered provider -- "supported provider" is enforced for free.
    require_provider_capabilities(
        provider=connection.provider, operation="reconcile_external_trade",
        required=("order_lookup_history", "fill_lookup"), environment=connection.environment,
    )

    profile = await _resolve_unique_live_trading_profile(db=db)

    existing = await db.scalar(
        select(LiveCryptoOrder).where(LiveCryptoOrder.provider_order_id == provider_order_id).limit(1)
    )
    if existing is not None:
        raise ConflictError(
            message="An order with this provider_order_id has already been imported",
            details={
                "provider_order_id": provider_order_id,
                "existing_live_crypto_order_id": str(existing.live_crypto_order_id),
                "existing_status": existing.status,
            },
        )

    credentials = _decrypt_credentials(connection)
    provider = get_exchange_provider(connection.provider, environment=connection.environment)
    if not (hasattr(provider, "lookup_order") and hasattr(provider, "list_fills")):
        raise InvalidRequestError(
            message="Provider does not support direct order/fill lookup for external trade reconciliation",
            details={"provider": connection.provider},
        )

    provider_order = await provider.lookup_order(
        credentials=credentials, environment=connection.environment,
        provider_order_id=provider_order_id, client_order_id=None, product_id=None,
    )
    if provider_order is None:
        raise InvalidRequestError(message="Provider order not found", details={"provider_order_id": provider_order_id})
    if (provider_order.status or "").strip().upper() != "FILLED":
        raise InvalidRequestError(
            message="Provider order is not terminal and filled",
            details={"provider_order_id": provider_order_id, "observed_status": provider_order.status},
        )

    raw = provider_order.raw if isinstance(provider_order.raw, dict) else {}
    descr = raw.get("descr") if isinstance(raw.get("descr"), dict) else {}
    pair = descr.get("pair") if isinstance(descr.get("pair"), str) else None
    product_id = product_id_from_kraken_pair(pair)
    side = (provider_order.side or "").strip().upper()
    if product_id is None or side not in {"BUY", "SELL"}:
        raise InvalidRequestError(
            message="Provider order product or side could not be normalized safely",
            details={"provider_order_id": provider_order_id, "pair": pair, "side": provider_order.side},
        )

    fills = await provider.list_fills(
        credentials=credentials, environment=connection.environment, provider_order_id=provider_order_id,
    )
    valid_fills = [f for f in fills if f.provider_fill_id is not None and f.size > Decimal("0") and f.price > Decimal("0")]
    if not valid_fills:
        raise InvalidRequestError(message="No fills found for provider order", details={"provider_order_id": provider_order_id})

    total_filled_quantity = sum((f.size for f in valid_fills), Decimal("0"))
    total_quote_notional = sum((f.size * f.price for f in valid_fills), Decimal("0"))
    if total_filled_quantity <= Decimal("0") or total_quote_notional <= Decimal("0"):
        raise InvalidRequestError(
            message="Provider order quantities could not be normalized safely",
            details={"provider_order_id": provider_order_id},
        )

    order_type_raw = descr.get("ordertype") if isinstance(descr.get("ordertype"), str) else None
    order_type = order_type_raw.strip().upper() if order_type_raw else "MARKET"
    submitted_at = provider_order.submitted_at or datetime.now(timezone.utc)

    new_order = LiveCryptoOrder(
        crypto_order_preview_id=uuid.uuid5(uuid.NAMESPACE_URL, f"external-trade-preview:{provider_order_id}"),
        exchange_connection_id=connection.exchange_connection_id,
        provider=connection.provider,
        environment=connection.environment,
        product_id=product_id,
        side=side,
        order_type=order_type,
        requested_quote_size=total_quote_notional,
        client_order_id=f"external-trade-{provider_order_id}",
        status="SUBMITTED",
        risk_event_id=None,
        decision_record_id=None,
        validation_run_id=None,
        provider_order_id=provider_order_id,
        provider_status=provider_order.status,
        submitted_at=submitted_at,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={
            "authority_classification": "EXTERNALLY_EXECUTED_MANUAL_TRADE",
            "capital_campaign_id": None,
            "live_trading_profile_id": str(profile.id),
            "paper_account_id": str(profile.paper_account_id),
            "imported_by_actor": actor,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "provider_order_evidence": raw,
        },
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=None,
    )
    db.add(new_order)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            message="An order with this provider_order_id has already been imported",
            details={"provider_order_id": provider_order_id},
        ) from exc

    live_crypto_order_id = new_order.live_crypto_order_id

    # Never computes fill quantities, fees, or LiveAccountingRecord rows here --
    # hands off immediately to the exact same reconciliation entrypoint the
    # scheduler and the operator /reconcile route both use, so accounting
    # arithmetic has exactly one implementation in this codebase.
    from app.schemas.live_crypto_orders import LiveCryptoOrderReconcileRequest
    from app.services.live_crypto_orders import LiveCryptoOrderService

    order_service = LiveCryptoOrderService()
    reconcile_response = await order_service.reconcile(
        db=db,
        live_crypto_order_id=live_crypto_order_id,
        request=LiveCryptoOrderReconcileRequest(operator_identity=actor),
    )

    await _record_audit(
        db=db,
        action="external_trade_reconciled",
        entity_id=live_crypto_order_id,
        before_state=None,
        after_state={
            "exchange_connection_id": str(connection.exchange_connection_id),
            "provider_order_id": provider_order_id,
            "product_id": product_id,
            "side": side,
            "authority_classification": "EXTERNALLY_EXECUTED_MANUAL_TRADE",
            "live_trading_profile_id": str(profile.id),
            "total_filled_quantity": format(total_filled_quantity, "f"),
            "total_quote_notional": format(total_quote_notional, "f"),
            "provider_fill_evidence": [dict(f.raw) if isinstance(f.raw, dict) else {} for f in valid_fills],
            "resulting_live_crypto_order_id": str(live_crypto_order_id),
            "resulting_status": reconcile_response.live_crypto_order.status,
            "reconciliation_status": reconcile_response.reconciliation_status,
            "accounting_completion_status": reconcile_response.accounting_completion_status,
        },
        actor=actor,
    )
    await db.commit()

    return ReconcileExternalTradeResponse(
        live_crypto_order=reconcile_response.live_crypto_order,
        provider_order_id=provider_order_id,
        product_id=product_id,
        side=side,
        live_trading_profile_id=profile.id,
        reconciliation_status=reconcile_response.reconciliation_status,
        accounting_completion_status=reconcile_response.accounting_completion_status,
        provider_fill_observed=reconcile_response.provider_fill_observed,
    )
