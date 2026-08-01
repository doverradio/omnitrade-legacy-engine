from __future__ import annotations

import builtins
import os
import socket
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.data.binance_client import NormalizedCandle
from app.services.exchange_connections.providers.base import (
    ExchangeOrderSubmissionRequest,
    ExchangeOrderSubmissionResult,
    ExchangeProviderAmbiguousResponse,
    ExchangeProviderFee,
    ExchangeProviderFill,
    ExchangeProviderOrder,
    ExchangeProviderRejection,
)
from app.services.pipeline_contracts.btc_kraken import (
    BTC_KRAKEN_INSTRUMENT_VERSION,
    BtcKrakenInstrumentV1,
    ExecutionSide,
    ProviderOutcome,
    StrategyAction,
)
from app.services.pipeline_contracts.btc_kraken_adapters import (
    candle_observation_from_legacy,
    execution_intent_from_legacy_request,
    provider_fill_reference_from_legacy,
    provider_submission_result_from_legacy,
    strategy_evaluation_from_legacy,
)
from app.services.pipeline_contracts.envelope import CANONICAL_ENVELOPE_SCHEMA_VERSION, CanonicalEnvelopeV1
from app.services.pipeline_contracts.identifiers import (
    AssetId,
    AssetIdentity,
    EventId,
    ExecutionClaimId,
    InstrumentId,
    InstrumentIdentity,
    LineageAuthority,
    LineageKind,
    LineageReference,
    PackageId,
)
from app.services.strategies.base import Signal


AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _envelope() -> CanonicalEnvelopeV1:
    return CanonicalEnvelopeV1(
        event_id=EventId(value=_uuid(1)), event_type="legacy_adaptation",
        schema_version=CANONICAL_ENVELOPE_SCHEMA_VERSION, source="test:legacy",
        occurred_at=AT, received_at=AT, stage_version="legacy/v1",
    )


def _instrument() -> BtcKrakenInstrumentV1:
    return BtcKrakenInstrumentV1(
        schema_version=BTC_KRAKEN_INSTRUMENT_VERSION,
        asset_identity=AssetIdentity(asset_id=AssetId(value=_uuid(2)), symbol="BTC"),
        instrument_identity=InstrumentIdentity(instrument_id=InstrumentId(value=_uuid(3)), canonical_symbol="BTC-USD"),
        internal_product="BTC-USD", canonical_base_asset="BTC", quote_asset="USD",
        provider="kraken_spot", provider_asset_code="XBT", provider_pair="XXBTZUSD",
    )


def _lineage(kind: LineageKind, authority: LineageAuthority = LineageAuthority.VERIFIED) -> LineageReference:
    return LineageReference(
        kind=kind, authority=authority,
        value=None if authority is LineageAuthority.ABSENT else f"{kind.value.lower()}-evidence",
    )


def _request(side: str = "BUY") -> ExchangeOrderSubmissionRequest:
    return ExchangeOrderSubmissionRequest(
        product_id="BTC-USD", side=side, order_type="MARKET",
        quote_size=Decimal("5.00") if side == "BUY" else None,
        base_size=Decimal("0.00005000") if side == "SELL" else None,
        client_order_id="client-1", idempotency_key="client-1", raw_payload={},
    )


def _intent(request: ExchangeOrderSubmissionRequest):
    return execution_intent_from_legacy_request(
        request, envelope=_envelope(), instrument=_instrument(),
        package_id=PackageId(value=_uuid(4)), claim_id=ExecutionClaimId(value=_uuid(5)),
        authorization_evidence=_lineage(LineageKind.APPROVAL),
        custody_evidence=_lineage(LineageKind.EXECUTION_CLAIM),
    )


def test_normalized_kraken_candle_maps_exact_values_and_identities() -> None:
    legacy = NormalizedCandle(
        open_time=AT, close_time=datetime(2026, 7, 15, 12, 15, tzinfo=timezone.utc),
        open=Decimal("50000.00"), high=Decimal("50100.00"), low=Decimal("49900.00"),
        close=Decimal("50050.00"), volume=Decimal("1.2300"), source="kraken_spot",
    )
    result = candle_observation_from_legacy(legacy, envelope=_envelope(), instrument=_instrument(), interval="15m")
    assert (result.open, result.volume) == (Decimal("50000.00"), Decimal("1.2300"))
    assert result.open_time == AT and result.instrument.asset_identity.asset_id.value == _uuid(2)
    assert (result.instrument.canonical_base_asset, result.instrument.provider_asset_code) == ("BTC", "XBT")
    assert (result.instrument.internal_product, result.instrument.provider_pair, result.instrument.quote_asset) == ("BTC-USD", "XXBTZUSD", "USD")
    assert b'"volume":"1.2300"' in result.canonical_bytes()
    with pytest.raises(ValueError, match="source"):
        candle_observation_from_legacy(replace(legacy, source="binance_us"), envelope=_envelope(), instrument=_instrument(), interval="15m")


