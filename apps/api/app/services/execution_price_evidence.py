from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import ExchangePriceEvidence


def _split_product(product_id: str) -> tuple[str, str]:
    normalized = product_id.strip().upper()
    if "-" not in normalized:
        raise InvalidRequestError(
            message="product_id must be a normalized spot pair like BTC-USD",
            details={"product_id": product_id},
        )
    base_symbol, quote_symbol = normalized.split("-", 1)
    return base_symbol, quote_symbol


async def load_current_execution_price_evidence(
    *,
    provider_client,
    credentials: dict[str, str],
    environment: str,
    expected_provider: str,
    product_id: str,
    max_age_minutes: int,
) -> tuple[ExchangePriceEvidence, Decimal, int]:
    evidence = await provider_client.fetch_price_evidence(
        credentials=credentials,
        environment=environment,
        product_id=product_id,
    )

    normalized_product = product_id.strip().upper()
    base_symbol, quote_symbol = _split_product(normalized_product)

    if evidence.provider != expected_provider:
        raise InvalidRequestError(
            message="Price evidence provider mismatch",
            details={
                "expected_provider": expected_provider,
                "evidence_provider": evidence.provider,
            },
        )

    if evidence.product_id.strip().upper() != normalized_product:
        raise InvalidRequestError(
            message="Price evidence product mismatch",
            details={
                "expected_product": normalized_product,
                "evidence_product": evidence.product_id,
            },
        )

    if evidence.quote_currency.strip().upper() != quote_symbol:
        raise InvalidRequestError(
            message="Price evidence quote currency mismatch",
            details={
                "expected_quote_currency": quote_symbol,
                "evidence_quote_currency": evidence.quote_currency,
            },
        )

    if evidence.base_currency.strip().upper() != base_symbol:
        raise InvalidRequestError(
            message="Price evidence base currency mismatch",
            details={
                "expected_base_currency": base_symbol,
                "evidence_base_currency": evidence.base_currency,
            },
        )

    if evidence.reference_price is None or evidence.reference_price <= Decimal("0"):
        raise InvalidRequestError(
            message="Price evidence quote unavailable",
            details={"product_id": normalized_product},
        )

    if evidence.confidence is not None and (evidence.confidence < Decimal("0") or evidence.confidence > Decimal("1")):
        raise InvalidRequestError(
            message="Price evidence confidence invalid",
            details={"confidence": format(evidence.confidence, "f")},
        )

    observed_at = evidence.observed_at
    if observed_at is None:
        raise InvalidRequestError(
            message="Price evidence timestamp invalid",
            details={"reason": "observed_at_missing", "product_id": normalized_product},
        )

    observed_utc = observed_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    age_seconds = int((now - observed_utc).total_seconds())
    if age_seconds < 0:
        raise InvalidRequestError(
            message="Price evidence timestamp invalid",
            details={"reason": "observed_at_in_future", "product_id": normalized_product},
        )

    max_age_seconds = max_age_minutes * 60
    if age_seconds > max_age_seconds:
        raise InvalidRequestError(
            message="Price evidence is stale",
            details={
                "product_id": normalized_product,
                "market_age_minutes": int(age_seconds / 60),
                "max_age_minutes": max_age_minutes,
            },
        )

    return evidence, evidence.reference_price, int(age_seconds / 60)
