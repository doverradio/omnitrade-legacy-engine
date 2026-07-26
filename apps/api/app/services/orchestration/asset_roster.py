from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.asset import Asset
from app.services.capital_campaign_domain import get_governing_campaign_definition
from app.services.mandates.lifecycle import get_governing_authorized_mandate_version

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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


async def resolve_autonomous_cycle_products_from_campaign(*, db: "AsyncSession", settings) -> list[str]:
    """Milestone-1 dynamic discovery. A product is evaluated only when it
    passes all four of:

      1. an active canonical Asset Registry row exists for it;
      2. it is in the CURRENTLY GOVERNING campaign's allowed_instruments
         (capital_campaign_definitions, pinned via
         AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID) -- "governing"
         means whatever version the runtime campaign's definition_version is
         actually pinned to, provided that pin's operational status is READY
         (reused via capital_campaign_domain.get_governing_campaign_definition,
         the same governing-membership lookup the rest of the platform uses
         -- not a second definition). Because create_campaign_draft no longer
         eagerly repins a currently-governing runtime onto an unvalidated
         DRAFT successor, the previously governing version keeps resolving
         as governing for the entire time a successor is being constructed
         and validated -- the pin, and therefore what this resolves to, only
         moves when the governed transition actually promotes the successor;
      3. it is in the governing AUTHORIZED mandate version's allowed_products
         (pinned via AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID, resolved
         through mandates.lifecycle.get_governing_authorized_mandate_version --
         the exact function production activation/promotion already uses, not
         a second definition of "governing");
      4. this module's own known Kraken symbol-alias table recognizes it.

    Applied uniformly: BTC-USD is included only if it independently passes the
    same four checks, no special-casing whatsoever. Fails closed to an EMPTY
    roster -- never to a default/legacy BTC-only roster -- when campaign_id or
    mandate_id is unconfigured, the campaign has no currently-governing READY
    version, or no valid governing mandate version exists: a governance
    lookup failure must never be treated as "fall back to a known-authorized
    product," since that is itself an authorization decision this function is
    not entitled to make on a lookup failure."""
    campaign_id = getattr(settings, "automatic_mandate_package_activation_campaign_id", None)
    mandate_id = getattr(settings, "automatic_mandate_package_activation_mandate_id", None)
    if campaign_id is None or mandate_id is None:
        return []

    definition = await get_governing_campaign_definition(db=db, campaign_id=campaign_id)
    if definition is None:
        logger.warning("asset_roster_campaign_db_discovery_no_governing_campaign campaign_id=%s", campaign_id)
        return []

    governing_version = await get_governing_authorized_mandate_version(db=db, mandate_id=mandate_id)
    if governing_version is None:
        logger.warning("asset_roster_campaign_db_discovery_no_governing_mandate mandate_id=%s", mandate_id)
        return []

    allowed_instruments = set(definition.allowed_instruments or [])
    allowed_products = set(governing_version.allowed_products or [])
    known_products = {AUTONOMOUS_CYCLE_PRODUCT_ID, *ADDITIONAL_PRODUCT_ASSET_SYMBOLS.keys()}
    candidates = [AUTONOMOUS_CYCLE_PRODUCT_ID] + sorted(known_products - {AUTONOMOUS_CYCLE_PRODUCT_ID})

    products: list[str] = []
    for candidate in candidates:
        if candidate not in allowed_instruments or candidate not in allowed_products:
            continue
        symbol = candidate.split("-")[0]
        asset = await db.scalar(
            select(Asset.id).where(Asset.symbol == symbol, Asset.exchange == AUTONOMOUS_CYCLE_PROVIDER, Asset.is_active.is_(True))
        )
        if asset is None:
            continue
        products.append(candidate)
    return products
