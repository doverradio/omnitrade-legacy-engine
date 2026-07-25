from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID


class RunMode(str, Enum):
    """Which execution context a piece of code is running under.

    Distinct from EvidenceClass: RunMode describes the *context* a decision
    was made in; EvidenceClass describes what the *resulting evidence* is
    permitted to be used for. The two are usually the same value but are
    kept as separate enums because a single run mode could in principle
    produce evidence of more than one class (e.g. a forward-paper run
    producing both FORWARD_PAPER and, incidentally, UNIT_TEST fixtures).
    """

    PRODUCTION_LIVE = "PRODUCTION_LIVE"
    FORWARD_PAPER = "FORWARD_PAPER"
    HISTORICAL_SIMULATION = "HISTORICAL_SIMULATION"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    UNIT_TEST = "UNIT_TEST"


class EvidenceClass(str, Enum):
    """What a piece of persisted evidence is (and is not) permitted to be
    used as, downstream. Never inferred from context -- always carried
    explicitly on EvidenceContext so a consumer can fail closed on a
    class it doesn't recognize rather than guessing from run_mode."""

    PRODUCTION_LIVE = "PRODUCTION_LIVE"
    FORWARD_PAPER = "FORWARD_PAPER"
    HISTORICAL_POINT_IN_TIME = "HISTORICAL_POINT_IN_TIME"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    UNIT_TEST = "UNIT_TEST"


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Minimum canonical provenance needed to identify where a piece of
    evidence came from and what it may be used for.

    Deliberately NOT persisted onto any production table in this phase
    (see ADR-0014) -- this is the shared, in-memory contract that future
    simulation/replay code will attach to synthetic records; production
    decision tables are untouched here.
    """

    run_mode: RunMode
    evidence_class: EvidenceClass
    run_id: UUID | None = None
    dataset_id: str | None = None
    dataset_version: str | None = None
    knowledge_cutoff_at: datetime | None = None
    random_seed: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
