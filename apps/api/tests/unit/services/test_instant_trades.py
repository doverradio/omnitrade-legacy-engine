from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError, ServiceUnavailableError
from app.models.live_crypto_order import LiveCryptoOrder
from app.services import instant_trades as instant_service
from app.services.risk.risk_engine import RiskDecisionAction


class _FakeDb:
    def __init__(self) -> None:
        self.orders_by_client_id: dict[str, LiveCryptoOrder] = {}
        self.orders_by_id: dict[uuid.UUID, LiveCryptoOrder] = {}

    async def connection(self):
        return self

    async def execute(self, _statement):
        return None

    async def scalar(self, statement):
        text = str(statement).lower()
        if "live_crypto_orders" in text and "client_order_id" in text:
            for order in self.orders_by_client_id.values():
                return order
            return None
        if "live_crypto_orders" in text:
            for order in self.orders_by_id.values():
                return order
            return None
        return None

    def add(self, item):
        if isinstance(item, LiveCryptoOrder):
            self._pending_order = item

    async def flush(self):
        if hasattr(self, "_pending_order"):
            order = self._pending_order
            if order.live_crypto_order_id is None:
                order.live_crypto_order_id = uuid.uuid4()
            now = datetime.now(timezone.utc)
            if order.created_at is None:
                order.created_at = now
            order.updated_at = now
            self.orders_by_client_id[order.client_order_id] = order
            self.orders_by_id[order.live_crypto_order_id] = order
            del self._pending_order

    async def refresh(self, _order):
        return None

    async def commit(self):
        return None


@pytest.fixture
def base_request() -> instant_service.InstantTradeBuyRequest:
    return instant_service.InstantTradeBuyRequest(
        paper_account_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        quote_amount=Decimal("5.00"),
        actor="11111111-1111-1111-1111-111111111111",
        confirmation=True,
        idempotency_key="idem-1",
    )


