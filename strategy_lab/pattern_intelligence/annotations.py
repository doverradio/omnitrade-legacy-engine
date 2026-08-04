from __future__ import annotations

from .models import ChartAnnotation, Finding


def annotations_for(findings: list[Finding]) -> tuple[ChartAnnotation, ...]:
    return tuple(ChartAnnotation(
        annotation_id=f"ann_{index:04d}", pattern_id=finding.detector_id,
        start_time=finding.start_time, end_time=finding.end_time,
        label=finding.pattern_name,
        chart_region="volume" if finding.group.value == "Volume" else "price",
        details_ref=finding.finding_id,
    ) for index, finding in enumerate(findings, start=1))