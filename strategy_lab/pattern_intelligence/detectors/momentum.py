from __future__ import annotations

from decimal import Decimal

from ..models import DetectorInput, Finding, FindingGroup
from .common import finding, value

DETECTOR_VERSION = "1.0.0"


def detect(data: DetectorInput) -> list[Finding]:
    end = data.selected_end
    short = value(data, "short_momentum", end)
    medium = value(data, "medium_momentum", end)
    acceleration = value(data, "momentum_acceleration", end)
    recovery = value(data, "recovery", end)
    exhaustion = value(data, "exhaustion_proxy", end)
    if short is None:
        return []
    results: list[Finding] = []
    bullish = short > 0 and (medium is None or medium > 0)
    bearish = short < 0 and (medium is None or medium < 0)
    if bullish or bearish:
        results.append(finding(data, "bullish_momentum_v1" if bullish else "bearish_momentum_v1", "Bullish Momentum" if bullish else "Bearish Momentum", FindingGroup.MOMENTUM, data.selected_start, end,
            {"short_momentum": short, "medium_momentum": medium}, {"zero_momentum": Decimal("0")},
            ("short and medium returns are positive" if bullish else "short and medium returns are negative",)))
    if acceleration is not None and short * acceleration > 0:
        results.append(finding(data, "momentum_acceleration_v1", "Momentum Acceleration", FindingGroup.MOMENTUM, data.selected_start, end,
            {"short_momentum": short, "acceleration": acceleration}, {"zero_acceleration": Decimal("0")}, ("momentum and acceleration have the same sign",)))
    if exhaustion is not None and exhaustion > Decimal("0.00001"):
        results.append(finding(data, "momentum_exhaustion_v1", "Momentum Exhaustion", FindingGroup.MOMENTUM, data.selected_start, end,
            {"exhaustion_proxy": exhaustion, "short_momentum": short}, {"minimum_exhaustion_proxy": Decimal("0.00001")}, ("non-zero momentum is decelerating",)))
    selected_return = (data.candles[end].close / data.candles[data.selected_start].close) - 1
    if recovery is not None and selected_return >= Decimal("0.02"):
        results.append(finding(data, "rapid_recovery_v1", "Rapid Recovery", FindingGroup.MOMENTUM, data.selected_start, end,
            {"selected_return": selected_return, "distance_from_recent_low": recovery}, {"minimum_return": Decimal("0.02")}, ("selected return >= 2%",)))
    if selected_return <= Decimal("-0.02"):
        results.append(finding(data, "rapid_decline_v1", "Rapid Decline", FindingGroup.MOMENTUM, data.selected_start, end,
            {"selected_return": selected_return}, {"maximum_return": Decimal("-0.02")}, ("selected return <= -2%",)))
    return results