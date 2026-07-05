from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.asset import Asset
from app.services.data.http_client import AsyncHTTPClient, ExternalAPIError


logger = logging.getLogger(__name__)

BINANCE_EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
CRYPTO_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
STOCK_SYMBOL = "AAPL"


@dataclass(slots=True)
class ExchangeSymbolMetadata:
    symbol: str
    quote_asset: str | None
    min_order_notional: Decimal | None
    qty_step_size: Decimal | None


@dataclass(slots=True)
class SeedSummary:
    inserted: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_unsupported: list[str] = field(default_factory=list)


def extract_filter_decimal(
    filters: list[dict[str, Any]],
    filter_type: str,
    value_key: str,
) -> Decimal | None:
    for item in filters:
        if item.get("filterType") != filter_type:
            continue

        raw_value = item.get(value_key)
        if raw_value in (None, ""):
            return None

        try:
            return Decimal(str(raw_value))
        except (InvalidOperation, TypeError):
            return None

    return None


def parse_exchange_symbols(payload: dict[str, Any]) -> dict[str, ExchangeSymbolMetadata]:
    symbols_raw = payload.get("symbols")
    if not isinstance(symbols_raw, list):
        raise ValueError("Binance exchangeInfo payload is missing a symbols list")

    metadata_by_symbol: dict[str, ExchangeSymbolMetadata] = {}

    for entry in symbols_raw:
        if not isinstance(entry, dict):
            continue

        symbol = entry.get("symbol")
        status = entry.get("status")
        if not isinstance(symbol, str) or status != "TRADING":
            continue

        filters = entry.get("filters")
        filter_list = filters if isinstance(filters, list) else []

        min_order_notional = extract_filter_decimal(filter_list, "MIN_NOTIONAL", "minNotional")
        qty_step_size = extract_filter_decimal(filter_list, "LOT_SIZE", "stepSize")

        metadata_by_symbol[symbol] = ExchangeSymbolMetadata(
            symbol=symbol,
            quote_asset=entry.get("quoteAsset") if isinstance(entry.get("quoteAsset"), str) else None,
            min_order_notional=min_order_notional,
            qty_step_size=qty_step_size,
        )

    return metadata_by_symbol


async def fetch_binance_exchange_symbols(http_client: AsyncHTTPClient) -> dict[str, ExchangeSymbolMetadata] | None:
    base_url = get_settings().binance_us_api_base.rstrip("/")
    endpoint = f"{base_url}{BINANCE_EXCHANGE_INFO_PATH}"

    try:
        response = await http_client.request("GET", endpoint)
    except ExternalAPIError as exc:
        logger.warning(
            "Failed to fetch Binance.US exchangeInfo; crypto asset seeding will be skipped: endpoint=%s status_code=%s body=%s",
            exc.endpoint,
            exc.status_code,
            exc.response_body,
        )
        return None

    payload = response.json()
    if not isinstance(payload, dict):
        logger.warning("Unexpected Binance.US exchangeInfo payload type: %s", type(payload).__name__)
        return None

    try:
        return parse_exchange_symbols(payload)
    except ValueError as exc:
        logger.warning("Unable to parse Binance.US exchangeInfo payload: %s", exc)
        return None


async def asset_exists(db_session: AsyncSession, symbol: str, exchange: str) -> bool:
    existing_id = await db_session.scalar(
        select(Asset.id).where(Asset.symbol == symbol).where(Asset.exchange == exchange)
    )
    return existing_id is not None


async def seed_assets(db_session: AsyncSession, exchange_symbols: dict[str, ExchangeSymbolMetadata] | None) -> SeedSummary:
    summary = SeedSummary()

    for symbol in CRYPTO_SYMBOLS:
        if await asset_exists(db_session, symbol=symbol, exchange="binance_us"):
            summary.skipped_existing.append(symbol)
            continue

        if exchange_symbols is None:
            logger.warning(
                "Skipping %s because Binance.US exchangeInfo is unavailable; cannot verify support.",
                symbol,
            )
            summary.skipped_unsupported.append(symbol)
            continue

        metadata = exchange_symbols.get(symbol)
        if metadata is None:
            logger.warning(
                "Skipping %s because Binance.US exchangeInfo does not list it as a supported TRADING symbol.",
                symbol,
            )
            summary.skipped_unsupported.append(symbol)
            continue

        if metadata.min_order_notional is None:
            logger.warning(
                "MIN_NOTIONAL filter missing/invalid for %s on Binance.US; inserting min_order_notional as null.",
                symbol,
            )
        if metadata.qty_step_size is None:
            logger.warning(
                "LOT_SIZE stepSize filter missing/invalid for %s on Binance.US; inserting qty_step_size as null.",
                symbol,
            )

        db_session.add(
            Asset(
                symbol=symbol,
                asset_class="crypto",
                exchange="binance_us",
                base_currency=metadata.quote_asset,
                supports_fractional=True,
                min_order_notional=metadata.min_order_notional,
                qty_step_size=metadata.qty_step_size,
                is_active=True,
            )
        )
        summary.inserted.append(symbol)

    if await asset_exists(db_session, symbol=STOCK_SYMBOL, exchange="alpaca"):
        summary.skipped_existing.append(STOCK_SYMBOL)
    else:
        db_session.add(
            Asset(
                symbol=STOCK_SYMBOL,
                asset_class="stock",
                exchange="alpaca",
                base_currency=None,
                supports_fractional=True,
                min_order_notional=None,
                qty_step_size=None,
                is_active=True,
            )
        )
        summary.inserted.append(STOCK_SYMBOL)

    await db_session.commit()
    return summary


def print_summary(summary: SeedSummary) -> None:
    print("Seed assets summary")
    print(f"Inserted ({len(summary.inserted)}): {', '.join(summary.inserted) if summary.inserted else 'none'}")
    print(
        "Skipped existing "
        f"({len(summary.skipped_existing)}): "
        f"{', '.join(summary.skipped_existing) if summary.skipped_existing else 'none'}"
    )
    print(
        "Skipped unsupported "
        f"({len(summary.skipped_unsupported)}): "
        f"{', '.join(summary.skipped_unsupported) if summary.skipped_unsupported else 'none'}"
    )


async def _async_main() -> int:
    setup_logging()

    async with AsyncHTTPClient() as http_client:
        exchange_symbols = await fetch_binance_exchange_symbols(http_client)

    async with AsyncSessionLocal() as db_session:
        summary = await seed_assets(db_session, exchange_symbols)

    print_summary(summary)
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