@pytest.mark.parametrize(("legacy_action", "canonical_action"), [("buy", StrategyAction.BUY), ("sell", StrategyAction.SELL), ("hold", StrategyAction.HOLD)])
def test_strategy_signal_preserves_all_actions(legacy_action: str, canonical_action: StrategyAction) -> None:
    signal = Signal(action=legacy_action, strength=Decimal("0.40"), reason="Current strategy explanation", indicators={}, timestamp=AT)
    result = strategy_evaluation_from_legacy(
        signal, envelope=_envelope(), instrument=_instrument(), strategy_identity="momentum", strategy_version="1",
    )
    assert result.action is canonical_action
    assert result.strength == Decimal("0.40") and result.reason == signal.reason and result.evaluated_at == AT


def test_strategy_adapter_rejects_naive_time_and_does_not_invent_lineage() -> None:
    signal = SimpleNamespace(action="hold", strength=Decimal("0.4"), reason="hold", timestamp=datetime(2026, 7, 15))
    with pytest.raises(ValidationError, match="timezone-aware"):
        strategy_evaluation_from_legacy(signal, envelope=_envelope(), instrument=_instrument(), strategy_identity="s", strategy_version="1")
    aware = SimpleNamespace(**{**signal.__dict__, "timestamp": AT})
    assert strategy_evaluation_from_legacy(aware, envelope=_envelope(), instrument=_instrument(), strategy_identity="s", strategy_version="1").source_signal_ref is None


def test_execution_request_preserves_buy_sell_sizing_and_cannot_adapt_hold() -> None:
    buy = _intent(_request("BUY"))
    sell = _intent(_request("SELL"))
    assert buy.side is ExecutionSide.BUY and buy.quote_notional == Decimal("5.00") and buy.base_quantity is None
    assert sell.side is ExecutionSide.SELL and sell.base_quantity == Decimal("0.00005000") and sell.quote_notional is None
    assert buy.internal_client_order_id == "client-1" and buy.grants_authority is False
    with pytest.raises(ValueError, match="side"):
        _intent(_request("HOLD"))


@pytest.mark.parametrize(
    "authority",
    [LineageAuthority.SYNTHETIC, LineageAuthority.ABSENT, LineageAuthority.LEGACY_UNVERIFIED],
)
def test_execution_request_rejects_identity_mismatch_and_unverified_authority(authority: LineageAuthority) -> None:
    wrong_product = replace(_request(), product_id="XBTUSD")
    with pytest.raises(ValueError, match="product"):
        _intent(wrong_product)
    with pytest.raises(ValueError, match="VERIFIED"):
        execution_intent_from_legacy_request(
            _request(), envelope=_envelope(), instrument=_instrument(), package_id=PackageId(value=_uuid(4)),
            claim_id=ExecutionClaimId(value=_uuid(5)),
            authorization_evidence=_lineage(LineageKind.APPROVAL, authority),
            custody_evidence=_lineage(LineageKind.EXECUTION_CLAIM),
        )


def _submission(classification: str) -> ExchangeOrderSubmissionResult:
    order = ExchangeProviderOrder(
        provider_order_id="KRAKEN-1" if classification == "success" else None,
        client_order_id="client-1", product_id="BTC-USD", side="BUY", status="OPEN", submitted_at=AT,
        acknowledged_at=AT if classification == "success" else None,
    )
    return ExchangeOrderSubmissionResult(
        classification=classification,
        order=order if classification != "rejected" else None,
        rejection=ExchangeProviderRejection(
            code="invalid_arguments", message="rejected", provider_status="REJECTED",
            retryable=False, safe_details={"errors": "EOrder:Invalid"},
        ) if classification == "rejected" else None,
        ambiguous=ExchangeProviderAmbiguousResponse(
            reason="missing_provider_order_id", safe_details={"result_keys": ["descr"]},
        ) if classification == "ambiguous" else None,
    )


@pytest.mark.parametrize(
    ("classification", "outcome"),
    [("success", ProviderOutcome.ACCEPTED), ("rejected", ProviderOutcome.REJECTED), ("ambiguous", ProviderOutcome.AMBIGUOUS)],
)
def test_provider_submission_classes_remain_distinct(classification: str, outcome: ProviderOutcome) -> None:
    adapted = provider_submission_result_from_legacy(_submission(classification), envelope=_envelope(), internal_client_order_id="client-1")
    assert adapted.outcome is outcome
    assert adapted.blind_resubmission_permitted is False
    if outcome is ProviderOutcome.ACCEPTED:
        assert adapted.provider_order_id is not None
    if outcome is ProviderOutcome.AMBIGUOUS:
        assert adapted.provider_order_id is None and adapted.reconciliation_required is True
        assert ("result_keys", '["descr"]') in adapted.safe_error_fields


