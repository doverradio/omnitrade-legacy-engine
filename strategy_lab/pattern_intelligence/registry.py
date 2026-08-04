from __future__ import annotations

from collections.abc import Callable

from .detectors import breakouts, momentum, price_structure, strategy_failures, volatility, volume
from .models import DetectorInput, Finding

Detector = Callable[[DetectorInput], list[Finding]]

DETECTORS: tuple[tuple[str, str, Detector], ...] = (
    ("price_structure", price_structure.DETECTOR_VERSION, price_structure.detect),
    ("volatility", volatility.DETECTOR_VERSION, volatility.detect),
    ("momentum", momentum.DETECTOR_VERSION, momentum.detect),
    ("volume", volume.DETECTOR_VERSION, volume.detect),
    ("breakouts", breakouts.DETECTOR_VERSION, breakouts.detect),
    ("strategy_failures", strategy_failures.DETECTOR_VERSION, strategy_failures.detect),
)


def detector_versions() -> dict[str, str]:
    return {name: version for name, version, _ in DETECTORS}