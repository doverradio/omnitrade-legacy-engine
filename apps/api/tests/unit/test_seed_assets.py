from __future__ import annotations

from decimal import Decimal

from scripts.seed_assets import extract_filter_decimal, parse_exchange_symbols


def test_extract_filter_decimal_returns_decimal_for_present_value() -> None:
    filters = [{"filterType": "MIN_NOTIONAL", "minNotional": "1.00000000"}]

    value = extract_filter_decimal(filters, "MIN_NOTIONAL", "minNotional")

    assert value == Decimal("1.00000000")


def test_extract_filter_decimal_returns_none_for_invalid_decimal() -> None:
    filters = [{"filterType": "LOT_SIZE", "stepSize": "not-a-number"}]

    value = extract_filter_decimal(filters, "LOT_SIZE", "stepSize")

    assert value is None


def test_parse_exchange_symbols_includes_only_trading_symbols_with_metadata() -> None:
    payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "quoteAsset": "USDT",
                "filters": [
                    {"filterType": "MIN_NOTIONAL", "minNotional": "1.00"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
                ],
            },
            {
                "symbol": "SOLUSDT",
                "status": "BREAK",
                "quoteAsset": "USDT",
                "filters": [],
            },
        ]
    }

    parsed = parse_exchange_symbols(payload)

    assert set(parsed.keys()) == {"BTCUSDT"}
    assert parsed["BTCUSDT"].quote_asset == "USDT"
    assert parsed["BTCUSDT"].min_order_notional == Decimal("1.00")
    assert parsed["BTCUSDT"].qty_step_size == Decimal("0.00001000")


def test_parse_exchange_symbols_sets_filter_fields_to_none_when_missing() -> None:
    payload = {
        "symbols": [
            {
                "symbol": "ETHUSDT",
                "status": "TRADING",
                "quoteAsset": "USDT",
                "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"}],
            }
        ]
    }

    parsed = parse_exchange_symbols(payload)

    assert parsed["ETHUSDT"].min_order_notional is None
    assert parsed["ETHUSDT"].qty_step_size is None
