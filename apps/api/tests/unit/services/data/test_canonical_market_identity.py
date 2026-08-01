from __future__ import annotations

import builtins
import importlib
import os
import socket
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

import app.services.data.canonical_market_identity as identity_module
from app.services.data.canonical_market_identity import (
    ECONOMIC_CANDLE_SLOT_KEY_VERSION,
    ECONOMIC_CANDLE_SLOT_NAMESPACE,
    ECONOMIC_INSTRUMENT_NAMESPACE,
    ECONOMIC_SPOT_INSTRUMENT_KEY_VERSION,
    CandleSlotId,
    EconomicCandleSlotKeyV1,
    EconomicSpotInstrumentKeyV1,
    derive_candle_slot_id,
    derive_instrument_id,
)
from app.services.pipeline_contracts.identifiers import InstrumentId


AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
OTHER_INSTRUMENT_ID = InstrumentId(value=UUID("f65e6af1-ffca-568f-b621-8f9835d51a78"))


def _instrument_key(**overrides: object) -> EconomicSpotInstrumentKeyV1:
    values: dict[str, object] = {
        "key_version": ECONOMIC_SPOT_INSTRUMENT_KEY_VERSION,
        "asset_class": "crypto",
        "base_asset": "BTC",
        "quote_asset": "USD",
        "instrument_kind": "spot",
    }
    values.update(overrides)
    return EconomicSpotInstrumentKeyV1.model_validate(values)


def _slot_key(**overrides: object) -> EconomicCandleSlotKeyV1:
    values: dict[str, object] = {
        "key_version": ECONOMIC_CANDLE_SLOT_KEY_VERSION,
        "instrument_id": derive_instrument_id(_instrument_key()),
        "interval": "15m",
        "open_time": AT,
    }
    values.update(overrides)
    return EconomicCandleSlotKeyV1.model_validate(values)


def test_btc_usd_spot_has_fixed_provider_independent_instrument_vector() -> None:
    key = _instrument_key()
    expected = InstrumentId(value=UUID("2bd84f78-488f-58fc-8a49-9a8f78915086"))
    assert derive_instrument_id(key) == expected
    assert derive_instrument_id(key) == derive_instrument_id(_instrument_key())
    assert key.canonical_bytes() == (
        b'{"asset_class":"crypto","base_asset":"BTC","instrument_kind":"spot",'
        b'"key_version":"economic-spot-instrument-key/v1","quote_asset":"USD"}'
    )


def test_instrument_identity_is_order_independent_and_semantically_sensitive() -> None:
    reordered = EconomicSpotInstrumentKeyV1.model_validate({
        "quote_asset": "USD", "instrument_kind": "spot", "base_asset": "BTC",
        "asset_class": "crypto", "key_version": ECONOMIC_SPOT_INSTRUMENT_KEY_VERSION,
    })
    baseline = derive_instrument_id(_instrument_key())
    assert derive_instrument_id(reordered) == baseline
    assert derive_instrument_id(_instrument_key(base_asset="ETH")) != baseline
    assert derive_instrument_id(_instrument_key(quote_asset="EUR")) != baseline


@pytest.mark.parametrize("overrides", [
    {"asset_class": "stock"},
    {"instrument_kind": "future"},
    {"base_asset": "XBT"},
    {"base_asset": "XXBT"},
    {"base_asset": "btc"},
    {"base_asset": "BtC"},
    {"base_asset": " BTC"},
    {"base_asset": "BTC-USD"},
    {"base_asset": "BTC", "quote_asset": "BTC"},
    {"base_asset": ""},
    {"quote_asset": ""},
])
def test_instrument_key_rejects_unsupported_ambiguous_or_noncanonical_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _instrument_key(**overrides)


def test_instrument_key_requires_every_field_and_forbids_provider_contamination() -> None:
    values = _instrument_key().model_dump()
    for field in tuple(values):
        incomplete = dict(values)
        incomplete.pop(field)
        with pytest.raises(ValidationError):
            EconomicSpotInstrumentKeyV1.model_validate(incomplete)
    for field in ("provider", "venue", "requested_pair", "response_series_key", "leverage"):
        with pytest.raises(ValidationError):
            EconomicSpotInstrumentKeyV1.model_validate({**values, field: "kraken"})


