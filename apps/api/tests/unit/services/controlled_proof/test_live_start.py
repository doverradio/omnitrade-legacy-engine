from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.services.controlled_proof import service


class _Rows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Db:
    def __init__(self, *, scalar_values=None, connections=None):
        self.scalar_values = list(scalar_values or [])
        self.connections = list(connections or [])

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, _statement):
        return _Rows(self.connections)


def _connection(available="10"):
    return SimpleNamespace(balances=[{"currency": "USD", "available": available}])


def _proof():
    return SimpleNamespace(
        proof_id=uuid.uuid4(), status="REQUESTED", product_id="BTC-USD",
        max_notional_usd=Decimal("5"), audit_correlation_id=uuid.uuid4(),
    )


def _enabled_settings():
    return SimpleNamespace(live_crypto_order_submission_enabled=True)


async def _false(**_kwargs):
    return False


def _ready(monkeypatch, *, created_proof=None):
    import app.services.orchestration.continuous_pipeline_worker as worker

    proof = created_proof or _proof()
    calls = []

    async def _create(**kwargs):
        calls.append(kwargs)
        return proof, None

    monkeypatch.setattr(service, "get_settings", _enabled_settings)
    monkeypatch.setattr(worker, "_has_open_live_order", _false)
    monkeypatch.setattr(service, "has_unresolved_reconciliation", _false)
    monkeypatch.setattr(service, "create_controlled_proof", _create)
    return proof, calls


@pytest.mark.asyncio
async def test_live_start_preflights_and_delegates_to_canonical_creation(monkeypatch) -> None:
    proof, calls = _ready(monkeypatch)
    result = await service.start_live_controlled_proof(
        db=_Db(scalar_values=[None, None], connections=[_connection()]),
        product_id="btc-usd", notional_usd=Decimal("5.00"), idempotency_key="new-live-proof",
        expires_in_minutes=60, actor="operator:human",
    )

    assert result.proof is proof
    assert result.created is True
    assert len(calls) == 1
    assert calls[0]["product_id"] == "BTC-USD"
    assert calls[0]["replace_active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("notional", [Decimal("0"), Decimal("4.99"), Decimal("5.01"), Decimal("500")])
async def test_live_start_rejects_invalid_or_noncanonical_notional(notional) -> None:
    with pytest.raises(InvalidRequestError, match="exact configured live notional"):
        await service.start_live_controlled_proof(
            db=_Db(), product_id="BTC-USD", notional_usd=notional,
            idempotency_key="bad-notional", expires_in_minutes=60, actor="operator:human",
        )


@pytest.mark.asyncio
async def test_live_start_rejects_disabled_live_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        service, "get_settings",
        lambda: SimpleNamespace(live_crypto_order_submission_enabled=False),
    )
    with pytest.raises(InvalidRequestError, match="disabled"):
        await service.start_live_controlled_proof(
            db=_Db(scalar_values=[None]), product_id="BTC-USD", notional_usd=Decimal("5"),
            idempotency_key="disabled", expires_in_minutes=60, actor="operator:human",
        )


@pytest.mark.asyncio
async def test_live_start_rejects_insufficient_balance(monkeypatch) -> None:
    monkeypatch.setattr(service, "get_settings", _enabled_settings)
    with pytest.raises(InvalidRequestError, match="Insufficient authoritative USD balance"):
        await service.start_live_controlled_proof(
            db=_Db(scalar_values=[None], connections=[_connection("4.99")]),
            product_id="BTC-USD", notional_usd=Decimal("5"), idempotency_key="unfunded",
            expires_in_minutes=60, actor="operator:human",
        )


@pytest.mark.asyncio
async def test_live_start_rejects_unresolved_reconciliation(monkeypatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker

    monkeypatch.setattr(service, "get_settings", _enabled_settings)
    monkeypatch.setattr(worker, "_has_open_live_order", _false)

    async def _true(**_kwargs):
        return True

    monkeypatch.setattr(service, "has_unresolved_reconciliation", _true)
    with pytest.raises(InvalidRequestError, match="Unresolved reconciliation"):
        await service.start_live_controlled_proof(
            db=_Db(scalar_values=[None], connections=[_connection()]),
            product_id="BTC-USD", notional_usd=Decimal("5"), idempotency_key="unresolved",
            expires_in_minutes=60, actor="operator:human",
        )


@pytest.mark.asyncio
async def test_live_start_idempotent_replay_returns_existing_without_new_preflight(monkeypatch) -> None:
    proof = _proof()
    monkeypatch.setattr(
        service, "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("replay must not rerun mutable preflight")),
    )
    result = await service.start_live_controlled_proof(
        db=_Db(scalar_values=[proof]), product_id="BTC-USD", notional_usd=Decimal("5"),
        idempotency_key="replay", expires_in_minutes=60, actor="operator:human",
    )
    assert result.proof is proof
    assert result.created is False


def test_live_start_service_has_no_provider_submission_dependency() -> None:
    import inspect

    source = inspect.getsource(service.start_live_controlled_proof)
    assert "submit_order" not in source
    assert "Kraken" not in source

