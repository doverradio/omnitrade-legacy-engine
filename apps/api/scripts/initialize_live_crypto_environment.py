from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import os
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
    run_live_crypto_rehearsal,
)
from scripts.review_live_crypto_dry_run_evidence import verify_dry_run_evidence


_PROVIDER_DEFAULT_ENV = {
    "coinbase_advanced": {
        "api_key_name": "OT_COINBASE_API_KEY_NAME",
        "private_key": "OT_COINBASE_PRIVATE_KEY",
        "passphrase": "OT_COINBASE_PASSPHRASE",
    },
    "kraken_spot": {
        "api_key_name": "OT_KRAKEN_API_KEY",
        "private_key": "OT_KRAKEN_API_SECRET",
        "passphrase": "OT_KRAKEN_OTP",
    },
}

DEFAULT_API_KEY_ENV = "OT_COINBASE_API_KEY_NAME"
DEFAULT_PRIVATE_KEY_ENV = "OT_COINBASE_PRIVATE_KEY"
DEFAULT_PASSPHRASE_ENV = "OT_COINBASE_PASSPHRASE"


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _resolve_credentials(args: argparse.Namespace) -> tuple[str | None, str | None, str | None]:
    provider = getattr(args, "provider", "coinbase_advanced")
    defaults = _PROVIDER_DEFAULT_ENV.get(provider, _PROVIDER_DEFAULT_ENV["coinbase_advanced"])

    api_key_env = args.exchange_api_key_name_env or defaults["api_key_name"]
    private_key_env = args.exchange_private_key_env or defaults["private_key"]
    passphrase_env = args.exchange_passphrase_env or defaults["passphrase"]

    api_key_name = args.exchange_api_key_name or os.getenv(api_key_env)
    private_key = os.getenv(private_key_env)
    passphrase = os.getenv(passphrase_env)

    if args.prompt_for_credentials:
        if not api_key_name:
            api_key_name = input("Coinbase API key name: ").strip()
        if not private_key:
            private_key = getpass.getpass("Coinbase private key (hidden): ").strip()
        if passphrase is None:
            prompted = getpass.getpass("Coinbase passphrase (hidden, optional): ").strip()
            passphrase = prompted or None

    if api_key_name is not None:
        api_key_name = api_key_name.strip() or None
    if private_key is not None:
        private_key = private_key.strip() or None
    if passphrase is not None:
        passphrase = passphrase.strip() or None
    return api_key_name, private_key, passphrase


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
    if not settings.live_crypto_dry_run_enabled:
        return False, "LIVE_CRYPTO_DRY_RUN_ENABLED must be true"
    if not settings.live_crypto_preparation_enabled:
        return False, "LIVE_CRYPTO_PREPARATION_ENABLED must be true"
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
            provider = getattr(args, "provider", "coinbase_advanced")
            if getattr(args, "run_rehearsal", False):
                if args.exchange_environment != "sandbox":
                    print("blocked: rehearsal requires --exchange-environment sandbox")
                    return 2
                api_key_name, private_key, passphrase = _resolve_credentials(args)
                result = await _maybe_await(run_live_crypto_rehearsal(
                    db=db,
                    request=InitializeLiveCryptoEnvironmentRequest(
                        actor=args.actor,
                        provider=provider,
                        paper_account_id=args.paper_account_id,
                        exchange_environment=args.exchange_environment,
                        exchange_connection_name=args.exchange_connection_name,
                        exchange_api_key_name=api_key_name,
                        exchange_private_key=private_key,
                        exchange_passphrase=passphrase,
                        registration_source=args.registration_source,
                        campaign_owner=args.campaign_owner,
                    ),
                    verify_rehearsal_evidence=verify_dry_run_evidence,
                ))
                print(f"rehearsal_mode={result.rehearsal_mode}")
                print(f"preview_created={str(result.preview_created).lower()}")
                print(f"approval_created={str(result.approval_created).lower()}")
                print(f"preview_id={result.preview_id}")
                print(f"approval_event_id={result.approval_event_id}")
                print(f"live_crypto_order_id={result.live_crypto_order_id}")
                print(f"audit_correlation_id={result.audit_correlation_id}")
                print(f"dry_run_status={result.dry_run_status}")
                print(f"review_summary={'PASS' if result.review_passed else 'FAIL'}")
                print(f"review_check_count={result.review_check_count}")
                print(f"production_ready={str(result.production_ready).lower()}")
                print("sandbox_rehearsal_only=true")
                return 0 if result.review_passed and not result.production_ready else 1

            if args.create_preview:
                if args.exchange_connection_id is None:
                    readiness = await _maybe_await(
                        inspect_live_crypto_environment(
                            db=db,
                            provider=provider,
                            exchange_environment=args.exchange_environment,
                            paper_account_id=args.paper_account_id,
                        )
                    )
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
                        exchange_environment=args.exchange_environment,
                    ),
                ))
                print(f"preview_created_id={result.crypto_order_preview_id}")
                print(f"preview_status={result.status}")
                return 0

            if args.create_approval:
                if args.live_trading_profile_id is None:
                    readiness = await _maybe_await(
                        inspect_live_crypto_environment(
                            db=db,
                            provider=provider,
                            exchange_environment=args.exchange_environment,
                            paper_account_id=args.paper_account_id,
                        )
                    )
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
                        exchange_environment=args.exchange_environment,
                    ),
                ))
                print(f"approval_event_id={result.approval_event_id}")
                print(f"approval_state={result.approval_state}")
                return 0

            if args.apply:
                api_key_name, private_key, passphrase = _resolve_credentials(args)
                result = await _maybe_await(initialize_live_crypto_environment(
                    db=db,
                    request=InitializeLiveCryptoEnvironmentRequest(
                        actor=args.actor,
                        provider=provider,
                        paper_account_id=args.paper_account_id,
                        exchange_environment=args.exchange_environment,
                        exchange_connection_name=args.exchange_connection_name,
                        exchange_api_key_name=api_key_name,
                        exchange_private_key=private_key,
                        exchange_passphrase=passphrase,
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

            readiness = await _maybe_await(
                inspect_live_crypto_environment(
                    db=db,
                    provider=provider,
                    exchange_environment=args.exchange_environment,
                    paper_account_id=args.paper_account_id,
                )
            )
            _print_readiness(readiness)
            if args.exchange_environment == "sandbox":
                production_readiness = await _maybe_await(
                    inspect_live_crypto_environment(
                        db=db,
                        provider=provider,
                        exchange_environment="production",
                        paper_account_id=args.paper_account_id,
                    )
                )
                print(f"production_ready={str(production_readiness.ready).lower()}")
                print("sandbox_rehearsal_only=true")
            return 0
        except Exception as exc:
            print("safe_failure_reason=initialization_failed")
            print(f"error_type={type(exc).__name__}")
            return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect and safely initialize live-crypto dry-run prerequisites",
        allow_abbrev=False,
    )
    parser.add_argument("--apply", action="store_true", help="Create only missing operational objects")
    parser.add_argument("--create-preview", action="store_true", help="Generate a fresh BTC-USD BUY $5 preview via preview service")
    parser.add_argument("--create-approval", action="store_true", help="Record first-live-enablement approval via approval workflow")
    parser.add_argument("--run-rehearsal", action="store_true", help="Run full sandbox/mock rehearsal including preview, approval, dry run, and evidence review")
    parser.add_argument("--provider", default="coinbase_advanced", choices=["coinbase_advanced", "kraken_spot"])
    parser.add_argument("--exchange-environment", default="production", choices=["production", "sandbox"])
    parser.add_argument("--actor", default="operator:human")
    parser.add_argument("--paper-account-id", type=UUID, default=UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73"))
    parser.add_argument("--exchange-connection-name")
    parser.add_argument("--exchange-api-key-name")
    parser.add_argument("--exchange-api-key-name-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--exchange-private-key-env", default=DEFAULT_PRIVATE_KEY_ENV)
    parser.add_argument("--exchange-passphrase-env", default=DEFAULT_PASSPHRASE_ENV)
    parser.add_argument("--prompt-for-credentials", action="store_true")
    parser.add_argument("--registration-source", default="human_production_initializer")
    parser.add_argument("--campaign-owner", default="operator")
    parser.add_argument("--exchange-connection-id", type=UUID)
    parser.add_argument("--live-trading-profile-id", type=UUID)
    args = parser.parse_args(argv)

    active_modes = [args.apply, args.create_preview, args.create_approval, args.run_rehearsal]
    if sum(1 for item in active_modes if item) > 1:
        parser.error("Choose only one mode: --apply, --create-preview, --create-approval, or --run-rehearsal")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