def test_kraken_aliases_are_not_instrument_identity_inputs() -> None:
    fields = set(EconomicSpotInstrumentKeyV1.model_fields)
    assert fields == {"key_version", "asset_class", "base_asset", "quote_asset", "instrument_kind"}
    assert not fields.intersection({"provider", "venue", "XBTUSD", "XXBTZUSD"})


def test_candle_slot_has_fixed_vector_and_deterministic_serialization() -> None:
    key = _slot_key()
    expected = CandleSlotId(value=UUID("7a6251c8-db9c-52e0-b02a-cab178fb9ebe"))
    assert derive_candle_slot_id(key) == expected
    assert derive_candle_slot_id(key) == derive_candle_slot_id(_slot_key())
    assert key.canonical_bytes() == (
        b'{"instrument_id":"2bd84f78-488f-58fc-8a49-9a8f78915086","interval":"15m",'
        b'"key_version":"economic-candle-slot-key/v1","open_time":"2026-07-15T12:00:00.000000Z"}'
    )


def test_candle_slot_changes_only_for_its_identity_fields() -> None:
    baseline = derive_candle_slot_id(_slot_key())
    assert derive_candle_slot_id(_slot_key(instrument_id=OTHER_INSTRUMENT_ID)) != baseline
    assert derive_candle_slot_id(_slot_key(interval="1h")) != baseline
    assert derive_candle_slot_id(_slot_key(open_time=AT + timedelta(minutes=15))) != baseline
    fields = set(EconomicCandleSlotKeyV1.model_fields)
    assert fields == {"key_version", "instrument_id", "interval", "open_time"}
    assert not fields.intersection({
        "provider", "venue", "requested_pair", "response_series_key",
        "open", "high", "low", "close", "volume",
    })


def test_utc_equivalent_times_share_one_slot_and_naive_time_fails() -> None:
    offset_time = datetime(2026, 7, 15, 5, 0, tzinfo=timezone(timedelta(hours=-7)))
    assert derive_candle_slot_id(_slot_key(open_time=offset_time)) == derive_candle_slot_id(_slot_key())
    with pytest.raises(ValidationError, match="timezone-aware"):
        _slot_key(open_time=datetime(2026, 7, 15, 12, 0))


@pytest.mark.parametrize("interval", ["", "15", "15M", "900s", "quarter-hour"])
def test_candle_slot_rejects_noncanonical_interval_aliases(interval: str) -> None:
    with pytest.raises(ValidationError):
        _slot_key(interval=interval)


def test_candle_slot_rejects_missing_malformed_and_extra_identity_fields() -> None:
    values = _slot_key().model_dump()
    for field in tuple(values):
        incomplete = dict(values)
        incomplete.pop(field)
        with pytest.raises(ValidationError):
            EconomicCandleSlotKeyV1.model_validate(incomplete)
    with pytest.raises(ValidationError):
        EconomicCandleSlotKeyV1.model_validate({**values, "instrument_id": {"value": "invalid"}})
    with pytest.raises(ValidationError, match="UUIDv5"):
        _slot_key(instrument_id=InstrumentId(value=UUID("11111111-1111-4111-8111-111111111111")))
    for extra in ("provider", "venue", "open", "acquired_at", "run_id"):
        with pytest.raises(ValidationError):
            EconomicCandleSlotKeyV1.model_validate({**values, extra: "not-an-identity-field"})


def test_instrument_and_candle_slot_namespaces_and_types_are_distinct() -> None:
    assert ECONOMIC_INSTRUMENT_NAMESPACE != ECONOMIC_CANDLE_SLOT_NAMESPACE
    instrument_id = derive_instrument_id(_instrument_key())
    slot_id = derive_candle_slot_id(_slot_key())
    assert instrument_id != slot_id

    class RequiresInstrument(BaseModel):
        instrument_id: InstrumentId

    class RequiresSlot(BaseModel):
        slot_id: CandleSlotId

    with pytest.raises(ValidationError):
        RequiresInstrument(instrument_id=slot_id)
    with pytest.raises(ValidationError):
        RequiresSlot(slot_id=instrument_id)