def _install_common_mocks(monkeypatch: pytest.MonkeyPatch, request: instant_service.InstantTradeBuyRequest, *, balance: Decimal = Decimal("10"), global_engaged: bool = False, account_engaged: bool = False) -> None:
    account = SimpleNamespace(
        id=request.paper_account_id,
        owner_user_id=uuid.UUID(request.actor),
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
    )
    profile = SimpleNamespace(id=request.live_trading_profile_id, paper_account_id=request.paper_account_id)
    connection = SimpleNamespace(
        exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot",
        environment="production",
        balances=[{"currency": "USD", "available": format(balance, "f")}],
    )

    async def _owned_account(**_kwargs):
        return account

    async def _profile(**_kwargs):
        return profile

    async def _connection(**_kwargs):
        return connection

    async def _asset(**_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    async def _kill_switch(**kwargs):
        if kwargs["scope"] == "global":
            return SimpleNamespace(engaged=global_engaged, rearm_required=False)
        return SimpleNamespace(engaged=account_engaged, rearm_required=False)

    async def _persist(**_kwargs):
        return SimpleNamespace(risk_event_id=uuid.uuid4())

    async def _risk_rules(**_kwargs):
        return SimpleNamespace(
            rules={
                "max_position_size_pct": Decimal("1"),
                "max_daily_loss_pct": Decimal("1"),
                "max_drawdown_pct": Decimal("1"),
            }
        )

    monkeypatch.setattr(instant_service, "_load_owned_account", _owned_account)
    monkeypatch.setattr(instant_service, "_load_profile", _profile)
    monkeypatch.setattr(instant_service, "_resolve_connection", _connection)
    monkeypatch.setattr(instant_service, "_load_asset_for_product", _asset)
    monkeypatch.setattr(instant_service, "_load_kill_switch_state", _kill_switch)
    monkeypatch.setattr(instant_service, "_load_decrypted_credentials", lambda _connection: {"api_key": "x", "api_secret": "y"})
    monkeypatch.setattr(instant_service, "_record_audit", lambda **_kwargs: _persist())
    monkeypatch.setattr(instant_service, "_commit_if_supported", lambda **_kwargs: _persist())
    monkeypatch.setattr(instant_service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(
        instant_service,
        "evaluate_signal_risk",
        lambda **_kwargs: SimpleNamespace(action=RiskDecisionAction.APPROVE, approved_quantity=Decimal("0.00005"), reason_code=None),
    )
    monkeypatch.setattr(instant_service, "persist_risk_decision", _persist)
    monkeypatch.setattr(instant_service, "require_provider_capabilities", lambda **_kwargs: None)
    monkeypatch.setattr(
        instant_service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_max_order_usd=Decimal("5"),
            instant_trade_db_timeout_seconds=2,
            instant_trade_provider_timeout_seconds=2,
            instant_trade_reconciliation_poll_timeout_seconds=1,
        ),
    )


@pytest.mark.asyncio
async def test_exact_five_usd_buy_succeeds_with_one_provider_submission(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    calls = {"submit": 0}

    async def _must_not_be_called(**_kwargs):
        raise AssertionError("autonomous campaign helper should not be consulted for instant buy")

    monkeypatch.setattr("app.services.live_crypto_orders._load_active_campaign_for_account", _must_not_be_called)

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(
            success=True,
            failure_reason=None,
            warning_messages=[],
            estimated_base_size=Decimal("0.00005"),
            estimated_fee=Decimal("0.01"),
            estimated_fee_currency="USD",
            estimated_average_price=Decimal("100000"),
            best_ask=Decimal("100000"),
        )

    async def _submit_order(**_kwargs):
        calls["submit"] += 1
        return SimpleNamespace(
            classification="success",
            order=SimpleNamespace(provider_order_id="O-1", status="OPEN"),
            rejection=None,
            ambiguous=None,
            raw_response={"ok": True},
            safe_headers={},
        )

    monkeypatch.setattr(instant_service, "get_exchange_provider", lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order))
    monkeypatch.setattr(instant_service.InstantTradeService, "_bounded_reconcile", lambda *args, **kwargs: _preview_market_order())

    receipt = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert receipt.requested_amount == Decimal("5.00")
    assert receipt.provider_order_id == "O-1"
    assert calls["submit"] == 1


@pytest.mark.asyncio
async def test_unsupported_product_blocks(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)

    async def _unsupported_asset(**_kwargs):
        raise LookupError("active asset not found for live product")

    monkeypatch.setattr(instant_service, "_load_asset_for_product", _unsupported_asset)

    with pytest.raises(LookupError, match="active asset"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)


@pytest.mark.asyncio
async def test_repeated_idempotency_key_does_not_duplicate_submission(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    calls = {"submit": 0}

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(success=True, failure_reason=None, warning_messages=[], estimated_base_size=Decimal("0.00005"), estimated_fee=Decimal("0.01"), estimated_fee_currency="USD", estimated_average_price=Decimal("100000"), best_ask=Decimal("100000"))

    async def _submit_order(**_kwargs):
        calls["submit"] += 1
        return SimpleNamespace(classification="success", order=SimpleNamespace(provider_order_id="O-1", status="OPEN"), rejection=None, ambiguous=None, raw_response={}, safe_headers={})

    monkeypatch.setattr(instant_service, "get_exchange_provider", lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order))
    monkeypatch.setattr(instant_service.InstantTradeService, "_bounded_reconcile", lambda *args, **kwargs: _preview_market_order())

    first = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)
    second = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert first.internal_order_id == second.internal_order_id
    assert calls["submit"] == 1