def test_provider_mapping_order_is_deterministic_and_input_is_detached() -> None:
    source = {"z": "last", "a": "first"}
    rejection = _submission("rejected").rejection
    first_legacy = replace(_submission("rejected"), rejection=replace(rejection, safe_details=source))
    second_legacy = replace(_submission("rejected"), rejection=replace(rejection, safe_details={"a": "first", "z": "last"}))
    first = provider_submission_result_from_legacy(first_legacy, envelope=_envelope(), internal_client_order_id="client-1")
    second = provider_submission_result_from_legacy(second_legacy, envelope=_envelope(), internal_client_order_id="client-1")
    source["a"] = "changed"
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.integrity_hash() == second.integrity_hash()
    assert first.safe_error_fields is not source


def test_provider_contradictions_and_missing_identity_fail() -> None:
    contradictory = ExchangeOrderSubmissionResult(
        classification="success", order=_submission("success").order,
        rejection=_submission("rejected").rejection, ambiguous=None,
    )
    with pytest.raises(ValueError, match="contradictory"):
        provider_submission_result_from_legacy(contradictory, envelope=_envelope(), internal_client_order_id="client-1")
    successful = _submission("success")
    missing = replace(successful, order=replace(successful.order, provider_order_id=None))
    with pytest.raises(ValidationError, match="provider_order_id"):
        provider_submission_result_from_legacy(missing, envelope=_envelope(), internal_client_order_id="client-1")


def test_provider_fill_preserves_decimal_fee_identity_time_and_order() -> None:
    fills = [
        ExchangeProviderFill("fill-2", "order-1", "BTC-USD", Decimal("0.20"), Decimal("50001.00"), ExchangeProviderFee(Decimal("0.01"), "USD"), AT),
        ExchangeProviderFill("fill-1", "order-1", "BTC-USD", Decimal("0.10"), Decimal("50000.00"), None, AT),
    ]
    adapted = tuple(provider_fill_reference_from_legacy(fill) for fill in fills)
    assert tuple(fill.provider_fill_id for fill in adapted) == ("fill-2", "fill-1")
    assert adapted[0].quantity == Decimal("0.20") and adapted[0].fee_amount == Decimal("0.01")
    assert adapted[0].fee_asset == "USD" and adapted[0].occurred_at == AT
    with pytest.raises(ValueError, match="identity"):
        provider_fill_reference_from_legacy(replace(fills[0], provider_fill_id=None))


def test_adapter_calls_have_no_external_or_generated_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("adapter attempted external or generated state")

    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr("uuid.uuid4", forbidden)
    monkeypatch.setattr("time.time", forbidden)
    assert _intent(_request()).internal_client_order_id == "client-1"
    assert provider_submission_result_from_legacy(_submission("ambiguous"), envelope=_envelope(), internal_client_order_id="client-1").outcome is ProviderOutcome.AMBIGUOUS


def test_isolated_adapter_import_has_no_application_side_effects() -> None:
    script = textwrap.dedent(
        """
        import builtins
        import importlib
        import importlib.abc
        import os
        import pathlib
        import socket
        import sys
        import time
        import uuid

        package_name = "app.services.pipeline_contracts"
        adapter_name = f"{package_name}.btc_kraken_adapters"

        def guard(original):
            def guarded(*args, **kwargs):
                caller = sys._getframe(1).f_globals.get("__name__", "")
                if caller == package_name or caller.startswith(f"{package_name}."):
                    raise AssertionError("application-level access during adapter import")
                return original(*args, **kwargs)
            return guarded

        class ForbiddenProductionImport(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith((
                    "app.api",
                    "app.config",
                    "app.db",
                    "app.models",
                    "app.services.controlled_proof",
                    "app.services.data",
                    "app.services.exchange_connections",
                    "app.services.live",
                    "app.services.orchestration",
                )):
                    raise AssertionError(f"forbidden production import: {fullname}")
                return None

        sys.meta_path.insert(0, ForbiddenProductionImport())
        os.getenv = guard(os.getenv)
        socket.socket = guard(socket.socket)
        socket.create_connection = guard(socket.create_connection)
        time.time = guard(time.time)
        time.time_ns = guard(time.time_ns)
        uuid.uuid4 = guard(uuid.uuid4)
        builtins.open = guard(builtins.open)
        pathlib.Path.open = guard(pathlib.Path.open)
        pathlib.Path.read_text = guard(pathlib.Path.read_text)
        pathlib.Path.read_bytes = guard(pathlib.Path.read_bytes)
        assert package_name not in sys.modules
        assert adapter_name not in sys.modules
        package = importlib.import_module(package_name)
        assert adapter_name in sys.modules
        for name in (
            "candle_observation_from_legacy",
            "strategy_evaluation_from_legacy",
            "execution_intent_from_legacy_request",
            "provider_submission_result_from_legacy",
            "provider_fill_reference_from_legacy",
        ):
            assert callable(getattr(package, name))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], check=False, capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[4],
    )
    assert completed.returncode == 0, completed.stderr
