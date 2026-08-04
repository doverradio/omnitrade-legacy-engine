from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.schemas.strategy_lab_offline import StrategyLabDatasetCreateRequest, StrategyLabParameters, StrategyLabReplayRequest

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from strategy_lab.capital import CapitalPolicy, apply_capital_policy  # noqa: E402
from strategy_lab.candles import Candle, load_candles_csv  # noqa: E402
from strategy_lab.comparison import CostScenario, buy_and_hold_ending_value  # noqa: E402
from strategy_lab.config import SimulationConfig  # noqa: E402
from strategy_lab.costs import CostModel  # noqa: E402
from strategy_lab.engine import run_simulation  # noqa: E402
from strategy_lab.metrics import compute_metrics  # noqa: E402
from strategy_lab.strategies.trailing_limit_v1 import TrailingLimitV1Strategy  # noqa: E402
from strategy_lab.strategies.trailing_limit_v2 import TrailingLimitV2Strategy  # noqa: E402

_DATASET_ROOT = _PROJECT_ROOT / "strategy_lab_data"
_REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def _dataset_path(dataset_id: str) -> Path:
    if not dataset_id or Path(dataset_id).name != dataset_id:
        raise InvalidRequestError(message="Invalid dataset identifier")
    path = (_DATASET_ROOT / f"{dataset_id}.csv").resolve()
    if path.parent != _DATASET_ROOT.resolve() or not path.is_file():
        raise NotFoundError(message="Strategy Laboratory dataset not found", details={"dataset_id": dataset_id})
    return path


@lru_cache(maxsize=16)
def _load_cached(path_text: str, modified_ns: int) -> tuple[Candle, ...]:
    del modified_ns
    return tuple(load_candles_csv(path_text))


def load_dataset(dataset_id: str) -> tuple[Candle, ...]:
    path = _dataset_path(dataset_id)
    return _load_cached(str(path), path.stat().st_mtime_ns)


def _identity(path: Path) -> tuple[str, str]:
    parts = path.stem.split("_")
    asset = parts[0].upper() if parts else path.stem.upper()
    interval = parts[-1] if len(parts) > 1 else "unknown"
    return asset, interval


def _interval_seconds(interval: str | None) -> int | None:
    if not interval:
        return None
    match = re.fullmatch(r"(\d+)(s|m|h|d|w)", interval.strip().lower())
    if not match:
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return int(match.group(1)) * multipliers[match.group(2)]


