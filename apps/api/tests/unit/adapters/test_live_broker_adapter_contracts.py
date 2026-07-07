from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.live.broker_adapters.contracts import (
    BrokerIdempotencyContract,
    NormalizedBrokerError,
    NormalizedBrokerOrderRequest,
    NormalizedFill,
    NormalizedOrderStatus,
    NormalizedRejection,
    ProviderBrokerRequestEnvelope,
    ProviderBrokerResponseEnvelope,
    RequiredOrchestrationIdentifiers,
)


def _required_ids() -> RequiredOrchestrationIdentifiers:
    return RequiredOrchestrationIdentifiers(
        risk_decision_id=uuid.uuid4(),
        approval_event_id=uuid.uuid4(),
        audit_correlation_id="audit-corr-1",
    )


def _idempotency() -> BrokerIdempotencyContract:
    return BrokerIdempotencyContract(
        idempotency_key="idem-key-1",
        idempotency_group="submit_order",
    )


def _order_request(**overrides: object) -> NormalizedBrokerOrderRequest:
    payload = {
        "orchestration_ids": _required_ids(),
        "idempotency": _idempotency(),
        "adapter_request_id": "adapter-req-1",
        "broker_account_ref": "acct-1",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "limit",
        "quantity": Decimal("1.5"),
        "limit_price": Decimal("200.00"),
        "stop_price": None,
        "time_in_force": "day",
        "requested_at": datetime.now(timezone.utc),
        "metadata": {"source": "orchestration"},
    }
    payload.update(overrides)
    return NormalizedBrokerOrderRequest(**payload)


def _provider_response(**overrides: object) -> ProviderBrokerResponseEnvelope:
    payload = {
        "orchestration_ids": _required_ids(),
        "idempotency": _idempotency(),
        "adapter_request_id": "adapter-req-1",
        "provider_name": "paper-sim",
        "provider_status_code": 200,
        "payload": {"status": "accepted"},
        "received_at": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    return ProviderBrokerResponseEnvelope(**payload)


def test_required_orchestration_identifiers_enforce_audit_correlation_id() -> None:
    with pytest.raises(ValueError, match="audit_correlation_id is required"):
        RequiredOrchestrationIdentifiers(
            risk_decision_id=uuid.uuid4(),
            approval_event_id=uuid.uuid4(),
            audit_correlation_id="   ",
        )


def test_order_request_requires_valid_normalized_shape() -> None:
    request = _order_request()

    assert request.orchestration_ids.audit_correlation_id == "audit-corr-1"
    assert request.idempotency.idempotency_key == "idem-key-1"
    assert request.side == "buy"


def test_order_request_rejects_invalid_side() -> None:
    with pytest.raises(ValueError, match="unsupported order side"):
        _order_request(side="hold")


def test_provider_request_and_response_contracts_require_provider_name() -> None:
    with pytest.raises(ValueError, match="provider_name is required"):
        ProviderBrokerRequestEnvelope(
            orchestration_ids=_required_ids(),
            idempotency=_idempotency(),
            adapter_request_id="adapter-req-1",
            provider_name="",
            endpoint_operation="submit_order",
            payload={"x": 1},
            created_at=datetime.now(timezone.utc),
        )

    with pytest.raises(ValueError, match="provider_name is required"):
        _provider_response(provider_name="")


def test_normalized_order_status_validates_status_domain() -> None:
    status = NormalizedOrderStatus(
        orchestration_ids=_required_ids(),
        idempotency=_idempotency(),
        adapter_request_id="adapter-req-1",
        provider_order_id="provider-order-1",
        client_order_id="client-order-1",
        status="accepted",
        reason=None,
        observed_at=datetime.now(timezone.utc),
        raw_payload={"status": "accepted"},
    )
    assert status.status == "accepted"

    with pytest.raises(ValueError, match="unsupported normalized order status"):
        NormalizedOrderStatus(
            orchestration_ids=_required_ids(),
            idempotency=_idempotency(),
            adapter_request_id="adapter-req-2",
            provider_order_id="provider-order-2",
            client_order_id="client-order-2",
            status="not_a_status",
            reason=None,
            observed_at=datetime.now(timezone.utc),
            raw_payload={"status": "unknown"},
        )


def test_normalized_fill_requires_positive_quantity_and_price() -> None:
    fill = NormalizedFill(
        orchestration_ids=_required_ids(),
        idempotency=_idempotency(),
        adapter_request_id="adapter-req-1",
        provider_fill_id="fill-1",
        provider_order_id="provider-order-1",
        client_order_id="client-order-1",
        symbol="AAPL",
        filled_quantity=Decimal("0.25"),
        fill_price=Decimal("201.10"),
        fee_amount=Decimal("0.15"),
        fee_currency="USD",
        liquidity="maker",
        observed_at=datetime.now(timezone.utc),
        raw_payload={"fill": "ok"},
    )
    assert fill.provider_fill_id == "fill-1"

    with pytest.raises(ValueError, match="filled_quantity must be positive"):
        NormalizedFill(
            orchestration_ids=_required_ids(),
            idempotency=_idempotency(),
            adapter_request_id="adapter-req-2",
            provider_fill_id="fill-2",
            provider_order_id="provider-order-2",
            client_order_id="client-order-2",
            symbol="AAPL",
            filled_quantity=Decimal("0"),
            fill_price=Decimal("201.10"),
            fee_amount=Decimal("0"),
            fee_currency="USD",
            liquidity=None,
            observed_at=datetime.now(timezone.utc),
            raw_payload={"fill": "bad"},
        )


def test_normalized_rejection_and_error_use_shared_category_domain() -> None:
    rejection = NormalizedRejection(
        orchestration_ids=_required_ids(),
        idempotency=_idempotency(),
        adapter_request_id="adapter-req-1",
        provider_order_id=None,
        client_order_id="client-order-1",
        category="broker_rejected",
        error_code="R001",
        message="Order rejected by provider",
        retriable=False,
        observed_at=datetime.now(timezone.utc),
        raw_payload={"reason": "notional too low"},
    )
    assert rejection.category == "broker_rejected"

    error = NormalizedBrokerError(
        orchestration_ids=_required_ids(),
        idempotency=_idempotency(),
        adapter_request_id="adapter-req-1",
        category="service_unavailable",
        error_code="SVC_DOWN",
        message="Provider unavailable",
        details={"retry_after": 5},
        retriable=True,
        observed_at=datetime.now(timezone.utc),
    )
    assert asdict(error)["details"]["retry_after"] == 5

    with pytest.raises(ValueError, match="unsupported rejection category"):
        NormalizedRejection(
            orchestration_ids=_required_ids(),
            idempotency=_idempotency(),
            adapter_request_id="adapter-req-3",
            provider_order_id=None,
            client_order_id="client-order-3",
            category="invalid_category",
            error_code="X",
            message="x",
            retriable=False,
            observed_at=datetime.now(timezone.utc),
            raw_payload={},
        )