def test_identity_construction_has_no_external_or_generated_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("identity construction attempted external or generated state")

    class ForbiddenEnviron(dict):
        def __getitem__(self, _key):
            raise AssertionError("identity construction attempted environment access")

        def get(self, _key, _default=None):
            raise AssertionError("identity construction attempted environment access")

        def __contains__(self, _key):
            raise AssertionError("identity construction attempted environment access")

        def __iter__(self):
            raise AssertionError("identity construction attempted environment access")

    class ForbiddenDateTime(datetime):
        @classmethod
        def now(cls, *_args, **_kwargs):
            raise AssertionError("identity construction attempted wall-clock access")

        @classmethod
        def utcnow(cls):
            raise AssertionError("identity construction attempted wall-clock access")

        @classmethod
        def today(cls):
            raise AssertionError("identity construction attempted wall-clock access")

    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(os, "environ", ForbiddenEnviron())
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr("time.time", forbidden)
    monkeypatch.setattr("time.time_ns", forbidden)
    monkeypatch.setattr("uuid.uuid1", forbidden)
    monkeypatch.setattr("uuid.uuid4", forbidden)
    monkeypatch.setattr(identity_module, "datetime", ForbiddenDateTime)

    instrument_id = derive_instrument_id(_instrument_key())
    assert derive_candle_slot_id(_slot_key(instrument_id=instrument_id)).value.version == 5


def test_isolated_import_has_no_application_side_effects() -> None:
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
        from collections.abc import MutableMapping

        module_name = "app.services.data.canonical_market_identity"

        def protected_caller():
            return sys._getframe(2).f_globals.get("__name__", "") == module_name

        def guard(original):
            def guarded(*args, **kwargs):
                if protected_caller():
                    raise AssertionError("application-level side effect during identity import")
                return original(*args, **kwargs)
            return guarded

        class GuardedEnviron(MutableMapping):
            def __init__(self, delegate):
                self.delegate = delegate
            def _check(self):
                if sys._getframe(2).f_globals.get("__name__", "") == module_name:
                    raise AssertionError("application-level environment access during identity import")
            def __getitem__(self, key):
                self._check()
                return self.delegate[key]
            def get(self, key, default=None):
                self._check()
                return self.delegate.get(key, default)
            def __contains__(self, key):
                self._check()
                return key in self.delegate
            def __iter__(self):
                self._check()
                return iter(self.delegate)
            def __len__(self):
                self._check()
                return len(self.delegate)
            def __setitem__(self, key, value):
                self.delegate[key] = value
            def __delitem__(self, key):
                del self.delegate[key]

        class ForbiddenProductionImport(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith((
                    "app.config", "app.db", "app.models", "app.api",
                    "app.services.exchange_connections", "app.services.orchestration",
                    "app.services.controlled_proof", "app.services.live",
                )):
                    raise AssertionError(f"forbidden production import: {fullname}")
                return None

        sys.meta_path.insert(0, ForbiddenProductionImport())
        os.environ = GuardedEnviron(os.environ)
        os.getenv = guard(os.getenv)
        socket.socket = guard(socket.socket)
        socket.create_connection = guard(socket.create_connection)
        time.time = guard(time.time)
        time.time_ns = guard(time.time_ns)
        uuid.uuid1 = guard(uuid.uuid1)
        uuid.uuid4 = guard(uuid.uuid4)
        builtins.open = guard(builtins.open)
        pathlib.Path.open = guard(pathlib.Path.open)
        pathlib.Path.read_text = guard(pathlib.Path.read_text)
        pathlib.Path.read_bytes = guard(pathlib.Path.read_bytes)
        assert module_name not in sys.modules
        module = importlib.import_module(module_name)
        assert callable(module.derive_instrument_id)
        assert callable(module.derive_candle_slot_id)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], check=False, capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[4],
    )
    assert completed.returncode == 0, completed.stderr


def test_no_existing_production_module_imports_identity_foundation() -> None:
    app_root = Path(__file__).resolve().parents[4] / "app"
    target = app_root / "services" / "data" / "canonical_market_identity.py"
    consumers = [
        path for path in app_root.rglob("*.py")
        if path != target and "canonical_market_identity" in path.read_text(encoding="utf-8")
    ]
    assert consumers == []