def analyze_csv(csv_text: str, interval: str | None = None) -> tuple[dict[str, object], list[Candle]]:
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    fields = [field.strip().lower() for field in (reader.fieldnames or [])]
    missing_columns = sorted(set(_REQUIRED_COLUMNS) - set(fields))
    errors: list[str] = []
    candles: list[Candle] = []
    total_rows = 0
    invalid_rows = 0
    if missing_columns:
        return ({
            "valid": False, "required_columns": list(_REQUIRED_COLUMNS), "missing_columns": missing_columns,
            "total_rows": 0, "candle_count": 0, "first_timestamp": None, "last_timestamp": None,
            "missing_candles": 0, "duplicate_timestamps": 0, "invalid_rows": 0,
            "errors": [f"Missing required columns: {', '.join(missing_columns)}"],
        }, [])
    for line_number, raw_row in enumerate(reader, start=2):
        total_rows += 1
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw_row.items()}
        try:
            timestamp_text = row["timestamp"]
            if timestamp_text.endswith("Z"):
                timestamp_text = timestamp_text[:-1] + "+00:00"
            timestamp = datetime.fromisoformat(timestamp_text)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            candle = Candle(
                timestamp=timestamp,
                open=Decimal(row["open"]), high=Decimal(row["high"]), low=Decimal(row["low"]),
                close=Decimal(row["close"]), volume=Decimal(row["volume"]),
            )
            if candle.volume < 0:
                raise ValueError("volume must be non-negative")
            candles.append(candle)
        except Exception as exc:  # noqa: BLE001 - validation report captures row context
            invalid_rows += 1
            if len(errors) < 20:
                errors.append(f"Row {line_number}: {exc}")
    candles.sort(key=lambda candle: candle.timestamp)
    duplicate_timestamps = sum(1 for index in range(1, len(candles)) if candles[index].timestamp == candles[index - 1].timestamp)
    seconds = _interval_seconds(interval)
    missing_candles = 0
    if seconds:
        for previous, current in zip(candles, candles[1:]):
            gap_seconds = int((current.timestamp - previous.timestamp).total_seconds())
            if gap_seconds > seconds:
                missing_candles += max(0, gap_seconds // seconds - 1)
    valid = not missing_columns and invalid_rows == 0 and duplicate_timestamps == 0 and bool(candles)
    if duplicate_timestamps:
        errors.append(f"Found {duplicate_timestamps} duplicate timestamp(s)")
    if not candles and not errors:
        errors.append("CSV contains no candle rows")
    return ({
        "valid": valid, "required_columns": list(_REQUIRED_COLUMNS), "missing_columns": missing_columns,
        "total_rows": total_rows, "candle_count": len(candles),
        "first_timestamp": candles[0].timestamp.isoformat() if candles else None,
        "last_timestamp": candles[-1].timestamp.isoformat() if candles else None,
        "missing_candles": missing_candles, "duplicate_timestamps": duplicate_timestamps,
        "invalid_rows": invalid_rows, "errors": errors,
    }, candles)


def _metadata(path: Path) -> dict[str, object]:
    metadata_path = path.with_suffix(".meta.json")
    if not metadata_path.is_file():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def create_dataset(payload: StrategyLabDatasetCreateRequest) -> dict[str, object]:
    report, candles = analyze_csv(payload.csv_text, payload.interval)
    if not report["valid"]:
        raise InvalidRequestError(message="Candle CSV validation failed", details=report)
    normalized = io.StringIO()
    writer = csv.writer(normalized, lineterminator="\n")
    writer.writerow(_REQUIRED_COLUMNS)
    for candle in candles:
        writer.writerow((candle.timestamp.isoformat(), candle.open, candle.high, candle.low, candle.close, candle.volume))
    normalized_text = normalized.getvalue()
    identity = "\n".join((payload.asset.upper().strip(), payload.exchange.strip(), payload.interval.lower().strip(), normalized_text))
    slug = re.sub(r"[^a-z0-9]+", "_", payload.name.lower()).strip("_")[:60] or payload.asset.lower()
    dataset_id = f"{slug}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"
    _DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    csv_path = _DATASET_ROOT / f"{dataset_id}.csv"
    metadata_path = csv_path.with_suffix(".meta.json")
    if csv_path.exists() or metadata_path.exists():
        raise ConflictError(message="This immutable dataset already exists", details={"dataset_id": dataset_id})
    metadata = {
        "id": dataset_id, "name": payload.name.strip(), "asset": payload.asset.upper().strip(),
        "exchange": payload.exchange.strip(), "interval": payload.interval.lower().strip(),
        "missing_candles": report["missing_candles"], "duplicate_timestamps": 0, "invalid_rows": 0,
    }
    created_csv = False
    created_metadata = False
    try:
        with csv_path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(normalized_text)
        created_csv = True
        with metadata_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        created_metadata = True
    except FileExistsError as exc:
        if created_csv:
            csv_path.unlink(missing_ok=True)
        raise ConflictError(message="This immutable dataset already exists", details={"dataset_id": dataset_id}) from exc
    except Exception:
        if created_csv:
            csv_path.unlink(missing_ok=True)
        if created_metadata:
            metadata_path.unlink(missing_ok=True)
        raise
    return {**metadata, "candle_count": len(candles), "first_timestamp": candles[0].timestamp, "last_timestamp": candles[-1].timestamp}


def list_datasets() -> list[dict[str, object]]:
    items = []
    if not _DATASET_ROOT.exists():
        return items
    for path in sorted(_DATASET_ROOT.glob("*.csv")):
        candles = _load_cached(str(path), path.stat().st_mtime_ns)
        if not candles:
            continue
        fallback_asset, fallback_interval = _identity(path)
        metadata = _metadata(path)
        asset = str(metadata.get("asset", fallback_asset))
        interval = str(metadata.get("interval", fallback_interval))
        items.append({
            "id": path.stem,
            "name": metadata.get("name", f"{asset} {interval} historical candles"),
            "asset": asset,
            "exchange": metadata.get("exchange", "offline_csv"),
            "interval": interval,
            "candle_count": len(candles),
            "first_timestamp": candles[0].timestamp,
            "last_timestamp": candles[-1].timestamp,
            "missing_candles": metadata.get("missing_candles", 0),
            "duplicate_timestamps": metadata.get("duplicate_timestamps", 0),
            "invalid_rows": metadata.get("invalid_rows", 0),
        })
    return items


def _slice_candles(candles: tuple[Candle, ...], start: datetime | None, end: datetime | None) -> tuple[Candle, ...]:
    if start is not None and end is not None and start >= end:
        raise InvalidRequestError(message="Replay start time must be before end time")
    selected = tuple(
        candle for candle in candles
        if (start is None or candle.timestamp >= start) and (end is None or candle.timestamp <= end)
    )
    if not selected:
        raise InvalidRequestError(message="No candles exist in the selected research period")
    return selected


def _config(parameters: StrategyLabParameters, interval: str) -> SimulationConfig:
    return SimulationConfig(
        entry_offset_pct=parameters.entry_offset_pct,
        initial_stop_pct=parameters.initial_stop_pct,
        profit_activation_pct=parameters.profit_activation_pct,
        trailing_distance_pct=parameters.trailing_distance_pct,
        required_declining_candles=parameters.required_declining_candles,
        fee_pct=parameters.fee_pct,
        slippage_pct=parameters.slippage_pct,
        initial_capital=parameters.initial_capital,
        candle_interval=interval,
        intra_candle_ambiguity_policy="pessimistic",
    )


def _decimal(value) -> str | None:
    return None if value is None else str(value)


def run_replay(payload: StrategyLabReplayRequest) -> dict[str, object]:
    path = _dataset_path(payload.dataset_id)
    asset, interval = _identity(path)
    candles = _slice_candles(load_dataset(payload.dataset_id), payload.start_time, payload.end_time)
    config = _config(payload.parameters, interval)
    strategy = TrailingLimitV1Strategy(config) if payload.strategy_version == "001" else TrailingLimitV2Strategy(config)
    result = run_simulation(candles, strategy, config, CostModel.from_config(config))
    metrics = compute_metrics(result)
    policy = CapitalPolicy(
        name="custom",
        trade_deployment_pct=payload.parameters.trade_deployment_pct,
        profit_compound_pct=payload.parameters.profit_compound_pct,
        profit_withdrawal_pct=payload.parameters.profit_withdrawal_pct,
        profit_tax_reserve_pct=payload.parameters.profit_tax_reserve_pct,
    )
    capital = apply_capital_policy(result.trades, payload.parameters.initial_capital, policy)
    scenario = CostScenario("custom", payload.parameters.fee_pct, payload.parameters.slippage_pct, "Research inputs")
    buy_hold = buy_and_hold_ending_value(candles, payload.parameters.initial_capital, scenario)
    cost_model = CostModel(fee_pct=scenario.fee_pct, slippage_pct=scenario.slippage_pct)
    buy_hold_quantity = (
        payload.parameters.initial_capital * (Decimal("1") - scenario.fee_pct)
        / cost_model.effective_buy_price(candles[0].open)
    )
    capital_curve = []
    record_index = 0
    trading_capital = payload.parameters.initial_capital
    withdrawn_profit = Decimal("0")
    total_economic_value = payload.parameters.initial_capital
    for candle_index, candle in enumerate(candles):
        while record_index < len(capital.records) and capital.records[record_index].trade.exit_candle_index <= candle_index:
            record = capital.records[record_index]
            trading_capital = record.trading_capital_after
            withdrawn_profit = record.cumulative_withdrawn_after
            total_economic_value = record.total_economic_value_after
            record_index += 1
        capital_curve.append({
            "timestamp": candle.timestamp.isoformat(),
            "trading_capital": str(trading_capital),
            "withdrawn_profit": str(withdrawn_profit),
            "total_economic_value": str(total_economic_value),
            "buy_and_hold": str(buy_hold_quantity * candle.close),
        })
    buy_hold_return = ((buy_hold / payload.parameters.initial_capital) - Decimal("1")) * Decimal("100")
    outperformance = capital.total_economic_value_final - buy_hold
    if len(candles) < 200 or metrics.total_trades < 3:
        verdict = "INSUFFICIENT DATA"
    elif capital.total_economic_value_final > buy_hold and metrics.net_return_pct > 0:
        verdict = "PROFITABLE"
    elif metrics.net_return_pct > 0:
        verdict = "MARGINAL"
    else:
        verdict = "UNPROFITABLE"

    return {
        "dataset": {
            "id": payload.dataset_id,
            "asset": asset,
            "exchange": "offline_csv",
            "interval": interval,
            "candle_count": len(candles),
            "first_timestamp": candles[0].timestamp.isoformat(),
            "last_timestamp": candles[-1].timestamp.isoformat(),
            "research_period": payload.research_period,
        },
        "strategy_version": payload.strategy_version,
        "parameters": {key: _decimal(value) if isinstance(value, Decimal) else value for key, value in payload.parameters.model_dump().items()},
        "candles": [
            {"timestamp": item.timestamp.isoformat(), "open": str(item.open), "high": str(item.high), "low": str(item.low), "close": str(item.close), "volume": str(item.volume)}
            for item in candles
        ],
        "events": [
            {"candle_index": event.candle_index, "timestamp": event.timestamp.isoformat(), "kind": event.kind, "price": str(event.price), "reason": event.reason}
            for event in result.replay_events
        ],
        "trades": [
            {
                **{key: (_decimal(value) if isinstance(value, Decimal) else value) for key, value in asdict(trade).items()},
                "entry_timestamp": trade.entry_timestamp.isoformat(),
                "exit_timestamp": trade.exit_timestamp.isoformat(),
                "exit_reason": trade.exit_reason.value,
                "mfe_pct": str((trade.highest_price_during_trade / trade.raw_fill_price - 1) * 100),
                "mae_pct": str((trade.lowest_price_during_trade / trade.raw_fill_price - 1) * 100),
                "net_pnl": str(trade.equity_after - trade.equity_before),
            }
            for trade in result.trades
        ],
        "equity_curve": [
            {"timestamp": candle.timestamp.isoformat(), "equity": str(equity)}
            for candle, equity in zip(candles, result.equity_curve)
        ],
        "capital_curve": capital_curve,
        "metrics": {
            **{key: (_decimal(value) if isinstance(value, Decimal) else value) for key, value in asdict(metrics).items()},
            "starting_capital": str(payload.parameters.initial_capital),
            "ending_trading_capital": str(capital.trading_capital_final),
            "withdrawn_profit": str(capital.cumulative_withdrawn_final),
            "tax_reserve": str(capital.cumulative_tax_reserve_final),
            "total_economic_value": str(capital.total_economic_value_final),
            "buy_and_hold_value": str(buy_hold),
            "buy_and_hold_return_pct": str(buy_hold_return),
            "outperformance": str(outperformance),
            "verdict": verdict,
            "profit_mode_activations": result.profit_mode_activations,
            "initial_stop_exits": result.initial_stop_exits,
            "trailing_exits": result.trailing_exits,
            "declining_close_exits": result.declining_close_exits,
        },
    }