@pytest.mark.asyncio
async def test_user_directed_buy_persists_risk_without_related_signal_id(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    observed: dict[str, object] = {"signal_id": "unset"}

    async def _persist(*, db, request):  # noqa: ANN001
        observed["signal_id"] = request.signal_id
        return SimpleNamespace(risk_event_id=uuid.uuid4())

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(
            success=True,
            failure_reason=None,
            warning_messages=[],
            estimated_base_size=Decimal("0.00005"),
            estimated_fee=Decimal("0.01"),
            estimated_fee_currency="USD",
            estimated_average_price=Decimal("100000"),
            best_ask=Decimal("100000"),
        )

    async def _submit_order(**_kwargs):
        return SimpleNamespace(
            classification="success",
            order=SimpleNamespace(provider_order_id="O-1", status="OPEN"),
            rejection=None,
            ambiguous=None,
            raw_response={"ok": True},
            safe_headers={},
        )

    monkeypatch.setattr(instant_service, "persist_risk_decision", _persist)
    monkeypatch.setattr(
        instant_service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order),
    )
    monkeypatch.setattr(instant_service.InstantTradeService, "_bounded_reconcile", lambda *args, **kwargs: _preview_market_order())

    await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert observed["signal_id"] is None


@pytest.mark.asyncio
async def test_provider_submission_happens_only_after_risk_persistence_and_durable_order(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    state: dict[str, object] = {"risk_persisted": False, "order_seen_before_submit": False}

    async def _persist(*, db, request):  # noqa: ANN001
        state["risk_persisted"] = True
        return SimpleNamespace(risk_event_id=uuid.uuid4())

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(
            success=True,
            failure_reason=None,
            warning_messages=[],
            estimated_base_size=Decimal("0.00005"),
            estimated_fee=Decimal("0.01"),
            estimated_fee_currency="USD",
            estimated_average_price=Decimal("100000"),
            best_ask=Decimal("100000"),
        )

    async def _submit_order(**_kwargs):
        state["order_seen_before_submit"] = len(db.orders_by_client_id) == 1
        return SimpleNamespace(
            classification="success",
            order=SimpleNamespace(provider_order_id="O-1", status="OPEN"),
            rejection=None,
            ambiguous=None,
            raw_response={"ok": True},
            safe_headers={},
        )

    monkeypatch.setattr(instant_service, "persist_risk_decision", _persist)
    monkeypatch.setattr(
        instant_service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order),
    )
    monkeypatch.setattr(instant_service.InstantTradeService, "_bounded_reconcile", lambda *args, **kwargs: _preview_market_order())

    await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert state["risk_persisted"] is True
    assert state["order_seen_before_submit"] is True


