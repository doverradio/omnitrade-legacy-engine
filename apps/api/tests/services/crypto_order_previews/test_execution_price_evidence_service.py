from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest

from app.core.errors import InvalidRequestError
from app.services.execution_price_evidence import load_current_execution_price_evidence
from app.services.exchange_connections.providers.base import ExchangePriceEvidence


class _Provider:
    def __init__(self, evidence: ExchangePriceEvidence) -> None:
        self._evidence = evidence

    async def fetch_price_evidence(self, *, credentials, environment, product_id):
        _ = (credentials, environment, product_id)
        return self._evidence


def _evidence(*, provider: str = "kraken_spot", product_id: str = "BTC-USD", quote_currency: str = "USD", observed_at: datetime | None = None) -> ExchangePriceEvidence:
    now = datetime.now(timezone.utc)
    return ExchangePriceEvidence(
        evidence_id=uuid.uuid4(),
        provider=provider,
        venue=provider,
        product_id=product_id,
        symbol="BTC",
        quote_currency=quote_currency,
        base_currency="BTC",
        bid=Decimal("64995"),
        ask=Decimal("65005"),
        midpoint=Decimal("65000"),
        last_trade=Decimal("65001"),
        reference_price=Decimal("65005"),
        observed_at=observed_at or now - timedelta(seconds=30),
        retrieved_at=now,
        latency_ms=12,
        freshness_seconds=30,
        source_endpoint="/public/Ticker",
        retrieval_method="provider_public_rest",
        confidence=None,
        audit_metadata={"pair": "XBTUSD"},
    )


@pytest.mark.asyncio
async def test_load_execution_price_evidence_success() -> None:
    provider = _Provider(_evidence())
    evidence, reference_price, age_minutes = await load_current_execution_price_evidence(
        provider_client=provider,
        credentials={"api_key": "key", "api_secret": "secret"},
        environment="production",
        expected_provider="kraken_spot",
        product_id="BTC-USD",
        max_age_minutes=2,
    )

    assert evidence.provider == "kraken_spot"
    assert reference_price == Decimal("65005")
    assert age_minutes == 0


@pytest.mark.asyncio
async def test_load_execution_price_evidence_rejects_provider_mismatch() -> None:
    provider = _Provider(_evidence(provider="coinbase_advanced"))
    with pytest.raises(InvalidRequestError, match="provider mismatch"):
        await load_current_execution_price_evidence(
            provider_client=provider,
            credentials={"api_key": "key", "api_secret": "secret"},
            environment="production",
            expected_provider="kraken_spot",
            product_id="BTC-USD",
            max_age_minutes=2,
        )


@pytest.mark.asyncio
async def test_load_execution_price_evidence_rejects_quote_currency_mismatch() -> None:
    provider = _Provider(_evidence(quote_currency="USDT"))
    with pytest.raises(InvalidRequestError, match="quote currency mismatch"):
        await load_current_execution_price_evidence(
            provider_client=provider,
            credentials={"api_key": "key", "api_secret": "secret"},
            environment="production",
            expected_provider="kraken_spot",
            product_id="BTC-USD",
            max_age_minutes=2,
        )


@pytest.mark.asyncio
async def test_load_execution_price_evidence_rejects_stale_quote() -> None:
    stale_observed = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = _Provider(_evidence(observed_at=stale_observed))
    with pytest.raises(InvalidRequestError, match="stale"):
        await load_current_execution_price_evidence(
            provider_client=provider,
            credentials={"api_key": "key", "api_secret": "secret"},
            environment="production",
            expected_provider="kraken_spot",
            product_id="BTC-USD",
            max_age_minutes=1,
        )
