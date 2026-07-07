from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_execution_quality_metric import LiveExecutionQualityMetric
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.contracts import LiveExecutionQualityCaptureRequest
from app.services.live.execution_quality import capture_live_execution_quality, read_live_execution_quality


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(
        self,
        *,
        execution_events: list[LiveExecutionEvent],
        reconciliation_events: list[LiveReconciliationEvent] | None = None,
        accounting_records: list[LiveAccountingRecord] | None = None,
    ) -> None:
        self.execution_events = execution_events
        self.reconciliation_events = reconciliation_events or []
        self.accounting_records = accounting_records or []
        self.quality_metrics: list[LiveExecutionQualityMetric] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_execution_quality_metrics" in sql and "idempotency_key_1" in params:
            key = params["idempotency_key_1"]
            for item in self.quality_metrics:
                if item.idempotency_key == key:
                    return item
            return None

        if "FROM live_execution_events" in sql:
            event_id = params.get("id_1")
            profile_id = params.get("live_trading_profile_id_1")
            for item in self.execution_events:
                if item.id == event_id and item.live_trading_profile_id == profile_id:
                    return item
            return None

        return None

    async def scalars(self, statement: Any) -> _Rows:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_reconciliation_events" in sql:
            source_execution_event_id = params.get("source_execution_event_id_1")
            rows = [
                item
                for item in self.reconciliation_events
                if item.source_execution_event_id == source_execution_event_id
            ]
            return _Rows(rows)

        if "FROM live_accounting_records" in sql:
            source_execution_event_id = params.get("source_execution_event_id_1")
            rows = [
                item
                for item in self.accounting_records
                if item.source_execution_event_id == source_execution_event_id
            ]
            return _Rows(rows)

        if "FROM live_execution_quality_metrics" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            rows = [item for item in self.quality_metrics if item.live_trading_profile_id == profile_id]
            return _Rows(rows)

        return _Rows([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveExecutionQualityMetric):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.quality_metrics.append(obj)

    async def flush(self) -> None:
        return None


def _execution_event(*, event_type: str = "execution_intent_created", payload: dict[str, Any] | None = None) -> LiveExecutionEvent:
    now = datetime.now(timezone.utc)
    return LiveExecutionEvent(
        id=uuid.uuid4(),
        idempotency_key=f"{event_type}-key",
        event_hash=f"{event_type}-hash",
        live_trading_profile_id=uuid.uuid4(),
        sequence_number=1,
        event_type=event_type,
        provider_name="paper-sim",
        risk_decision_id=uuid.uuid4(),
        approval_event_id=uuid.uuid4(),
        audit_correlation_id="audit-correlation-1",
        operating_mode="live",
        paper_default_mode=True,
        risk_authority_model="risk_engine_final",
        event_payload=payload or {"symbol": "AAPL", "side": "buy", "expected_price": "100"},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )


def _fill_reconciliation(source_event: LiveExecutionEvent) -> LiveReconciliationEvent:
    now = datetime.now(timezone.utc)
    return LiveReconciliationEvent(
        id=uuid.uuid4(),
        idempotency_key="rec-key",
        event_hash="rec-hash",
        live_trading_profile_id=source_event.live_trading_profile_id,
        source_execution_event_id=source_event.id,
        source_execution_event_type="execution_intent_created",
        sequence_number=1,
        event_type="fill_reconciled",
        reconciliation_status="filled",
        provider_name="paper-sim",
        provider_order_id="provider-order-1",
        provider_fill_id="provider-fill-1",
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )


def _fill_accounting(source_event: LiveExecutionEvent, rec_event: LiveReconciliationEvent) -> LiveAccountingRecord:
    now = datetime.now(timezone.utc)
    return LiveAccountingRecord(
        id=uuid.uuid4(),
        idempotency_key="acct-key",
        live_trading_profile_id=source_event.live_trading_profile_id,
        reconciliation_event_id=rec_event.id,
        source_execution_event_id=source_event.id,
        source_execution_event_type="execution_intent_created",
        record_type="fill_accounting",
        provider_order_id="provider-order-1",
        provider_fill_id="provider-fill-1",
        symbol="AAPL",
        side="buy",
        filled_quantity=Decimal("1"),
        fill_price=Decimal("101"),
        gross_notional=Decimal("101"),
        fee_amount=Decimal("1"),
        fee_currency="USD",
        net_cash_impact=Decimal("-102"),
        provenance={"source": "test"},
        recorded_at=now,
        created_at=now,
    )


def _capture_request(source_event: LiveExecutionEvent, **overrides: Any) -> LiveExecutionQualityCaptureRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": source_event.live_trading_profile_id,
        "source_execution_event_id": source_event.id,
        "market_context": {"regime": "trend", "volatility_bucket": "high", "liquidity_tier": "medium"},
        "requested_by": "operator",
        "provenance_metadata": {"ticket": "LIVE-97"},
        "idempotency_key": "quality-key-1",
    }
    payload.update(overrides)
    return LiveExecutionQualityCaptureRequest(**payload)


