from __future__ import annotations

import argparse
import asyncio
import inspect
from decimal import Decimal
from uuid import UUID

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.services.live_crypto_environment import (
    GeneratePreviewHelperRequest,
    InitializeLiveCryptoEnvironmentRequest,
    RecordApprovalHelperRequest,
    generate_fresh_btc_dry_run_preview,
    initialize_live_crypto_environment,
    inspect_live_crypto_environment,
    record_first_live_enablement_approval,
)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _print_readiness(readiness) -> None:
    print("Live Crypto Environment Readiness")
    for item in readiness.items:
        state = "READY" if item.ready else "MISSING"
        print(f"{item.label}: {state} - {item.detail}")
    print(f"Overall Ready: {str(readiness.ready).lower()}")


def _validate_safe_flags() -> tuple[bool, str | None]:
    settings = get_settings()
    if settings.live_crypto_order_submission_enabled:
        return False, "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false"
    if Decimal(str(settings.live_crypto_max_order_usd)) != Decimal("5"):
        return False, "LIVE_CRYPTO_MAX_ORDER_USD must equal 5"
    return True, None


async def _run(args: argparse.Namespace) -> int:
    safe, reason = _validate_safe_flags()
    if not safe:
        print(f"blocked: {reason}")
        return 2

    async with AsyncSessionLocal() as db:
        try:
            if args.create_preview:
                if args.exchange_connection_id is None:
                    readiness = await _maybe_await(inspect_live_crypto_environment(db=db, exchange_environment=args.exchange_environment))
                    if readiness.exchange_connection_id is None:
                        print("blocked: exchange connection missing; run --apply first")
                        return 2
                    exchange_connection_id = readiness.exchange_connection_id
                else:
                    exchange_connection_id = args.exchange_connection_id
                result = await _maybe_await(generate_fresh_btc_dry_run_preview(
                    db=db,
                    request=GeneratePreviewHelperRequest(
                        actor=args.actor,
                        exchange_connection_id=exchange_connection_id,
                    ),
                ))
                print(f"preview_created_id={result.crypto_order_preview_id}")
                print(f"preview_status={result.status}")
                return 0

            if args.create_approval:
                if args.live_trading_profile_id is None:
                    readiness = await _maybe_await(inspect_live_crypto_environment(db=db, exchange_environment=args.exchange_environment))
                    if readiness.live_trading_profile_id is None:
                        print("blocked: live trading profile missing; run --apply first")
                        return 2
                    live_trading_profile_id = readiness.live_trading_profile_id
                else:
                    live_trading_profile_id = args.live_trading_profile_id
                result = await _maybe_await(record_first_live_enablement_approval(
                    db=db,
                    request=RecordApprovalHelperRequest(
                        actor=args.actor,
                        live_trading_profile_id=live_trading_profile_id,
                    ),
                ))
                print(f"approval_event_id={result.approval_event_id}")
                print(f"approval_state={result.approval_state}")
                return 0

            if args.apply:
                result = await _maybe_await(initialize_live_crypto_environment(
                    db=db,
                    request=InitializeLiveCryptoEnvironmentRequest(
                        actor=args.actor,
                        exchange_environment=args.exchange_environment,
                        exchange_connection_name=args.exchange_connection_name,
                        exchange_api_key_name=args.exchange_api_key_name,
                        exchange_private_key=args.exchange_private_key,
                        exchange_passphrase=args.exchange_passphrase,
                        registration_source=args.registration_source,
                        campaign_owner=args.campaign_owner,
                    ),
                ))
                print(f"created_exchange_connection={str(result.created_exchange_connection).lower()}")
                print(f"created_asset={str(result.created_asset).lower()}")
                print(f"created_live_trading_profile={str(result.created_live_trading_profile).lower()}")
                print(f"created_capital_campaign={str(result.created_capital_campaign).lower()}")
                _print_readiness(result.readiness)
                return 0

            readiness = await _maybe_await(inspect_live_crypto_environment(db=db, exchange_environment=args.exchange_environment))
            _print_readiness(readiness)
            return 0
        except Exception as exc:
            print(f"safe_failure_reason={str(exc)}")
            return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and safely initialize live-crypto dry-run prerequisites")
    parser.add_argument("--apply", action="store_true", help="Create only missing operational objects")
    parser.add_argument("--create-preview", action="store_true", help="Generate a fresh BTC-USD BUY $5 preview via preview service")
    parser.add_argument("--create-approval", action="store_true", help="Record first-live-enablement approval via approval workflow")
    parser.add_argument("--exchange-environment", default="production", choices=["production", "sandbox"])
    parser.add_argument("--actor", default="operator:human")
    parser.add_argument("--exchange-connection-name", default="coinbase-production-primary")
    parser.add_argument("--exchange-api-key-name")
    parser.add_argument("--exchange-private-key")
    parser.add_argument("--exchange-passphrase")
    parser.add_argument("--registration-source", default="human_production_initializer")
    parser.add_argument("--campaign-owner", default="operator")
    parser.add_argument("--exchange-connection-id", type=UUID)
    parser.add_argument("--live-trading-profile-id", type=UUID)
    args = parser.parse_args(argv)

    active_modes = [args.apply, args.create_preview, args.create_approval]
    if sum(1 for item in active_modes if item) > 1:
        parser.error("Choose only one mode: --apply, --create-preview, or --create-approval")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
