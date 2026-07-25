"""Idempotent Kraken Asset Registry commissioning for the bounded Phase-1
live multi-asset roster (app.services.orchestration.asset_roster).

Verifies live Kraken tradability via the existing KrakenSpotClient
(read-only, no credentials required for public product metadata) before
inserting a single canonical Asset row. Never overwrites a conflicting
existing row -- a symbol/exchange collision with different metadata fails
closed and prints the conflict, exactly like scripts/seed_assets.py's own
asset_exists guard for the binance_us roster.

Usage:
    python scripts/commission_kraken_asset.py --product-id ETH-USD

Prints the resulting canonical Asset ID (existing or newly created) and
exits non-zero if Kraken does not currently report the product as
available and trading-enabled, or if a conflicting row already exists.
Does not touch campaign/mandate authority -- that remains a separate,
manual operator step (see operator_cli's mandate/campaign commands).
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.asset import Asset
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient
from app.services.orchestration import asset_roster

logger = logging.getLogger(__name__)


async def commission_kraken_asset(
    db_session: AsyncSession, *, product_id: str, environment: str = "production",
) -> tuple[str, bool]:
    """Returns (canonical_asset_id, created). Raises on any conflict or
    infeasibility -- never silently proceeds."""
    normalized_product = product_id.strip().upper()
    if normalized_product not in asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS and normalized_product != asset_roster.AUTONOMOUS_CYCLE_PRODUCT_ID:
        raise ValueError(
            f"{normalized_product} is not in the bounded Phase-1 roster table "
            f"(asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS); refusing to guess its Kraken symbol."
        )
    canonical_symbol = normalized_product.split("-")[0]
    exchange = "kraken_spot"

    existing = await db_session.scalar(
        select(Asset).where(Asset.symbol == canonical_symbol).where(Asset.exchange == exchange)
    )
    if existing is not None:
        logger.info("commission_kraken_asset_already_exists product_id=%s asset_id=%s", normalized_product, existing.id)
        return str(existing.id), False

    client = KrakenSpotClient()
    product = await client.fetch_product(credentials={}, environment=environment, product_id=normalized_product)
    if not product.available or not product.trading_enabled:
        raise PermissionError(
            f"Kraken does not currently report {normalized_product} as available+trading_enabled "
            f"(available={product.available} trading_enabled={product.trading_enabled}); refusing to commission."
        )

    asset = Asset(
        symbol=canonical_symbol,
        asset_class="crypto",
        exchange=exchange,
        base_currency="USD",
        supports_fractional=True,
        min_order_notional=product.min_order_notional,
        qty_step_size=product.quantity_increment,
        is_active=True,
    )
    db_session.add(asset)
    await db_session.flush()
    await db_session.commit()
    logger.info(
        "commission_kraken_asset_created product_id=%s asset_id=%s min_order_notional=%s qty_step_size=%s",
        normalized_product, asset.id, product.min_order_notional, product.quantity_increment,
    )
    return str(asset.id), True


async def _async_main(args: argparse.Namespace) -> int:
    setup_logging()
    async with AsyncSessionLocal() as db_session:
        try:
            asset_id, created = await commission_kraken_asset(
                db_session, product_id=args.product_id, environment=args.environment,
            )
        except Exception as exc:
            print(f"BLOCKED: {type(exc).__name__}: {exc}")
            return 1
    print(f"{'CREATED' if created else 'EXISTING'} canonical_asset_id={asset_id} product_id={args.product_id.upper()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-id", required=True, help="e.g. ETH-USD")
    parser.add_argument("--environment", default="production")
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
