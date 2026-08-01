from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from app.core.errors import InvalidRequestError
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient
from app.services.live.accounting_reconciliation import record_live_fill_reconciliation


TESTS_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = TESTS_ROOT / "fixtures" / "pipeline_contracts" / "btc_kraken_golden_scenarios.json"
ACCOUNTING_BUILDERS_PATH = TESTS_ROOT / "unit" / "services" / "live" / "test_live_accounting_reconciliation.py"
FIXED_TIME = "2026-07-15T14:30:00+00:00"
FIXED_SOURCE_ID = UUID("11111111-1111-4111-8111-111111111111")
FIXED_PROFILE_ID = UUID("22222222-2222-4222-8222-222222222222")


def _scenarios() -> dict[str, dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return {item["scenario"]: item for item in payload["scenarios"]}


def _load_existing_accounting_builders() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_golden_source_accounting_tests", ACCOUNTING_BUILDERS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DeterministicSession:
    def __init__(self, builders: ModuleType, source: object) -> None:
        self._delegate = builders._FakeSession(execution_events=[source])
        self._next_id = 1

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def add(self, obj: object) -> None:
        if isinstance(obj, (LiveReconciliationEvent, LiveAccountingRecord)) and not getattr(obj, "id", None):
            obj.id = UUID(f"00000000-0000-4000-8000-{self._next_id:012d}")
            self._next_id += 1
        self._delegate.add(obj)


def _kraken_request() -> ExchangeOrderSubmissionRequest:
    return ExchangeOrderSubmissionRequest(
        product_id="BTC-USD", side="BUY", order_type="MARKET",
        quote_size=Decimal("5"), base_size=None,
        client_order_id="cid", idempotency_key="cid", raw_payload={},
    )


async def _fake_public(*, path: str, **_kwargs) -> dict:
    if path == "/public/AssetPairs":
        return {"error": [], "result": {"XXBTZUSD": {
            "altname": "XBTUSD", "wsname": "XBT/USD", "base": "BTC", "quote": "USD",
            "status": "online", "pair_decimals": 1, "lot_decimals": 8,
            "ordermin": "0.00005", "costmin": "0.5",
        }}}
    return {"error": [], "result": {"XXBTZUSD": {"a": ["50000.0", "1", "1"], "b": ["49999.0", "1", "1"]}}}


@pytest.mark.asyncio
async def test_provider_rejection_matches_current_kraken_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _scenarios()["provider_rejection"]
    client = KrakenSpotClient()
    calls = 0

    async def _fake_private(**_kwargs):
        nonlocal calls
        calls += 1
        raise InvalidRequestError(message="Kraken API returned errors", details={
            "status_code": 200, "path": "/private/AddOrder",
            "errors": ["EOrder:Insufficient funds"],
            "response_body": {"error": ["EOrder:Insufficient funds"], "result": {}},
        })

    monkeypatch.setattr(client, "_public_request", _fake_public)
    monkeypatch.setattr(client, "_private_request", _fake_private)
    result = await client.submit_order(credentials={"api_key": "fake", "api_secret": "fake"}, environment="production", request=_kraken_request())

    assert calls == fixture["expected"]["provider_submission_count"]
    assert result.classification == fixture["expected"]["provider_submission_classification"]
    assert result.classification == fixture["expected"]["provider_outcome"]


@pytest.mark.asyncio
async def test_ambiguous_result_matches_current_kraken_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _scenarios()["ambiguous_provider_result"]
    client = KrakenSpotClient()
    calls = 0

    async def _fake_private(*, path: str, **_kwargs):
        nonlocal calls
        calls += 1
        assert path == "/private/AddOrder"
        return {"error": [], "result": {"descr": {"order": "buy market"}}}

    monkeypatch.setattr(client, "_public_request", _fake_public)
    monkeypatch.setattr(client, "_private_request", _fake_private)
    result = await client.submit_order(credentials={"api_key": "fake", "api_secret": "fake"}, environment="production", request=_kraken_request())

    assert calls == fixture["expected"]["provider_submission_count"]
    assert result.classification == "ambiguous"
    assert result.ambiguous is not None
    assert result.ambiguous.reason == fixture["expected"]["reason_code"]
    assert fixture["expected"]["provider_outcome"] == "ambiguous_after_possible_submission"


def _fixed_fill_source_and_session() -> tuple[ModuleType, object, _DeterministicSession]:
    builders = _load_existing_accounting_builders()
    source = builders._execution_event("execution_intent_created")
    source.id = FIXED_SOURCE_ID
    source.live_trading_profile_id = FIXED_PROFILE_ID
    source.recorded_at = builders.datetime.fromisoformat(FIXED_TIME)
    source.created_at = builders.datetime.fromisoformat(FIXED_TIME)
    return builders, source, _DeterministicSession(builders, source)


@pytest.mark.asyncio
async def test_partial_fill_persistence_matches_existing_builder_and_current_writer() -> None:
    fixture = _scenarios()["partial_or_delayed_order"]
    builders, source, session = _fixed_fill_source_and_session()
    request = builders._fill_request(
        source, provider_name="kraken_spot", symbol="BTC-USD",
        fill_quantity="0.5", cumulative_filled_quantity="0.5", order_quantity="1.0",
        idempotency_key="golden-partial-fill",
    )
    result = await record_live_fill_reconciliation(db=session, request=request)

    assert result.accepted is True
    assert session.reconciliation_events[0].reconciliation_status == fixture["expected"]["provider_outcome"]
    observed_effects = ["reconciliation_event_insert"] + [
        record.record_type + "_insert" for record in session.accounting_records
    ]
    assert observed_effects == fixture["expected"]["database_effects"]
    assert fixture["expected"]["provider_submission_count"] == 0


@pytest.mark.asyncio
async def test_reconciliation_and_accounting_idempotency_match_current_writer() -> None:
    scenarios = _scenarios()
    builders, source, session = _fixed_fill_source_and_session()
    request = builders._fill_request(
        source, provider_name="kraken_spot", symbol="BTC-USD",
        idempotency_key="golden-reconciliation-fill",
    )
    first = await record_live_fill_reconciliation(db=session, request=request)
    second = await record_live_fill_reconciliation(db=session, request=request)

    assert first.status == "recorded"
    assert second.status == "replayed"
    assert len(session.reconciliation_events) == 1
    assert len(session.accounting_records) == 2
    assert scenarios["reconciliation"]["expected"]["idempotency"]["duplicate_fill_replays_existing_ids"] is True
    assert scenarios["accounting"]["expected"]["idempotency"]["duplicate_fill_replays_existing_ids"] is True
    observed_effects = ["reconciliation_event_insert"] + [
        record.record_type + "_insert" for record in session.accounting_records
    ]
    assert observed_effects == scenarios["reconciliation"]["expected"]["database_effects"]
    assert observed_effects == scenarios["accounting"]["expected"]["database_effects"]
