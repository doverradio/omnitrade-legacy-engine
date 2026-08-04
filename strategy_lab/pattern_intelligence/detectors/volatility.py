from __future__ import annotations

from decimal import Decimal

from ..models import DetectorInput, Finding, FindingGroup
from .common import finding, value

DETECTOR_VERSION = "1.0.0"


def detect(data: DetectorInput) -> list[Finding]:
    end = data.selected_end
    ratio = value(data, "range_ratio", end)
    percentile = value(data, "volatility_percentile", end)
    normalized = value(data, "normalized_range", end)
    if ratio is None:
        return []
    results: list[Finding] = []
    contraction = ratio <= data.config.contraction_ratio
    expansion = ratio >= data.config.expansion_ratio
    if contraction:
        measurements = {"range_ratio": ratio, "range_contraction_pct": (Decimal("1") - ratio) * 100, "rolling_range": normalized}
        for detector_id, name in (("volatility_contraction_v1", "Volatility Contraction"), ("compressed_range_v1", "Compressed Range")):
            results.append(finding(data, detector_id, name, FindingGroup.VOLATILITY, data.selected_start, end, measurements,
                {"maximum_range_ratio": data.config.contraction_ratio}, (f"range_ratio <= {data.config.contraction_ratio}",)))
    if expansion:
        measurements = {"range_ratio": ratio, "range_expansion_pct": (ratio - Decimal("1")) * 100, "rolling_range": normalized}
        for detector_id, name in (("volatility_expansion_v1", "Volatility Expansion"), ("expanding_range_v1", "Expanding Range")):
            results.append(finding(data, detector_id, name, FindingGroup.VOLATILITY, data.selected_start, end, measurements,
                {"minimum_range_ratio": data.config.expansion_ratio}, (f"range_ratio >= {data.config.expansion_ratio}",)))
    if percentile is not None and percentile <= Decimal("0.20"):
        results.append(finding(data, "low_volatility_regime_v1", "Low-Volatility Regime", FindingGroup.VOLATILITY, data.selected_start, end,
            {"volatility_percentile": percentile}, {"maximum_percentile": Decimal("0.20")}, ("rolling return volatility is in the lowest 20% of its baseline",)))
    if percentile is not None and percentile >= Decimal("0.80"):
        results.append(finding(data, "high_volatility_regime_v1", "High-Volatility Regime", FindingGroup.VOLATILITY, data.selected_start, end,
            {"volatility_percentile": percentile}, {"minimum_percentile": Decimal("0.80")}, ("rolling return volatility is in the highest 20% of its baseline",)))
    return results