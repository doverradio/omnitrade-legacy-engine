from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Shared, product-agnostic home for the bounded Phase-1 live multi-asset
# roster logic, used by BOTH continuous_pipeline_worker.py (which evaluates
# the roster every cycle) and canonical_campaign_binding.py (which must
# validate that a campaign's allowed_instruments never exceeds what the
# worker is actually configured to evaluate). Kept in its own module
# specifically to avoid a circular import between those two: worker imports
# app.services.autonomous_cycle, which imports canonical_campaign_binding.

AUTONOMOUS_CYCLE_PRODUCT_ID = "BTC-USD"
AUTONOMOUS_CYCLE_PROVIDER = "kraken_spot"
AUTONOMOUS_CYCLE_INTERVAL = "15m"
AUTONOMOUS_CYCLE_ASSET_SYMBOLS = ("BTC", "XBT", "XXBT")

# Bounded Phase-1 live multi-asset roster. Deliberately a small, explicit,
# hand-maintained table -- not a general Kraken symbol-resolution system --
# scoped to exactly the products the worker is prepared to evaluate. Entries
# not present here are logged and skipped (fail closed), never guessed at.
ADDITIONAL_PRODUCT_ASSET_SYMBOLS: dict[str, tuple[str, ...]] = {
    "ETH-USD": ("ETH", "XETH"),
    "SOL-USD": ("SOL",),
}


def resolve_autonomous_cycle_products(*, settings) -> list[str]:
    """BTC-USD first, always -- then any configured additional products that
    are in the known roster table, deduplicated, order-preserving. Returns
    exactly ["BTC-USD"] when no additional products are configured."""
    products = [AUTONOMOUS_CYCLE_PRODUCT_ID]
    for candidate in settings.parsed_autonomous_cycle_additional_products:
        if candidate == AUTONOMOUS_CYCLE_PRODUCT_ID or candidate in products:
            continue
        if candidate not in ADDITIONAL_PRODUCT_ASSET_SYMBOLS:
            logger.warning(
                "autonomous_cycle_unknown_additional_product product_id=%s known_products=%s",
                candidate, sorted(ADDITIONAL_PRODUCT_ASSET_SYMBOLS),
            )
            continue
        products.append(candidate)
    return products


def asset_symbols_for_product(*, product_id: str) -> tuple[str, ...]:
    if product_id == AUTONOMOUS_CYCLE_PRODUCT_ID:
        return AUTONOMOUS_CYCLE_ASSET_SYMBOLS
    return ADDITIONAL_PRODUCT_ASSET_SYMBOLS.get(product_id, ())
