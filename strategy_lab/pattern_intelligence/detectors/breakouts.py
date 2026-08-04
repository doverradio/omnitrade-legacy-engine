from __future__ import annotations

from decimal import Decimal

from ..models import DetectorInput, Finding, FindingGroup
from .common import finding

DETECTOR_VERSION = "1.0.0"


def detect(data: DetectorInput) -> list[Finding]:
    results: list[Finding] = []
    window = data.config.structure_window
    start = max(data.selected_start, window)
    for index in range(start, data.selected_end + 1):
        prior = data.candles[index - window:index]
        upper = max(item.high for item in prior)
        lower = min(item.low for item in prior)
        candle = data.candles[index]
        bullish = candle.close > upper * (Decimal("1") + data.config.breakout_buffer_pct)
        bearish = candle.close < lower * (Decimal("1") - data.config.breakout_buffer_pct)
        if bullish or bearish:
            detector_id = "bullish_breakout_v1" if bullish else "bearish_breakdown_v1"
            name = "Bullish Breakout" if bullish else "Bearish Breakdown"
            level = upper if bullish else lower
            results.append(finding(data, detector_id, name, FindingGroup.BREAKOUTS, index, index,
                {"close": candle.close, "range_boundary": level, "escape_pct": abs(candle.close / level - 1)},
                {"breakout_buffer_pct": data.config.breakout_buffer_pct, "lookback_candles": window},
                ("close exceeded the buffered prior range boundary",)))
            horizon_end = min(data.selected_end, index + data.config.failed_breakout_window)
            future = data.candles[index + 1:horizon_end + 1]
            failed = bullish and any(item.close <= upper for item in future) or bearish and any(item.close >= lower for item in future)
            if failed:
                failure_index = next(item_index for item_index in range(index + 1, horizon_end + 1)
                    if (bullish and data.candles[item_index].close <= upper) or (bearish and data.candles[item_index].close >= lower))
                results.append(finding(data, "failed_breakout_v1" if bullish else "failed_breakdown_v1", "Failed Breakout" if bullish else "Failed Breakdown", FindingGroup.BREAKOUTS, index, failure_index,
                    {"breakout_level": level, "return_close": data.candles[failure_index].close, "failure_candles": failure_index - index},
                    {"maximum_failure_window": data.config.failed_breakout_window}, ("price closed back inside the prior range within the failure window",)))
            elif future and any((bullish and item.low <= upper and item.close > upper) or (bearish and item.high >= lower and item.close < lower) for item in future):
                results.append(finding(data, "breakout_retest_v1", "Breakout Retest", FindingGroup.BREAKOUTS, index, horizon_end,
                    {"breakout_level": level, "retest_window": len(future)}, {"maximum_retest_window": data.config.failed_breakout_window},
                    ("price retested the escaped boundary and closed beyond it",)))
            results.append(finding(data, "range_escape_v1", "Range Escape", FindingGroup.BREAKOUTS, index, index,
                {"close": candle.close, "prior_high": upper, "prior_low": lower}, {"lookback_candles": window},
                ("close finished outside the prior rolling range",)))
    return results