@pytest.mark.asyncio
async def test_capture_execution_quality_records_expected_vs_actual_slippage() -> None:
    source = _execution_event()
    rec = _fill_reconciliation(source)
    acct = _fill_accounting(source, rec)
    session = _FakeSession(execution_events=[source], reconciliation_events=[rec], accounting_records=[acct])

    result = await capture_live_execution_quality(db=session, request=_capture_request(source))

    assert result.accepted is True
    assert result.status == "recorded"
    assert len(session.quality_metrics) == 1
    metric = session.quality_metrics[0]
    assert metric.expected_price_state == "available"
    assert metric.actual_price_state == "available"
    assert metric.slippage_state == "available"
    assert Decimal(str(metric.slippage_bps)) > Decimal("0")


@pytest.mark.asyncio
async def test_capture_execution_quality_marks_unknown_expected_price() -> None:
    source = _execution_event(payload={"symbol": "AAPL", "side": "buy"})
    rec = _fill_reconciliation(source)
    acct = _fill_accounting(source, rec)
    session = _FakeSession(execution_events=[source], reconciliation_events=[rec], accounting_records=[acct])

    result = await capture_live_execution_quality(
        db=session,
        request=_capture_request(source, idempotency_key="quality-key-2"),
    )

    assert result.accepted is True
    metric = session.quality_metrics[0]
    assert metric.expected_price_state == "unknown"
    assert metric.slippage_state == "unknown"


@pytest.mark.asyncio
async def test_capture_execution_quality_marks_unavailable_actual_when_fill_missing() -> None:
    source = _execution_event()
    session = _FakeSession(execution_events=[source], reconciliation_events=[], accounting_records=[])

    result = await capture_live_execution_quality(
        db=session,
        request=_capture_request(source, idempotency_key="quality-key-3"),
    )

    assert result.accepted is True
    metric = session.quality_metrics[0]
    assert metric.actual_price_state == "unavailable"
    assert metric.slippage_state == "unavailable"


@pytest.mark.asyncio
async def test_capture_execution_quality_blocks_non_execution_intent_events() -> None:
    source = _execution_event(event_type="execution_blocked")
    session = _FakeSession(execution_events=[source])

    result = await capture_live_execution_quality(
        db=session,
        request=_capture_request(source, idempotency_key="quality-key-4"),
    )

    assert result.accepted is False
    assert result.reason == "source_execution_event_not_telemetry_eligible"
    assert len(session.quality_metrics) == 0


@pytest.mark.asyncio
async def test_execution_quality_read_model_aggregates_and_filters() -> None:
    source = _execution_event()
    rec = _fill_reconciliation(source)
    acct = _fill_accounting(source, rec)
    session = _FakeSession(execution_events=[source], reconciliation_events=[rec], accounting_records=[acct])

    await capture_live_execution_quality(
        db=session,
        request=_capture_request(source, idempotency_key="quality-key-5"),
    )

    unknown_source = _execution_event(payload={"symbol": "MSFT", "side": "sell"})
    unknown_source.live_trading_profile_id = source.live_trading_profile_id
    session.execution_events.append(unknown_source)
    await capture_live_execution_quality(
        db=session,
        request=_capture_request(
            unknown_source,
            idempotency_key="quality-key-6",
            market_context={"regime": "range", "volatility_bucket": "unknown", "liquidity_tier": "unknown"},
        ),
    )

    read_model = await read_live_execution_quality(
        db=session,
        live_trading_profile_id=source.live_trading_profile_id,
        symbol="AAPL",
    )

    assert read_model.total_records == 1
    assert read_model.available_slippage_records == 1
    assert read_model.unknown_or_unavailable_records == 0
    assert read_model.average_slippage_bps is not None


@pytest.mark.asyncio
async def test_capture_execution_quality_is_idempotent() -> None:
    source = _execution_event()
    rec = _fill_reconciliation(source)
    acct = _fill_accounting(source, rec)
    session = _FakeSession(execution_events=[source], reconciliation_events=[rec], accounting_records=[acct])
    request = _capture_request(source, idempotency_key="quality-key-7")

    first = await capture_live_execution_quality(db=session, request=request)
    second = await capture_live_execution_quality(db=session, request=request)

    assert first.status == "recorded"
    assert second.status == "replayed"
    assert len(session.quality_metrics) == 1
