from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.exchange_connection import ExchangeConnection
from app.schemas.exchange_connections import (
    ExchangeBalanceResponse,
    ExchangeConnectionListResponse,
    ExchangeConnectionResponse,
    ExchangeCredentialMaskResponse,
    ExchangeReadinessCheckResponse,
    SaveExchangeConnectionRequest,
    TestExchangeConnectionRequest,
    TestExchangeConnectionResponse,
)
from app.services.exchange_connections.crypto import decrypt_credential_payload, encrypt_credential_payload
from app.services.exchange_connections.providers.base import ExchangeAuthResult
from app.services.exchange_connections.providers.registry import get_exchange_provider


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


def _build_readiness(connection: ExchangeConnection) -> list[ExchangeReadinessCheckResponse]:
    now = datetime.now(timezone.utc)
    has_heartbeat = connection.last_heartbeat_at is not None
    heartbeat_fresh = False
    if connection.last_heartbeat_at is not None:
        heartbeat_fresh = (now - connection.last_heartbeat_at.astimezone(timezone.utc)) <= timedelta(minutes=15)

    balances_retrieved = len(connection.balances or []) > 0
    permissions_verified = len(connection.api_permissions or []) > 0

    return [
        ExchangeReadinessCheckResponse(
            code="exchange_connected",
            label="Exchange Connected",
            ok=connection.status == "connected",
            detail="Connected" if connection.status == "connected" else "Disconnected",
        ),
        ExchangeReadinessCheckResponse(
            code="credentials_valid",
            label="Credentials Valid",
            ok=bool(connection.credentials_valid),
            detail="Validated" if connection.credentials_valid else "Not validated",
        ),
        ExchangeReadinessCheckResponse(
            code="balances_retrieved",
            label="Balances Retrieved",
            ok=balances_retrieved,
            detail="Balances available" if balances_retrieved else "Refresh balances required",
        ),
        ExchangeReadinessCheckResponse(
            code="permissions_verified",
            label="Permissions Verified",
            ok=permissions_verified,
            detail="Permissions available" if permissions_verified else "Refresh permissions required",
        ),
        ExchangeReadinessCheckResponse(
            code="time_synced",
            label="Time Synced",
            ok=has_heartbeat and heartbeat_fresh,
            detail="Heartbeat fresh" if has_heartbeat and heartbeat_fresh else "Heartbeat missing or stale",
        ),
        ExchangeReadinessCheckResponse(
            code="api_reachable",
            label="API Reachable",
            ok=connection.last_api_error is None,
            detail="Reachable" if connection.last_api_error is None else "Last API call failed",
        ),
    ]


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
            api_key=connection.api_key_masked,
            api_secret=connection.api_secret_masked,
            passphrase="********" if connection.passphrase_configured else None,
        ),
        api_permissions=list(connection.api_permissions or []),
        account_status=connection.account_status,
        balances=balances,
        total_equity_usd=_safe_decimal(connection.total_equity_usd),
        last_successful_sync_at=connection.last_successful_sync_at,
        last_heartbeat_at=connection.last_heartbeat_at,
        last_api_error=connection.last_api_error,
        readiness_checks=_build_readiness(connection),
        updated_at=connection.updated_at,
    )


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
            "api_key": payload.api_key,
            "api_secret": payload.api_secret,
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
        "api_key": str(parsed.get("api_key", "")),
        "api_secret": str(parsed.get("api_secret", "")),
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
            "api_key": payload.api_key,
            "api_secret": payload.api_secret,
            "passphrase": payload.passphrase or "",
        },
        environment=payload.environment,
    )

    credentials_encrypted = encrypt_credential_payload(
        json.dumps(
            {
                "api_key": payload.api_key,
                "api_secret": payload.api_secret,
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
        api_key_masked=_mask_api_key(payload.api_key),
        api_secret_masked=_mask_secret(payload.api_secret),
        passphrase_configured=bool(payload.passphrase),
        credentials_valid=auth_result.authenticated,
        api_permissions=auth_result.permissions,
        account_status=auth_result.account_status,
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=auth_result.heartbeat_at if auth_result.authenticated else None,
        last_heartbeat_at=auth_result.heartbeat_at,
        last_api_error=auth_result.error,
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

    if auth_result.authenticated:
        snapshot = await provider.fetch_balances(credentials=credentials, environment=connection.environment)
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

    await _record_audit(
        db=db,
        action="exchange_connection_balances_refreshed",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "balances": connection.balances,
            "total_equity_usd": connection.total_equity_usd,
            "status": connection.status,
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

    await _record_audit(
        db=db,
        action="exchange_connection_account_refreshed",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "account_status": connection.account_status,
            "status": connection.status,
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

    await _record_audit(
        db=db,
        action="exchange_connection_permissions_refreshed",
        entity_id=connection.exchange_connection_id,
        before_state=before_state,
        after_state={
            "api_permissions": list(connection.api_permissions or []),
            "status": connection.status,
        },
        actor=actor,
    )

    await db.commit()
    await db.refresh(connection)
    return _to_response(connection)
