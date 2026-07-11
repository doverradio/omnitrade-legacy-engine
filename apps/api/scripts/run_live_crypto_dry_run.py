from __future__ import annotations

import argparse
import asyncio
import inspect
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.schemas.live_crypto_orders import LiveCryptoOrderDryRunRequest
from app.services import live_crypto_orders as live_crypto_orders_service


async def _run_dry_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.live_crypto_order_submission_enabled:
        print("blocked: LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false")
        return 2
    if not settings.live_crypto_dry_run_enabled:
        print("blocked: LIVE_CRYPTO_DRY_RUN_ENABLED must be true")
        return 2
    if not settings.live_crypto_preparation_enabled:
        print("blocked: LIVE_CRYPTO_PREPARATION_ENABLED must be true")
        return 2
    if Decimal(str(settings.live_crypto_max_order_usd)) != Decimal("5"):
        print("blocked: LIVE_CRYPTO_MAX_ORDER_USD must equal 5")
        return 2

    async with AsyncSessionLocal() as db:
        try:
            response = live_crypto_orders_service.service.dry_run(
                db=db,
                request=LiveCryptoOrderDryRunRequest(
                    live_trading_profile_id=args.live_trading_profile_id,
                    crypto_order_preview_id=args.crypto_order_preview_id,
                    operator_identity=args.operator_identity,
                    idempotency_token=args.idempotency_token,
                ),
            )
            if inspect.isawaitable(response):
                response = await response
        except Exception as exc:
            print(f"safe_failure_reason={str(exc)}")
            return 1

    live_order = response.live_crypto_order
    safe = live_order.safe_provider_response or {}

    print(f"dry_run_mode={safe.get('mode', 'dry_run')}")
    print(f"submission_skipped={str(response.submission_skipped).lower()}")
    print(f"local_order_id={live_order.live_crypto_order_id}")
    print(f"client_order_id={live_order.client_order_id}")
    print(f"operator_identity={safe.get('operator_identity', args.operator_identity)}")
    print(f"product={live_order.product_id}")
    print(f"side={live_order.side}")
    print(f"quote_amount={format(live_order.requested_quote_size, 'f')}")
    print(f"approval_event_id={safe.get('approval_event_id')}")
    print(f"risk_event_id={safe.get('risk_event_id')}")
    print(f"readiness_result={safe.get('readiness_result')}")
    print(f"kill_switch_result={safe.get('kill_switch_result')}")
    print(
        "evidence_age_summary="
        f"preview={safe.get('preview_age_seconds')},"
        f"readiness={safe.get('readiness_age_seconds')},"
        f"heartbeat={safe.get('heartbeat_age_seconds')},"
        f"balance={safe.get('balance_age_seconds')},"
        f"price={safe.get('price_age_seconds')}"
    )
    print(f"cap={safe.get('max_order_usd')}")
    print(f"result_status={response.dry_run_status}")

    if response.dry_run_status != "DRY_RUN_READY":
        print(f"safe_failure_reason={response.live_crypto_order.failure_reason or response.dry_run_message}")
        return 1
    if response.provider_create_order_called or response.order_submitted:
        print("blocked: inconsistent dry-run state")
        return 1

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one safe production-equivalent live crypto dry run")
    parser.add_argument("--live-trading-profile-id", type=UUID, required=True)
    parser.add_argument("--crypto-order-preview-id", type=UUID, required=True)
    parser.add_argument("--operator-identity", type=str, required=True)
    parser.add_argument("--idempotency-token", type=str, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run_dry_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
