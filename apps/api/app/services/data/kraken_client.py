from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.data.binance_client import NormalizedCandle
from app.services.data.http_client import AsyncHTTPClient, ExternalAPIError


logger = logging.getLogger(__name__)


_OHLC_ENDPOINT = "/0/public/OHLC"
_INTERVAL_TO_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


@dataclass(slots=True)
class KrakenClientError(Exception):
    message: str
    symbol: str
    interval: str
    start_time: datetime
    end_time: datetime | None
    cause: Exception | None = None

    def __str__(self) -> str:
        return (
            f"{self.message} symbol={self.symbol} interval={self.interval} "
            f"start_time={self.start_time.isoformat()} "
            f"end_time={self.end_time.isoformat() if self.end_time else None} "
            f"cause={self.cause}"
        )


class KrakenSpotClient:
    def __init__(
        self,
        http_client: AsyncHTTPClient,
        *,
        base_url: str = "https://api.kraken.com",
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime | None,
    ) -> list[NormalizedCandle]:
        interval_minutes = _INTERVAL_TO_MINUTES.get(interval)
        if interval_minutes is None:
            raise ValueError(f"Unsupported Kraken interval: {interval}")

        current_since = _to_unix_seconds(start_time)
        end_seconds = _to_unix_seconds(end_time) if end_time else None
        pair = _normalize_pair(symbol)

        candles: list[NormalizedCandle] = []

        while True:
            params: dict[str, str | int] = {
                "pair": pair,
                "interval": interval_minutes,
                "since": current_since,
            }

            try:
                response = await self._http_client.request(
                    "GET",
                    f"{self._base_url}{_OHLC_ENDPOINT}",
                    params=params,
                )
            except ExternalAPIError as exc:
                logger.error(
                    "Kraken OHLC ingestion failed: symbol=%s interval=%s start_time=%s end_time=%s endpoint=%s status_code=%s response_body=%s",
                    symbol,
                    interval,
                    start_time.isoformat(),
                    end_time.isoformat() if end_time else None,
                    exc.endpoint,
                    exc.status_code,
                    exc.response_body,
                )
                raise KrakenClientError(
                    message="Failed to fetch Kraken OHLC candles",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                    cause=exc,
                ) from exc

            payload = response.json()
            if not isinstance(payload, dict):
                raise KrakenClientError(
                    message="Unexpected Kraken OHLC response payload",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                )

            errors = payload.get("error")
            if isinstance(errors, list) and errors:
                raise KrakenClientError(
                    message="Kraken OHLC request returned errors",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                    cause=ValueError(str(errors)),
                )

            result = payload.get("result")
            if not isinstance(result, dict):
                raise KrakenClientError(
                    message="Kraken OHLC response missing result",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                )

            series = _extract_series(result, pair)
            if not series:
                break

            parsed_page = []
            for row in series:
                if len(row) < 8:
                    continue
                candle = _normalize_ohlc(row, interval_minutes=interval_minutes)
                if end_seconds is not None and _to_unix_seconds(candle.close_time) > end_seconds:
                    continue
                parsed_page.append(candle)

            candles.extend(parsed_page)

            next_since = int(result.get("last") or 0)
            if next_since <= current_since:
                break

            current_since = next_since
            if end_seconds is not None and current_since > end_seconds:
                break

        return candles


def _extract_series(result: dict[str, object], pair: str) -> list[list[object]]:
    candidates = [pair, pair.upper(), pair.replace("-", ""), pair.replace("/", "")]
    for candidate in candidates:
        series = result.get(candidate)
        if isinstance(series, list):
            return [row for row in series if isinstance(row, list)]

    for key, value in result.items():
        if key == "last":
            continue
        if isinstance(value, list):
            return [row for row in value if isinstance(row, list)]
    return []


def _normalize_pair(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("/", "-")
    if "-" in normalized:
        base, quote = normalized.split("-", 1)
    else:
        base, quote = _split_compound_symbol(normalized)

    canonical_base = {"BTC": "XBT", "XXBT": "XBT"}.get(base, base)
    return f"{canonical_base}{quote}"


def _split_compound_symbol(symbol: str) -> tuple[str, str]:
    quote_candidates = ("USDT", "USD", "EUR", "GBP", "BTC", "ETH")
    for quote in quote_candidates:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    if len(symbol) <= 3:
        return symbol, "USD"
    return symbol[:-3], symbol[-3:]


def _normalize_ohlc(row: list[object], *, interval_minutes: int) -> NormalizedCandle:
    open_time = _from_unix_seconds(int(row[0]))
    close_time = open_time + _interval_delta(interval_minutes)
    return NormalizedCandle(
        open_time=open_time,
        close_time=close_time,
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[6])),
        source="kraken_spot",
    )


def _interval_delta(interval_minutes: int) -> timedelta:
    return timedelta(minutes=interval_minutes)


def _to_unix_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _from_unix_seconds(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)