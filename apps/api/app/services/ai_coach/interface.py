from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass(frozen=True, slots=True)
class AICoachObservation:
    observation_id: uuid.UUID
    evaluation_timestamp: datetime
    summary: str
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    confidence_note: str
    reproducibility_note: str
    suggested_follow_up: str
