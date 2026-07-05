from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.config import get_settings
from app.services.data.http_client import AsyncHTTPClient, ExternalAPIError


logger = logging.getLogger(__name__)


_KLINES_ENDPOINT = "/api/v3/klines"


@dataclass(slots=True)
class NormalizedCandle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str = "binance_us"


@dataclass(slots=True)
class BinanceClientError(Exception):
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


class BinanceUSClient:
    def __init__(
        self,
        http_client: AsyncHTTPClient,
        *,
        base_url: str | None = None,
        page_limit: int = 1000,
    ) -> None:
        self._http_client = http_client
        resolved_base_url = base_url
        if resolved_base_url is None:
            resolved_base_url = get_settings().binance_us_api_base

        self._base_url = resolved_base_url.rstrip("/")
        self._page_limit = page_limit

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime | None,
    ) -> list[NormalizedCandle]:
        if self._page_limit <= 0:
            raise ValueError("page_limit must be positive")

        current_start_ms = _to_unix_ms(start_time)
        end_ms = _to_unix_ms(end_time) if end_time else None

        candles: list[NormalizedCandle] = []

        while True:
            params: dict[str, str | int] = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start_ms,
                "limit": self._page_limit,
            }
            if end_ms is not None:
                params["endTime"] = end_ms

            try:
                response = await self._http_client.request(
                    "GET",
                    f"{self._base_url}{_KLINES_ENDPOINT}",
                    params=params,
                )
            except ExternalAPIError as exc:
                # TODO(audit_log): Persist this upstream ingestion failure once audit_log is implemented in this phase flow.
                logger.error(
                    "Binance.US kline ingestion failed: symbol=%s interval=%s start_time=%s end_time=%s endpoint=%s status_code=%s response_body=%s",
                    symbol,
                    interval,
                    start_time.isoformat(),
                    end_time.isoformat() if end_time else None,
                    exc.endpoint,
                    exc.status_code,
                    exc.response_body,
                )
                raise BinanceClientError(
                    message="Failed to fetch Binance.US klines",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                    cause=exc,
                ) from exc

            payload = response.json()
            if not isinstance(payload, list):
                raise BinanceClientError(
                    message="Unexpected Binance.US klines response payload",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                )

            if not payload:
                break

            parsed_page = [_normalize_kline(kline) for kline in payload]
            candles.extend(parsed_page)

            if len(payload) < self._page_limit:
                break

            next_start_ms = int(payload[-1][6]) + 1
            if end_ms is not None and next_start_ms > end_ms:
                break

            if next_start_ms <= current_start_ms:
                raise BinanceClientError(
                    message="Pagination did not advance for Binance.US klines",
                    symbol=symbol,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                )

            current_start_ms = next_start_ms

        return candles


def _normalize_kline(kline: list[object]) -> NormalizedCandle:
    return NormalizedCandle(
        open_time=_from_unix_ms(int(kline[0])),
        close_time=_from_unix_ms(int(kline[6])),
        open=Decimal(str(kline[1])),
        high=Decimal(str(kline[2])),
        low=Decimal(str(kline[3])),
        close=Decimal(str(kline[4])),
        volume=Decimal(str(kline[5])),
        source="binance_us",
    )


def _to_unix_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _from_unix_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)