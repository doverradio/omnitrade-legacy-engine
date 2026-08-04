from __future__ import annotations

from decimal import Decimal

from ..models import DetectorInput, Finding, FindingGroup
from .common import average, finding, sequence_direction, value

DETECTOR_VERSION = "1.0.0"


def detect(data: DetectorInput) -> list[Finding]:
    start, end = data.selected_start, data.selected_end
    selected = data.candles[start:end + 1]
    if len(selected) < 3:
        return []
    results: list[Finding] = []
    definitions = (
        ("higher_highs_v1", "Higher Highs", [item.high for item in selected], "higher"),
        ("higher_lows_v1", "Higher Lows", [item.low for item in selected], "higher"),
        ("lower_highs_v1", "Lower Highs", [item.high for item in selected], "lower"),
        ("lower_lows_v1", "Lower Lows", [item.low for item in selected], "lower"),
    )
    for detector_id, name, values, direction in definitions:
        if sequence_direction(values, direction):
            results.append(finding(data, detector_id, name, FindingGroup.PRICE_STRUCTURE, start, end,
                {"first_value": values[0], "last_value": values[-1], "consecutive_candles": len(values)},
                {"required_consecutive_candles": 3},
                (f"all {len(values) - 1} consecutive comparisons were strictly {direction}",)))

    high, low = max(item.high for item in selected), min(item.low for item in selected)
    range_pct = (high - low) / selected[0].close
    if range_pct <= data.config.flat_range_pct:
        results.append(finding(data, "flat_price_range_v1", "Flat Price Range", FindingGroup.PRICE_STRUCTURE, start, end,
            {"range_pct": range_pct, "range_high": high, "range_low": low}, {"maximum_range_pct": data.config.flat_range_pct},
            (f"range_pct <= {data.config.flat_range_pct}",)))
    range_ratio = value(data, "range_ratio", end)
    if range_pct <= data.config.flat_range_pct * Decimal("2") and range_ratio is not None and range_ratio <= data.config.contraction_ratio:
        results.append(finding(data, "consolidation_v1", "Consolidation", FindingGroup.PRICE_STRUCTURE, start, end,
            {"range_pct": range_pct, "range_ratio": range_ratio},
            {"maximum_range_pct": data.config.flat_range_pct * Decimal("2"), "maximum_range_ratio": data.config.contraction_ratio},
            ("selected range remained narrow", "current rolling range is below its baseline")))

    slope = value(data, "slope", end)
    acceleration = value(data, "slope_acceleration", end)
    if slope is not None and slope != 0:
        positive = slope > 0
        results.append(finding(data, "positive_slope_v1" if positive else "negative_slope_v1", "Positive Slope" if positive else "Negative Slope", FindingGroup.PRICE_STRUCTURE, start, end,
            {"least_squares_slope": slope}, {"zero_slope": Decimal("0")}, ("least-squares close slope > 0" if positive else "least-squares close slope < 0",)))
    if slope is not None and acceleration is not None and acceleration != 0:
        accelerating = slope * acceleration > 0
        results.append(finding(data, "slope_acceleration_v1" if accelerating else "slope_deceleration_v1", "Slope Acceleration" if accelerating else "Slope Deceleration", FindingGroup.PRICE_STRUCTURE, start, end,
            {"slope": slope, "slope_change": acceleration}, {"zero_change": Decimal("0")},
            ("slope and slope change have the same sign" if accelerating else "slope and slope change have opposite signs",)))

    tolerance = average([item.close for item in selected]) * data.config.support_tolerance_pct
    low_touches = sum(abs(item.low - low) <= tolerance for item in selected)
    high_touches = sum(abs(item.high - high) <= tolerance for item in selected)
    for detector_id, name, count, level in (("repeated_support_v1", "Repeated Support", low_touches, low), ("repeated_resistance_v1", "Repeated Resistance", high_touches, high)):
        if count >= 2:
            results.append(finding(data, detector_id, name, FindingGroup.PRICE_STRUCTURE, start, end,
                {"touch_count": count, "level": level, "tolerance": tolerance}, {"minimum_touches": 2, "tolerance_pct": data.config.support_tolerance_pct},
                ("at least two candle extremes were within tolerance of the level",)))
    return results