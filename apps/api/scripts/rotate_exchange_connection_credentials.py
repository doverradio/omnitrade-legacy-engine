from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.exchange_connection import ExchangeConnection
from app.services.exchange_connections.crypto import encrypt_credential_payload
from app.services.exchange_connections.service import get_decrypted_credentials_for_connection
from scripts.initialize_live_crypto_environment import _resolve_credentials


def _mask_api_key(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def _emit_result(
    *,
    connection_found: bool,
    connection_id: str | None,
    provider: str,
    environment: str,
    credentials_rotated: bool,
    credentials_already_current: bool,
    audit_recorded: bool,
    actor: str,
    success: bool,
    safe_failure_category: str | None,
) -> None:
    print(f"connection_found={str(connection_found).lower()}")
    print(f"connection_id={connection_id or 'none'}")
    print(f"provider={provider}")
    print(f"environment={environment}")
    print(f"credentials_rotated={str(credentials_rotated).lower()}")
    print(f"credentials_already_current={str(credentials_already_current).lower()}")
    print(f"audit_recorded={str(audit_recorded).lower()}")
    print(f"actor={actor}")
    print(f"success={str(success).lower()}")
    if safe_failure_category is not None:
        print(f"safe_failure_category={safe_failure_category}")


async def _find_matching_connections(*, db, provider: str, environment: str) -> list[ExchangeConnection]:
    result = await db.execute(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == provider)
        .where(ExchangeConnection.environment == environment)
        .order_by(ExchangeConnection.created_at.desc())
    )
    return list(result.scalars().all())


async def _record_rotation_audit(
    *,
    db,
    actor: str,
    connection: ExchangeConnection,
    action: str,
    before_state: dict[str, object],
    after_state: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="exchange_connection",
            entity_id=connection.exchange_connection_id,
            before_state=before_state,
            after_state=after_state,
        )
    )


def _load_credentials_from_settings(provider: str) -> tuple[str | None, str | None, str | None]:
    args = SimpleNamespace(
        provider=provider,
        exchange_api_key_name=None,
        exchange_api_key_name_env=None,
        exchange_private_key_env=None,
        exchange_passphrase_env=None,
        prompt_for_credentials=False,
    )
    return _resolve_credentials(args)


async def _run(args: argparse.Namespace) -> int:
    actor = str(args.actor or "").strip()
    if not actor:
        _emit_result(
            connection_found=False,
            connection_id=None,
            provider=args.provider,
            environment=args.environment,
            credentials_rotated=False,
            credentials_already_current=False,
            audit_recorded=False,
            actor="none",
            success=False,
            safe_failure_category="actor_required",
        )
        return 2

    if args.confirm_replace is not True:
        _emit_result(
            connection_found=False,
            connection_id=None,
            provider=args.provider,
            environment=args.environment,
            credentials_rotated=False,
            credentials_already_current=False,
            audit_recorded=False,
            actor=actor,
            success=False,
            safe_failure_category="confirmation_required",
        )
        return 2

    api_key, api_secret, passphrase = _load_credentials_from_settings(args.provider)
    if not api_key or not api_secret:
        _emit_result(
            connection_found=False,
            connection_id=None,
            provider=args.provider,
            environment=args.environment,
            credentials_rotated=False,
            credentials_already_current=False,
            audit_recorded=False,
            actor=actor,
            success=False,
            safe_failure_category="configuration_error",
        )
        return 2

    async with AsyncSessionLocal() as db:
        audit_recorded = False
        connection: ExchangeConnection | None = None
        try:
            matches = await _find_matching_connections(db=db, provider=args.provider, environment=args.environment)
            if len(matches) == 0:
                _emit_result(
                    connection_found=False,
                    connection_id=None,
                    provider=args.provider,
                    environment=args.environment,
                    credentials_rotated=False,
                    credentials_already_current=False,
                    audit_recorded=False,
                    actor=actor,
                    success=False,
                    safe_failure_category="missing_exchange_connection",
                )
                return 2
            if len(matches) > 1:
                _emit_result(
                    connection_found=False,
                    connection_id=None,
                    provider=args.provider,
                    environment=args.environment,
                    credentials_rotated=False,
                    credentials_already_current=False,
                    audit_recorded=False,
                    actor=actor,
                    success=False,
                    safe_failure_category="multiple_exchange_connections",
                )
                return 2

            connection = matches[0]
            existing = get_decrypted_credentials_for_connection(connection)
            existing_key = str(existing.get("api_key") or "").strip()
            existing_secret = str(existing.get("api_secret") or "").strip()
            existing_passphrase = str(existing.get("passphrase") or "").strip()

            already_current = (
                existing_key == api_key
                and existing_secret == api_secret
                and existing_passphrase == str(passphrase or "").strip()
            )

            before_state = {
                "provider": connection.provider,
                "environment": connection.environment,
                "api_key_masked": connection.api_key_masked,
            }

            if already_current:
                await _record_rotation_audit(
                    db=db,
                    actor=actor,
                    connection=connection,
                    action="CREDENTIAL_ROTATION_SKIPPED",
                    before_state=before_state,
                    after_state={
                        "provider": connection.provider,
                        "environment": connection.environment,
                        "credentials_already_current": True,
                    },
                )
                audit_recorded = True
                await db.commit()
                _emit_result(
                    connection_found=True,
                    connection_id=str(connection.exchange_connection_id),
                    provider=connection.provider,
                    environment=connection.environment,
                    credentials_rotated=False,
                    credentials_already_current=True,
                    audit_recorded=audit_recorded,
                    actor=actor,
                    success=True,
                    safe_failure_category=None,
                )
                return 0

            connection.credentials_encrypted = encrypt_credential_payload(
                json.dumps(
                    {
                        "api_key_name": api_key,
                        "private_key": api_secret,
                        "passphrase": passphrase or "",
                    }
                )
            )
            connection.api_key_masked = _mask_api_key(api_key)
            connection.api_secret_masked = "********"
            connection.passphrase_configured = bool(passphrase)

            await _record_rotation_audit(
                db=db,
                actor=actor,
                connection=connection,
                action="CREDENTIAL_ROTATED_MANUAL",
                before_state=before_state,
                after_state={
                    "provider": connection.provider,
                    "environment": connection.environment,
                    "api_key_masked": connection.api_key_masked,
                    "credentials_already_current": False,
                },
            )
            audit_recorded = True
            await db.commit()
            if hasattr(db, "refresh"):
                await db.refresh(connection)

            _emit_result(
                connection_found=True,
                connection_id=str(connection.exchange_connection_id),
                provider=connection.provider,
                environment=connection.environment,
                credentials_rotated=True,
                credentials_already_current=False,
                audit_recorded=audit_recorded,
                actor=actor,
                success=True,
                safe_failure_category=None,
            )
            return 0
        except Exception:
            if hasattr(db, "rollback"):
                await db.rollback()
            _emit_result(
                connection_found=connection is not None,
                connection_id=None if connection is None else str(connection.exchange_connection_id),
                provider=args.provider,
                environment=args.environment,
                credentials_rotated=False,
                credentials_already_current=False,
                audit_recorded=audit_recorded,
                actor=actor,
                success=False,
                safe_failure_category="rotation_failed",
            )
            return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rotate credentials for an existing exchange connection")
    parser.add_argument("--provider", required=True, choices=["coinbase_advanced", "kraken_spot"])
    parser.add_argument("--environment", required=True, choices=["sandbox", "production"])
    parser.add_argument("--actor", required=True)
    parser.add_argument("--confirm-replace", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
