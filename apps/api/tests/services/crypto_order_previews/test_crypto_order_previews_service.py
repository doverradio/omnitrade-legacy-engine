from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.models.risk_event import RiskEvent
from app.services.crypto_order_previews import service
from app.services.exchange_connections.providers.base import ExchangePreviewResult, ExchangePriceEvidence


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    class _BeginContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def add(self, item: object) -> None:
        if isinstance(item, CryptoOrderPreview) and getattr(item, "crypto_order_preview_id", None) is None:
            item.crypto_order_preview_id = uuid.uuid4()
        if isinstance(item, DecisionRecord) and getattr(item, "decision_id", None) is None:
            item.decision_id = uuid.uuid4()
        if isinstance(item, RiskEvent) and getattr(item, "id", None) is None:
            item.id = uuid.uuid4()
        self.added.append(item)

    def in_transaction(self) -> bool:
        return False

    def begin(self) -> _BeginContext:
        return self._BeginContext()

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _item: object) -> None:
        return None

    async def scalar(self, _statement):
        return None


@pytest.mark.asyncio
async def test_create_buy_preview_success(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    db = _FakeDb()
    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        connection_name="Primary Coinbase",
        environment="production",
        status="connected",
        credentials_encrypted="encrypted",
        api_key_masked="******1234",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=True,
        api_permissions=["view"],
        account_status="active",
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=now,
        last_heartbeat_at=now,
        last_api_error=None,
        last_verified_at=now,
        last_readiness_verdict="READY_FOR_PREVIEW",
        last_readiness_report=[],
        created_at=now,
        updated_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTC",
        asset_class="crypto",
        exchange="coinbase_advanced",
        base_currency="USD",
        supports_fractional=True,
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.00000001"),
        is_active=True,
        created_at=now,
    )

    class _Provider:
        async def fetch_balances(self, *, credentials, environment):
            _ = (credentials, environment)
            return SimpleNamespace(
                balances=[
                    SimpleNamespace(currency="USD", available=Decimal("100.00"), reserved=Decimal("0"), total=Decimal("100.00")),
                    SimpleNamespace(currency="BTC", available=Decimal("0.50"), reserved=Decimal("0"), total=Decimal("0.50")),
                ],
                total_equity_usd=Decimal("100.00"),
            )

        async def preview_market_order(self, *, credentials, environment, product_id, side, quote_size, base_size, client_order_id=None):
            _ = (credentials, environment, product_id, side, base_size, client_order_id)
            return ExchangePreviewResult(
                preview_id="preview-123",
                success=True,
                failure_reason=None,
                warning_messages=["Estimated fee subject to change"],
                estimated_average_price=Decimal("10000.00"),
                estimated_total_value=Decimal("5.10"),
                estimated_base_size=Decimal("0.000499"),
                estimated_quote_size=quote_size,
                estimated_fee=Decimal("0.10"),
                estimated_fee_currency="USD",
                estimated_slippage=Decimal("0.01"),
                estimated_commission_total=Decimal("0.10"),
                best_bid=Decimal("9995.00"),
                best_ask=Decimal("10005.00"),
                exchange_response_summary={"preview_id": "preview-123"},
            )

    async def _load_exchange_connection(*_args, **_kwargs):
        return connection

    evidence = ExchangePriceEvidence(
        evidence_id=uuid.uuid4(),
        provider="coinbase_advanced",
        venue="coinbase_advanced",
        product_id="BTC-USD",
        symbol="BTC",
        quote_currency="USD",
        base_currency="BTC",
        bid=Decimal("9995.00"),
        ask=Decimal("10005.00"),
        midpoint=Decimal("10000.00"),
        last_trade=Decimal("10000.00"),
        reference_price=Decimal("10005.00"),
        observed_at=now - timedelta(minutes=1),
        retrieved_at=now,
        latency_ms=10,
        freshness_seconds=60,
        source_endpoint="/api/v3/brokerage/products/BTC-USD",
        retrieval_method="provider_authenticated_rest",
        confidence=None,
        audit_metadata={"source": "coinbase_brokerage_product"},
    )

    async def _load_asset_and_price(*_args, **_kwargs):
        return asset

    async def _load_execution_price_evidence(**_kwargs):
        return evidence, Decimal("10005.00"), 1

    async def _global_kill_switch(*_args, **_kwargs):
        return False

    async def _risk_rules(**_kwargs):
        return SimpleNamespace(rules={"max_daily_loss_pct": Decimal("0.05"), "max_drawdown_pct": Decimal("0.10")})

    captured: dict[str, object] = {}

    def _evaluate_signal_risk(*_args, **_kwargs):
        captured["reference_price"] = _kwargs.get("reference_price")
        return SimpleNamespace(
            action=service.RiskDecisionAction.APPROVE,
            reason_code=None,
            approved_quantity=Decimal("0.000499"),
            steps=[SimpleNamespace(step="global_kill_switch", status="approve", reason_code=None)],
        )

    monkeypatch.setattr(service, "_load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr(service, "_load_asset_and_price", _load_asset_and_price)
    monkeypatch.setattr(service, "_load_execution_price_evidence", _load_execution_price_evidence)
    monkeypatch.setattr(service, "_get_global_kill_switch", _global_kill_switch)
    monkeypatch.setattr(service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(service, "evaluate_signal_risk", _evaluate_signal_risk)
    monkeypatch.setattr(service, "get_decrypted_credentials_for_connection", lambda _connection: {"api_key": "key", "api_secret": "secret", "passphrase": "pass"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda _provider: _Provider())

    response = await service.create_crypto_order_preview(
        db=db,
        request=service.CryptoOrderPreviewCreateRequest(
            exchange_connection_id=connection.exchange_connection_id,
            environment="production",
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            quote_size=Decimal("5.00"),
            requested_amount_currency="USD",
            generated_by="operator",
        ),
    )

    assert response.status == "PREVIEW_READY"
    assert response.risk_verdict == "approved_for_preview"
    assert response.preview_id == "preview-123"
    assert response.order_submitted is False
    assert response.execution_available is False
    assert response.warning_messages == ["Estimated fee subject to change"]
    assert response.estimated_balance_after == Decimal("94.90")
    stored = next(item for item in db.added if isinstance(item, CryptoOrderPreview))
    decision = next(item for item in db.added if isinstance(item, DecisionRecord))
    snapshot = next(item for item in db.added if isinstance(item, DecisionSnapshot))
    risk_event = next(item for item in db.added if isinstance(item, RiskEvent))
    assert captured["reference_price"] == Decimal("10005.00")
    assert stored.exchange_response_summary["price_evidence"]["evidence_id"] == str(evidence.evidence_id)
    assert stored.exchange_response_summary["price_evidence"]["quote_currency"] == "USD"
    assert stored.failure_reason is None
    assert stored.exchange_response_summary["preview_id"] == "preview-123"
    assert stored.decision_record_id is not None
    assert stored.decision_record_id == decision.decision_id
    assert stored.risk_event_id is not None
    assert stored.risk_event_id == risk_event.id
    assert snapshot.decision_id == decision.decision_id
    assert decision.execution_details["preview_id"] == str(stored.crypto_order_preview_id)


@pytest.mark.asyncio
async def test_preview_rejects_when_connection_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    db = _FakeDb()
    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        connection_name="Primary Coinbase",
        environment="production",
        status="connected",
        credentials_encrypted="encrypted",
        api_key_masked="******1234",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=True,
        api_permissions=["view"],
        account_status="active",
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=now,
        last_heartbeat_at=now,
        last_api_error=None,
        last_verified_at=now,
        last_readiness_verdict="PERMISSION_BLOCKED",
        last_readiness_report=[],
        created_at=now,
        updated_at=now,
    )

    async def _load_exchange_connection(*_args, **_kwargs):
        return connection

    async def _load_asset_and_price(*_args, **_kwargs):
        raise AssertionError("should not reach market data")

    monkeypatch.setattr(service, "_load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr(service, "_load_asset_and_price", _load_asset_and_price)

    with pytest.raises(InvalidRequestError) as exc_info:
        await service.create_crypto_order_preview(
            db=db,
            request=service.CryptoOrderPreviewCreateRequest(
                exchange_connection_id=connection.exchange_connection_id,
                environment="production",
                product_id="BTC-USD",
                side="BUY",
                order_type="MARKET",
                quote_size=Decimal("5.00"),
                requested_amount_currency="USD",
            ),
        )

    assert "not ready for preview" in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_preview_uses_only_preview_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.exchange_connections.providers.coinbase_advanced import CoinbaseAdvancedClient

    captured = {}

    class _Response:
        status_code = 200
        headers = {"Date": "Thu, 09 Jul 2026 10:00:00 GMT"}

        def json(self):
            return {"preview_id": "preview-xyz", "success": True, "estimated_average_price": "10000.00", "estimated_quote_size": "5.00"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, path, content=None, headers=None, params=None):
            captured["method"] = method
            captured["path"] = path
            captured["content"] = content
            captured["headers"] = headers
            captured["params"] = params
            return _Response()

    monkeypatch.setattr("app.services.exchange_connections.providers.coinbase_advanced.httpx.AsyncClient", lambda **kwargs: _Client())
    monkeypatch.setattr("app.services.exchange_connections.providers.coinbase_advanced.build_coinbase_jwt", lambda **kwargs: "jwt-token")

    client = CoinbaseAdvancedClient()
    result = await client.preview_market_order(
        credentials={"api_key": "key", "api_secret": "secret", "passphrase": "pass"},
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        quote_size=Decimal("5.00"),
        base_size=None,
        client_order_id="client-1",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v3/brokerage/orders/preview"
    assert "orders/preview" in captured["path"]
    assert result.preview_id == "preview-xyz"
    assert result.success is True


@pytest.mark.asyncio
async def test_preview_redacts_sensitive_exchange_response_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    db = _FakeDb()
    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        connection_name="Primary Coinbase",
        environment="production",
        status="connected",
        credentials_encrypted="encrypted",
        api_key_masked="******1234",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=True,
        api_permissions=["view"],
        account_status="active",
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=now,
        last_heartbeat_at=now,
        last_api_error=None,
        last_verified_at=now,
        last_readiness_verdict="READY_FOR_PREVIEW",
        last_readiness_report=[],
        created_at=now,
        updated_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTC",
        asset_class="crypto",
        exchange="coinbase_advanced",
        base_currency="USD",
        supports_fractional=True,
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.00000001"),
        is_active=True,
        created_at=now,
    )

    class _Provider:
        async def fetch_balances(self, *, credentials, environment):
            _ = (credentials, environment)
            return SimpleNamespace(
                balances=[
                    SimpleNamespace(currency="USD", available=Decimal("100.00"), reserved=Decimal("0"), total=Decimal("100.00")),
                    SimpleNamespace(currency="BTC", available=Decimal("0.50"), reserved=Decimal("0"), total=Decimal("0.50")),
                ],
                total_equity_usd=Decimal("100.00"),
            )

        async def preview_market_order(self, *, credentials, environment, product_id, side, quote_size, base_size, client_order_id=None):
            _ = (credentials, environment, product_id, side, quote_size, base_size, client_order_id)
            return ExchangePreviewResult(
                preview_id="preview-secret",
                success=True,
                failure_reason=None,
                warning_messages=[],
                estimated_average_price=Decimal("10000.00"),
                estimated_total_value=Decimal("5.10"),
                estimated_base_size=Decimal("0.000499"),
                estimated_quote_size=Decimal("5.00"),
                estimated_fee=Decimal("0.10"),
                estimated_fee_currency="USD",
                estimated_slippage=Decimal("0.01"),
                estimated_commission_total=Decimal("0.10"),
                best_bid=Decimal("9995.00"),
                best_ask=Decimal("10005.00"),
                exchange_response_summary={
                    "preview_id": "preview-secret",
                    "api_key": "SENTINEL_API_KEY",
                    "token": "SENTINEL_TOKEN",
                    "nested": {"authorization": "Bearer SENTINEL_AUTH", "safe": "ok"},
                },
            )

    async def _load_exchange_connection(*_args, **_kwargs):
        return connection

    evidence = ExchangePriceEvidence(
        evidence_id=uuid.uuid4(),
        provider="coinbase_advanced",
        venue="coinbase_advanced",
        product_id="BTC-USD",
        symbol="BTC",
        quote_currency="USD",
        base_currency="BTC",
        bid=Decimal("9995.00"),
        ask=Decimal("10005.00"),
        midpoint=Decimal("10000.00"),
        last_trade=Decimal("10000.00"),
        reference_price=Decimal("10005.00"),
        observed_at=now - timedelta(minutes=1),
        retrieved_at=now,
        latency_ms=8,
        freshness_seconds=60,
        source_endpoint="/api/v3/brokerage/products/BTC-USD",
        retrieval_method="provider_authenticated_rest",
        confidence=None,
        audit_metadata={"source": "coinbase_brokerage_product"},
    )

    async def _load_asset_and_price(*_args, **_kwargs):
        return asset

    async def _load_execution_price_evidence(**_kwargs):
        return evidence, Decimal("10005.00"), 1

    async def _global_kill_switch(*_args, **_kwargs):
        return False

    async def _risk_rules(**_kwargs):
        return SimpleNamespace(rules={"max_daily_loss_pct": Decimal("0.05"), "max_drawdown_pct": Decimal("0.10")})

    def _evaluate_signal_risk(*_args, **_kwargs):
        return SimpleNamespace(
            action=service.RiskDecisionAction.APPROVE,
            reason_code=None,
            approved_quantity=Decimal("0.000499"),
            steps=[SimpleNamespace(step="global_kill_switch", status="approve", reason_code=None)],
        )

    monkeypatch.setattr(service, "_load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr(service, "_load_asset_and_price", _load_asset_and_price)
    monkeypatch.setattr(service, "_load_execution_price_evidence", _load_execution_price_evidence)
    monkeypatch.setattr(service, "_get_global_kill_switch", _global_kill_switch)
    monkeypatch.setattr(service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(service, "evaluate_signal_risk", _evaluate_signal_risk)
    monkeypatch.setattr(service, "get_decrypted_credentials_for_connection", lambda _connection: {"api_key": "key", "api_secret": "secret", "passphrase": "pass"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda _provider: _Provider())

    response = await service.create_crypto_order_preview(
        db=db,
        request=service.CryptoOrderPreviewCreateRequest(
            exchange_connection_id=connection.exchange_connection_id,
            environment="production",
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            quote_size=Decimal("5.00"),
            requested_amount_currency="USD",
            generated_by="operator",
        ),
    )

    stored = next(item for item in db.added if isinstance(item, CryptoOrderPreview))
    assert stored.exchange_response_summary["api_key"] == "[REDACTED]"
    assert stored.exchange_response_summary["token"] == "[REDACTED]"
    assert stored.exchange_response_summary["nested"]["authorization"] == "[REDACTED]"
    assert stored.exchange_response_summary["nested"]["safe"] == "ok"
    assert stored.exchange_response_summary["price_evidence"]["evidence_id"] == str(evidence.evidence_id)
    assert response.exchange_response_summary["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_create_preview_rejected_persists_decision_and_risk_event(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    db = _FakeDb()
    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        connection_name="Primary Coinbase",
        environment="production",
        status="connected",
        credentials_encrypted="encrypted",
        api_key_masked="******1234",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=True,
        api_permissions=["view"],
        account_status="active",
        balances=[],
        total_equity_usd=None,
        last_successful_sync_at=now,
        last_heartbeat_at=now,
        last_api_error=None,
        last_verified_at=now,
        last_readiness_verdict="READY_FOR_PREVIEW",
        last_readiness_report=[],
        created_at=now,
        updated_at=now,
    )
    asset = Asset(
        id=uuid.uuid4(),
        symbol="BTC",
        asset_class="crypto",
        exchange="coinbase_advanced",
        base_currency="USD",
        supports_fractional=True,
        min_order_notional=Decimal("1"),
        qty_step_size=Decimal("0.00000001"),
        is_active=True,
        created_at=now,
    )

    class _Provider:
        async def fetch_balances(self, *, credentials, environment):
            _ = (credentials, environment)
            return SimpleNamespace(
                balances=[
                    SimpleNamespace(currency="USD", available=Decimal("100.00"), reserved=Decimal("0"), total=Decimal("100.00")),
                    SimpleNamespace(currency="BTC", available=Decimal("0.50"), reserved=Decimal("0"), total=Decimal("0.50")),
                ],
                total_equity_usd=Decimal("100.00"),
            )

        async def preview_market_order(self, *, credentials, environment, product_id, side, quote_size, base_size, client_order_id=None):
            _ = (credentials, environment, product_id, side, quote_size, base_size, client_order_id)
            raise AssertionError("provider preview should not be called when risk rejects")

    async def _load_exchange_connection(*_args, **_kwargs):
        return connection

    evidence = ExchangePriceEvidence(
        evidence_id=uuid.uuid4(),
        provider="coinbase_advanced",
        venue="coinbase_advanced",
        product_id="BTC-USD",
        symbol="BTC",
        quote_currency="USD",
        base_currency="BTC",
        bid=Decimal("9995.00"),
        ask=Decimal("10005.00"),
        midpoint=Decimal("10000.00"),
        last_trade=Decimal("10000.00"),
        reference_price=Decimal("10005.00"),
        observed_at=now - timedelta(minutes=1),
        retrieved_at=now,
        latency_ms=8,
        freshness_seconds=60,
        source_endpoint="/api/v3/brokerage/products/BTC-USD",
        retrieval_method="provider_authenticated_rest",
        confidence=None,
        audit_metadata={"source": "coinbase_brokerage_product"},
    )

    async def _load_asset_and_price(*_args, **_kwargs):
        return asset

    async def _load_execution_price_evidence(**_kwargs):
        return evidence, Decimal("10005.00"), 1

    async def _global_kill_switch(*_args, **_kwargs):
        return False

    async def _risk_rules(**_kwargs):
        return SimpleNamespace(rules={"max_daily_loss_pct": Decimal("0.05"), "max_drawdown_pct": Decimal("0.10")})

    def _evaluate_signal_risk(*_args, **_kwargs):
        return SimpleNamespace(
            action=service.RiskDecisionAction.REJECT,
            reason_code="position_below_minimum_order_size",
            approved_quantity=Decimal("0"),
            steps=[
                SimpleNamespace(step="global_kill_switch", status="approve", reason_code=None),
                SimpleNamespace(step="minimum_order_size", status="reject", reason_code="position_below_minimum_order_size"),
            ],
        )

    monkeypatch.setattr(service, "_load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr(service, "_load_asset_and_price", _load_asset_and_price)
    monkeypatch.setattr(service, "_load_execution_price_evidence", _load_execution_price_evidence)
    monkeypatch.setattr(service, "_get_global_kill_switch", _global_kill_switch)
    monkeypatch.setattr(service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(service, "evaluate_signal_risk", _evaluate_signal_risk)
    monkeypatch.setattr(service, "get_decrypted_credentials_for_connection", lambda _connection: {"api_key": "key", "api_secret": "secret", "passphrase": "pass"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda _provider: _Provider())

    response = await service.create_crypto_order_preview(
        db=db,
        request=service.CryptoOrderPreviewCreateRequest(
            exchange_connection_id=connection.exchange_connection_id,
            environment="production",
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            quote_size=Decimal("5.00"),
            requested_amount_currency="USD",
            generated_by="operator",
        ),
    )

    stored = next(item for item in db.added if isinstance(item, CryptoOrderPreview))
    decision = next(item for item in db.added if isinstance(item, DecisionRecord))
    snapshot = next(item for item in db.added if isinstance(item, DecisionSnapshot))
    risk_event = next(item for item in db.added if isinstance(item, RiskEvent))

    assert response.status == "RISK_REJECTED"
    assert stored.decision_record_id is not None
    assert stored.risk_event_id is not None
    assert stored.decision_record_id == decision.decision_id
    assert stored.risk_event_id == risk_event.id
    assert snapshot.decision_id == decision.decision_id
    assert decision.trade_accepted is False
    assert decision.trade_rejected_reason == "position_below_minimum_order_size"
