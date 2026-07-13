from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any
from uuid import UUID

from app.operator_cli.formatting import (
    render_candles_text,
    render_json,
    render_preview_show_text,
    render_preview_text,
    render_status_text,
)
from app.operator_cli.service import (
    execute_preview_cycle,
    fetch_candle_readiness,
    fetch_operator_status,
    fetch_preview_evidence,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator",
        description="OmniTrade operator CLI (preview-only and read-only evidence commands)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser(
        "preview",
        help="Run one autonomous preview cycle (never submits a live order)",
    )
    preview.add_argument("--mandate-id", type=UUID, required=True)
    preview.add_argument("--actor", type=str, default="operator:human")
    preview.add_argument("--product-id", type=str, default="BTC-USD")
    preview.add_argument("--strategy-interval", type=str, default="15m")
    preview.add_argument("--trigger", type=str, default="operator_cli")
    preview.add_argument("--idempotency-seed", type=str, default=None)
    preview.add_argument("--software-build-version", type=str, default=None)
    preview.add_argument("--forced-action", choices=["BUY", "SELL", "HOLD"], default=None)
    preview.add_argument("--json", action="store_true", dest="json_output")

    preview_show = subparsers.add_parser(
        "preview-show",
        help="Show persisted preview evidence and linked decision records",
    )
    preview_show.add_argument("--preview-id", type=UUID, required=True)
    preview_show.add_argument("--json", action="store_true", dest="json_output")

    candles = subparsers.add_parser(
        "candles",
        help="Inspect candle freshness/readiness for one symbol",
    )
    candles.add_argument("--symbol", type=str, required=True)
    candles.add_argument("--interval", type=str, default="15m")
    candles.add_argument("--exchange", type=str, default=None)
    candles.add_argument("--max-age-minutes", type=int, default=30)
    candles.add_argument("--lookback-limit", type=int, default=200)
    candles.add_argument("--json", action="store_true", dest="json_output")

    status = subparsers.add_parser(
        "status",
        help="Show current operator-safe status summary",
    )
    status.add_argument("--mandate-id", type=UUID, default=None)
    status.add_argument("--symbol", type=str, default=None)
    status.add_argument("--interval", type=str, default="15m")
    status.add_argument("--exchange", type=str, default=None)
    status.add_argument("--max-age-minutes", type=int, default=30)
    status.add_argument("--json", action="store_true", dest="json_output")

    return parser


async def _run_async(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    if args.command == "preview":
        payload = await execute_preview_cycle(
            mandate_id=args.mandate_id,
            actor=args.actor,
            product_id=args.product_id,
            strategy_interval=args.strategy_interval,
            trigger=args.trigger,
            idempotency_seed=args.idempotency_seed,
            software_build_version=args.software_build_version,
            forced_action=args.forced_action,
        )
        text = render_json(payload) if args.json_output else render_preview_text(payload)
        state = str(payload.get("state") or "")
        code = 0 if state in {"COMPLETE", "PREVIEW_READY"} else 1
        return code, payload, text

    if args.command == "preview-show":
        payload = await fetch_preview_evidence(preview_id=args.preview_id)
        text = render_json(payload) if args.json_output else render_preview_show_text(payload)
        return 0, payload, text

    if args.command == "candles":
        payload = await fetch_candle_readiness(
            symbol=args.symbol,
            interval=args.interval,
            exchange=args.exchange,
            max_age_minutes=args.max_age_minutes,
            lookback_limit=args.lookback_limit,
        )
        text = render_json(payload) if args.json_output else render_candles_text(payload)
        return (0 if payload.get("ready") else 1), payload, text

    payload = await fetch_operator_status(
        mandate_id=args.mandate_id,
        candle_symbol=args.symbol,
        candle_interval=args.interval,
        candle_exchange=args.exchange,
        candle_max_age_minutes=args.max_age_minutes,
    )
    text = render_json(payload) if args.json_output else render_status_text(payload)
    return 0, payload, text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        code, _payload, text = asyncio.run(_run_async(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
