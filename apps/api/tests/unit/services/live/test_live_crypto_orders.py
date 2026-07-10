from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.schemas.live_crypto_orders import LiveCryptoOrderPrepareRequest
from app.services import live_crypto_orders as service


class _FakeDb:
    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return []

    def add(self, _item):
        return None

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_get_readiness_returns_closed_when_profile_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    response = await service.service.get_readiness(db=fake_db, live_trading_profile_id=uuid.uuid4())

    assert response.live_mode_enabled is False
    assert response.live_profile_ready is False
    assert response.feature_flag_enabled is False
    assert response.reason == "live_profile_not_found"


@pytest.mark.asyncio
async def test_prepare_confirmation_rejects_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    with pytest.raises(PermissionError, match="disabled"):
        await service.service.prepare_confirmation(
            db=fake_db,
            request=LiveCryptoOrderPrepareRequest(
                live_trading_profile_id=uuid.uuid4(),
                crypto_order_preview_id=uuid.uuid4(),
                operator_identity="operator:human",
                idempotency_token="token-1",
            ),
        )