@pytest.mark.asyncio
async def test_risk_rejection_is_persisted_and_blocks_provider_submission(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    state: dict[str, int] = {"persist_calls": 0, "submit_calls": 0}

    async def _persist(*, db, request):  # noqa: ANN001
        state["persist_calls"] += 1
        return SimpleNamespace(risk_event_id=uuid.uuid4())

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(
            success=True,
            failure_reason=None,
            warning_messages=[],
            estimated_base_size=Decimal("0.00005"),
            estimated_fee=Decimal("0.01"),
            estimated_fee_currency="USD",
            estimated_average_price=Decimal("100000"),
            best_ask=Decimal("100000"),
        )

    async def _submit_order(**_kwargs):
        state["submit_calls"] += 1
        return SimpleNamespace(classification="success", order=SimpleNamespace(provider_order_id="O-1", status="OPEN"), rejection=None, ambiguous=None, raw_response={}, safe_headers={})

    monkeypatch.setattr(
        instant_service,
        "evaluate_signal_risk",
        lambda **_kwargs: SimpleNamespace(action=RiskDecisionAction.REJECT, approved_quantity=Decimal("0"), reason_code="max_daily_loss_breached"),
    )
    monkeypatch.setattr(instant_service, "persist_risk_decision", _persist)
    monkeypatch.setattr(
        instant_service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order),
    )

    with pytest.raises(Exception, match="Risk engine blocked instant buy"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert state["persist_calls"] == 1
    assert state["submit_calls"] == 0
    assert len(db.orders_by_client_id) == 0


@pytest.mark.asyncio
async def test_insufficient_balance_blocks(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request, balance=Decimal("1"))
    with pytest.raises(InvalidRequestError, match="Insufficient USD balance"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)


@pytest.mark.asyncio
async def test_precision_violation_blocks(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    bad = base_request.model_copy(update={"quote_amount": Decimal("5.001")})
    with pytest.raises(ValueError, match="precision"):
        await instant_service.service.buy(db=db, request=bad, authenticated_user_id=bad.actor)


@pytest.mark.asyncio
async def test_kill_switch_blocks(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request, global_engaged=True)
    with pytest.raises(Exception, match="kill switch"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)


@pytest.mark.asyncio
async def test_provider_timeout_returns_reconciliation_required(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(success=True, failure_reason=None, warning_messages=[], estimated_base_size=Decimal("0.00005"), estimated_fee=Decimal("0.01"), estimated_fee_currency="USD", estimated_average_price=Decimal("100000"), best_ask=Decimal("100000"))

    async def _submit_order(**_kwargs):
        raise TimeoutError()

    monkeypatch.setattr(instant_service, "get_exchange_provider", lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order))

    receipt = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert receipt.status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_database_timeout_fails_clearly(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)

    async def _slow_owned_account(**_kwargs):
        await instant_service.asyncio.sleep(0.05)
        return SimpleNamespace(
            id=base_request.paper_account_id,
            owner_user_id=uuid.UUID(base_request.actor),
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
        )

    monkeypatch.setattr(instant_service, "_load_owned_account", _slow_owned_account)
    monkeypatch.setattr(
        instant_service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_max_order_usd=Decimal("5"),
            instant_trade_db_timeout_seconds=0,
            instant_trade_provider_timeout_seconds=2,
            instant_trade_reconciliation_poll_timeout_seconds=1,
        ),
    )

    with pytest.raises(ServiceUnavailableError, match="timed out"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)


@pytest.mark.asyncio
async def test_db_checkout_pre_ping_path_is_bounded(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()

    async def _slow_connection():
        await instant_service.asyncio.sleep(0.05)
        return db

    db.connection = _slow_connection

    monkeypatch.setattr(
        instant_service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_max_order_usd=Decimal("5"),
            instant_trade_db_timeout_seconds=0,
            instant_trade_provider_timeout_seconds=2,
            instant_trade_reconciliation_poll_timeout_seconds=1,
        ),
    )

    with pytest.raises(ServiceUnavailableError, match="timed out"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)


@pytest.mark.asyncio
async def test_provider_ambiguity_does_not_resubmit(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    calls = {"submit": 0}

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(success=True, failure_reason=None, warning_messages=[], estimated_base_size=Decimal("0.00005"), estimated_fee=Decimal("0.01"), estimated_fee_currency="USD", estimated_average_price=Decimal("100000"), best_ask=Decimal("100000"))

    async def _submit_order(**_kwargs):
        calls["submit"] += 1
        return SimpleNamespace(classification="ambiguous", order=None, rejection=None, ambiguous=SimpleNamespace(reason="x"), raw_response={}, safe_headers={})

    monkeypatch.setattr(instant_service, "get_exchange_provider", lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order))

    first = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)
    second = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert first.status == "RECONCILIATION_REQUIRED"
    assert second.internal_order_id == first.internal_order_id
    assert calls["submit"] == 1


