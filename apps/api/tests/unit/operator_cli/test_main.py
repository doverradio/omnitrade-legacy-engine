from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import app.operator_cli.main as operator_main
from app.operator_cli.main import parse_args


def test_parse_preview_command() -> None:
    args = parse_args([
        "preview",
        "--verbose",
        "--actor",
        "operator:test",
        "--product-id",
        "BTC-USD",
    ])

    assert args.command == "preview"
    assert args.mandate_id is None
    assert args.actor == "operator:test"
    assert args.product_id == "BTC-USD"
    assert args.strategy_interval == "15m"
    assert args.verbose is True


def test_resolve_preview_idempotency_seed_defaults_to_fresh_value(monkeypatch) -> None:
    monkeypatch.setattr(operator_main, "uuid4", lambda: SimpleNamespace(hex="fresh-seed"))
    args = parse_args([
        "preview",
        "--actor",
        "operator:test",
    ])

    assert operator_main._resolve_preview_idempotency_seed(args) == "fresh-seed"


def test_resolve_preview_idempotency_seed_can_reuse_existing_key() -> None:
    args = parse_args([
        "preview",
        "--reuse-idempotency-key",
        "--actor",
        "operator:test",
    ])

    assert operator_main._resolve_preview_idempotency_seed(args) is None


def test_parse_preview_show_command() -> None:
    args = parse_args([
        "preview-show",
        "--no-color",
        "--preview-id",
        "22222222-2222-2222-2222-222222222222",
        "--json",
    ])

    assert args.command == "preview-show"
    assert args.preview_id == UUID("22222222-2222-2222-2222-222222222222")
    assert args.json_output is True
    assert args.no_color is True


def test_parse_candles_command() -> None:
    args = parse_args([
        "candles",
        "--symbol",
        "BTC",
        "--interval",
        "15m",
        "--exchange",
        "kraken_spot",
        "--max-age-minutes",
        "12",
    ])

    assert args.command == "candles"
    assert args.symbol == "BTC"
    assert args.interval == "15m"
    assert args.exchange == "kraken_spot"
    assert args.max_age_minutes == 12


def test_parse_status_command() -> None:
    args = parse_args([
        "status",
        "--mandate-id",
        "33333333-3333-3333-3333-333333333333",
        "--symbol",
        "BTC",
    ])

    assert args.command == "status"
    assert args.mandate_id == UUID("33333333-3333-3333-3333-333333333333")
    assert args.symbol == "BTC"
    assert args.interval == "15m"


def test_parse_watch_command() -> None:
    args = parse_args([
        "watch",
        "--symbol",
        "BTC",
        "--refresh-seconds",
        "2",
    ])

    assert args.command == "watch"
    assert args.symbol == "BTC"
    assert args.refresh_seconds == 2


def test_parse_roster_command() -> None:
    args = parse_args([
        "roster",
        "--provider",
        "kraken_spot",
        "--product-id",
        "BTC-USD",
        "--interval",
        "15m",
    ])

    assert args.command == "roster"
    assert args.provider == "kraken_spot"
    assert args.product_id == "BTC-USD"
    assert args.interval == "15m"


def test_parse_scorecards_command() -> None:
    args = parse_args([
        "scorecards",
        "--provider",
        "kraken_spot",
        "--product-id",
        "BTC-USD",
        "--interval",
        "15m",
        "--json",
    ])

    assert args.command == "scorecards"
    assert args.provider == "kraken_spot"
    assert args.product_id == "BTC-USD"
    assert args.interval == "15m"
    assert args.json_output is True


def test_parse_execution_forensics_command_selectors() -> None:
    latest = parse_args([
        "execution-forensics",
        "--latest",
    ])
    assert latest.command == "execution-forensics"
    assert latest.latest is True
    assert latest.since is None
    assert latest.cycle is None

    since = parse_args([
        "execution-forensics",
        "--since",
        "2 hours ago",
    ])
    assert since.command == "execution-forensics"
    assert since.latest is False
    assert since.since == "2 hours ago"
    assert since.cycle is None

    cycle = parse_args([
        "execution-forensics",
        "--cycle",
        "44444444-4444-4444-4444-444444444444",
        "--json",
    ])
    assert cycle.command == "execution-forensics"
    assert cycle.latest is False
    assert cycle.since is None
    assert cycle.cycle == UUID("44444444-4444-4444-4444-444444444444")
    assert cycle.json_output is True
