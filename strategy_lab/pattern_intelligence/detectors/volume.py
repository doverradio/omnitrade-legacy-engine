from __future__ import annotations

from decimal import Decimal

from ..models import DetectorInput, Finding, FindingGroup
from .common import finding, value

DETECTOR_VERSION = "1.0.0"


def detect(data: DetectorInput) -> list[Finding]:
    end = data.selected_end
    ratio = value(data, "volume_ratio", end)
    candle_return = value(data, "candle_return", end)
    if ratio is None or candle_return is None:
        return []
    results: list[Finding] = []
    if ratio >= Decimal("1.25"):
        results.append(finding(data, "volume_expansion_v1", "Volume Expansion", FindingGroup.VOLUME, data.selected_start, end,
            {"volume_ratio": ratio}, {"minimum_volume_ratio": Decimal("1.25")}, ("current volume >= 125% of rolling mean",)))
    if ratio <= Decimal("0.75"):
        results.append(finding(data, "volume_contraction_v1", "Volume Contraction", FindingGroup.VOLUME, data.selected_start, end,
            {"volume_ratio": ratio}, {"maximum_volume_ratio": Decimal("0.75")}, ("current volume <= 75% of rolling mean",)))
    if ratio >= Decimal("1.10") and candle_return != 0:
        bullish = candle_return > 0
        results.append(finding(data, "bullish_price_volume_confirmation_v1" if bullish else "bearish_price_volume_confirmation_v1",
            "Bullish Price-Volume Confirmation" if bullish else "Bearish Price-Volume Confirmation", FindingGroup.VOLUME, data.selected_start, end,
            {"candle_return": candle_return, "volume_ratio": ratio}, {"minimum_volume_ratio": Decimal("1.10")},
            ("price direction is accompanied by above-average volume",)))
    medium = value(data, "medium_momentum", end)
    volume_change = value(data, "volume_expansion", end)
    if medium is not None and volume_change is not None and medium * volume_change < 0:
        results.append(finding(data, "price_volume_divergence_v1", "Price-Volume Divergence", FindingGroup.VOLUME, data.selected_start, end,
            {"medium_momentum": medium, "volume_change": volume_change}, {"opposite_sign_product_below": Decimal("0")},
            ("price momentum and relative volume change have opposite signs",)))
    return results