@pytest.mark.asyncio
async def test_risk_persistence_failure_causes_zero_provider_submission(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    state = {"submit_calls": 0}

    class _SyntheticForeignKeyViolation(RuntimeError):
        pass

    async def _persist(*, db, request):  # noqa: ANN001
        if request.signal_id is not None:
            raise _SyntheticForeignKeyViolation("risk_events_related_signal_id_fkey")
        raise _SyntheticForeignKeyViolation("risk_events_related_signal_id_fkey")

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(success=True, failure_reason=None, warning_messages=[], estimated_base_size=Decimal("0.00005"), estimated_fee=Decimal("0.01"), estimated_fee_currency="USD", estimated_average_price=Decimal("100000"), best_ask=Decimal("100000"))

    async def _submit_order(**_kwargs):
        state["submit_calls"] += 1
        return SimpleNamespace(classification="success", order=SimpleNamespace(provider_order_id="O-1", status="OPEN"), rejection=None, ambiguous=None, raw_response={}, safe_headers={})

    monkeypatch.setattr(instant_service, "persist_risk_decision", _persist)
    monkeypatch.setattr(
        instant_service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order),
    )

    with pytest.raises(_SyntheticForeignKeyViolation, match="related_signal_id"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert state["submit_calls"] == 0
    assert len(db.orders_by_client_id) == 0


@pytest.mark.asyncio
async def test_retry_same_idempotency_key_after_risk_persistence_failure_is_safe(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)
    state = {"persist_calls": 0, "submit_calls": 0}

    class _SyntheticForeignKeyViolation(RuntimeError):
        pass

    async def _persist(*, db, request):  # noqa: ANN001
        state["persist_calls"] += 1
        if state["persist_calls"] == 1:
            raise _SyntheticForeignKeyViolation("risk_events_related_signal_id_fkey")
        return SimpleNamespace(risk_event_id=uuid.uuid4())

    async def _preview_market_order(**_kwargs):
        return SimpleNamespace(success=True, failure_reason=None, warning_messages=[], estimated_base_size=Decimal("0.00005"), estimated_fee=Decimal("0.01"), estimated_fee_currency="USD", estimated_average_price=Decimal("100000"), best_ask=Decimal("100000"))

    async def _submit_order(**_kwargs):
        state["submit_calls"] += 1
        return SimpleNamespace(classification="success", order=SimpleNamespace(provider_order_id="O-1", status="OPEN"), rejection=None, ambiguous=None, raw_response={}, safe_headers={})

    monkeypatch.setattr(instant_service, "persist_risk_decision", _persist)
    monkeypatch.setattr(
        instant_service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: SimpleNamespace(preview_market_order=_preview_market_order, submit_order=_submit_order),
    )
    monkeypatch.setattr(instant_service.InstantTradeService, "_bounded_reconcile", lambda *args, **kwargs: _preview_market_order())

    with pytest.raises(_SyntheticForeignKeyViolation, match="related_signal_id"):
        await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    receipt = await instant_service.service.buy(db=db, request=base_request, authenticated_user_id=base_request.actor)

    assert receipt.provider_order_id == "O-1"
    assert state["submit_calls"] == 1
    assert len(db.orders_by_client_id) == 1


@pytest.mark.asyncio
async def test_adoption_optional_and_only_after_reconciliation(monkeypatch: pytest.MonkeyPatch, base_request: instant_service.InstantTradeBuyRequest) -> None:
    db = _FakeDb()
    _install_common_mocks(monkeypatch, base_request)

    order = LiveCryptoOrder(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(),
        exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_quote_size=Decimal("5.00"),
        client_order_id="c1",
        status="FILLED",
        risk_event_id=None,
        decision_record_id=None,
        validation_run_id=None,
        provider_order_id="O-1",
        provider_status="FILLED",
        submitted_at=datetime.now(timezone.utc),
        acknowledged_at=datetime.now(timezone.utc),
        filled_at=datetime.now(timezone.utc),
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={
            "paper_account_id": str(base_request.paper_account_id),
            "reconciliation": {"normalized_status": "filled", "total_filled_quantity": "0.00005", "weighted_average_fill_price": "100000", "fees": {"USD": "0.01"}},
        },
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.orders_by_id[order.live_crypto_order_id] = order

    async def _scalar_override(_statement):
        return order

    db.scalar = _scalar_override

    receipt = await instant_service.service.adopt_into_autonomous_management(
        db=db,
        order_id=order.live_crypto_order_id,
        actor=base_request.actor,
        authenticated_user_id=base_request.actor,
    )

    assert receipt.status == "FILLED"
    assert order.safe_provider_response["instant_trade_adoption"]["adopted"] is True
