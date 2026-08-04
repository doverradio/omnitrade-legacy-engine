from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from ..models import DetectorInput, Finding, FindingCategory, FindingGroup


def finding(
    data: DetectorInput,
    detector_id: str,
    pattern_name: str,
    group: FindingGroup,
    start: int,
    end: int,
    measurements: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    conditions: tuple[str, ...],
    category: FindingCategory = FindingCategory.OBSERVATION,
    sufficient: bool = True,
) -> Finding:
    evidence = tuple(f"{key}={value}" for key, value in sorted(measurements.items()))
    return Finding(
        finding_id=f"{detector_id}:{start}:{end}",
        detector_id=detector_id,
        detector_version="1.0.0",
        category=category,
        group=group,
        pattern_name=pattern_name,
        start_index=start,
        end_index=end,
        start_time=data.candles[start].timestamp,
        end_time=data.candles[end].timestamp,
        measurements=measurements,
        thresholds=thresholds,
        evidence=evidence,
        conditions=conditions,
        sufficient_evidence=sufficient,
    )


def value(data: DetectorInput, name: str, index: int) -> Decimal | None:
    return data.features.at(name, index)


def sequence_direction(values: list[Decimal], direction: str) -> bool:
    if direction == "higher":
        return all(current > previous for previous, current in zip(values, values[1:]))
    return all(current < previous for previous, current in zip(values, values[1:]))


def average(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)