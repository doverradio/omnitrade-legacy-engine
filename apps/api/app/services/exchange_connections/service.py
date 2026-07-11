from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.exchange_connection import ExchangeConnection
from app.schemas.exchange_connections import (
    DisconnectExchangeConnectionResponse,
    DisconnectExchangeConnectionRequest,
    ExchangeBalanceResponse,
    ExchangeConnectionListResponse,
    ExchangeConnectionResponse,
    ExchangeCredentialMaskResponse,
    ExchangeReadinessCheckResponse,
    ExchangeReadinessReportResponse,
    RotateExchangeCredentialsRequest,
    SaveExchangeConnectionRequest,
    TestExchangeConnectionRequest,
    TestExchangeConnectionResponse,
)
from app.services.exchange_connections.crypto import decrypt_credential_payload, encrypt_credential_payload
from app.services.exchange_connections.providers.base import ExchangeAuthResult
from app.services.exchange_connections.providers.registry import get_exchange_provider
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
        provider_label="Coinbase Advanced",
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
        auth_result=auth_result,
        credentials_stored=True,
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=False,
        permissions_retrieved=len(auth_result.permissions) > 0,
        usd_balance_retrieved=False,
        btc_balance_retrieved=False,
        dangerous_permissions_detected=_has_dangerous_permissions(auth_result.permissions),
        product_btc_usd_available=False,
        product_trading_enabled=False,
        account_restricted=_is_account_restricted(auth_result.account_status),
        rate_limit_status_available=False,
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
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=balances_retrieved,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_balance_amount(connection, "BTC") >= Decimal("0"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=product_btc_usd_available,
        product_trading_enabled=product_trading_enabled,
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
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
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=len(connection.balances or []) > 0,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_balance_amount(connection, "BTC") >= Decimal("0"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=bool(product_snapshot and product_snapshot.available),
        product_trading_enabled=bool(product_snapshot and product_snapshot.trading_enabled),
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
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
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=len(connection.balances or []) > 0,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_balance_amount(connection, "BTC") >= Decimal("0"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=bool(product_snapshot and product_snapshot.available),
        product_trading_enabled=bool(product_snapshot and product_snapshot.trading_enabled),
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
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
    auth_result: ExchangeAuthResult,
    credentials_stored: bool,
    encryption_key_configured: bool,
    accounts_retrieved: bool,
    balances_retrieved: bool,
    permissions_retrieved: bool,
    usd_balance_retrieved: bool,
    btc_balance_retrieved: bool,
    dangerous_permissions_detected: bool,
    product_btc_usd_available: bool,
    product_trading_enabled: bool,
    account_restricted: bool,
    rate_limit_status_available: bool,
) -> ExchangeReadinessReportResponse:
    checks: list[ExchangeReadinessCheckResponse] = []
    checks.append(
        readiness_check(
            code="credentials_stored",
            label="Credentials Stored",
            status="pass" if credentials_stored else "fail",
            explanation="Encrypted credentials are present." if credentials_stored else "No encrypted credentials are stored.",
            remediation="Save Coinbase API key name and private key in Exchange Connections.",
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
            label="JWT Generation",
            status="pass" if auth_result.error is None else "fail",
            explanation="JWT generated for current request binding." if auth_result.error is None else "JWT generation failed.",
            remediation="Confirm Coinbase key name, private key format, and newline escaping.",
        )
    )
    checks.append(
        readiness_check(
            code="api_reachable",
            label="API Reachable",
            status="pass" if auth_result.reachable else "fail",
            explanation="Coinbase API endpoint reachable." if auth_result.reachable else "Coinbase API unreachable.",
            remediation="Check egress network access and Coinbase status page.",
        )
    )
    checks.append(
        readiness_check(
            code="authentication_valid",
            label="Authentication Valid",
            status="pass" if auth_result.authenticated else "fail",
            explanation="Authenticated read-only request succeeded." if auth_result.authenticated else "Authentication failed.",
            remediation="Verify key name and private key pairing in Coinbase Developer Platform.",
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
            remediation="Use a key that supports permission introspection and rerun verification.",
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
                "No withdrawal/transfer permission detected."
                if not dangerous_permissions_detected
                else "Withdrawal or transfer permission detected; this key is not eligible for automatic readiness."
            ),
            remediation="Use least-privilege Coinbase key and disable withdrawal/transfer scopes.",
        )
    )
    checks.append(
        readiness_check(
            code="trade_permission_present",
            label="Trade Permission Present",
            status="pass" if auth_result.trade_permission_present else "fail",
            explanation=(
                "Trade permission is present."
                if auth_result.trade_permission_present
                else "Trade permission not present."
            ),
            remediation="Enable trade/order permission for preview and dry-run readiness.",
        )
    )

    checks.append(
        readiness_check(
            code="usd_balance_retrieved",
            label="USD Balance Retrieved",
            status="pass" if usd_balance_retrieved else "fail",
            explanation="USD balance was retrieved and is available." if usd_balance_retrieved else "USD balance is unavailable.",
            remediation="Confirm USD account access and non-zero available balance.",
        )
    )
    checks.append(
        readiness_check(
            code="btc_balance_retrieved",
            label="BTC Balance Retrieved",
            status="pass" if btc_balance_retrieved else "fail",
            explanation="BTC balance endpoint returned successfully." if btc_balance_retrieved else "BTC balance is unavailable.",
            remediation="Confirm BTC account access for read checks.",
        )
    )
    checks.append(
        readiness_check(
            code="product_btc_usd_available",
            label="BTC-USD Product Available",
            status="pass" if product_btc_usd_available else "fail",
            explanation="BTC-USD is available on Coinbase Advanced." if product_btc_usd_available else "BTC-USD product endpoint unavailable.",
            remediation="Confirm BTC-USD product availability on the connected account.",
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
            remediation="Resolve account restrictions in Coinbase before proceeding.",
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
        auth_result=auth_result,
        credentials_stored=bool(connection.credentials_encrypted),
        encryption_key_configured=True,
        accounts_retrieved=accounts_retrieved,
        balances_retrieved=balances_retrieved,
        permissions_retrieved=permissions_retrieved,
        usd_balance_retrieved=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_balance_amount(connection, "BTC") >= Decimal("0"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=product_btc_usd_available,
        product_trading_enabled=product_trading_enabled,
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
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
        auth_result=auth_result,
        credentials_stored=True,
        encryption_key_configured=True,
        accounts_retrieved=auth_result.authenticated,
        balances_retrieved=len(connection.balances or []) > 0,
        permissions_retrieved=len(connection.api_permissions or []) > 0,
        usd_balance_retrieved=_balance_amount(connection, "USD") > Decimal("0"),
        btc_balance_retrieved=_balance_amount(connection, "BTC") >= Decimal("0"),
        dangerous_permissions_detected=_has_dangerous_permissions(connection.api_permissions),
        product_btc_usd_available=False,
        product_trading_enabled=False,
        account_restricted=_is_account_restricted(connection.account_status),
        rate_limit_status_available=auth_result.reachable,
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
