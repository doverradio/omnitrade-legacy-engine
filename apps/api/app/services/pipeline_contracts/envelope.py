"""Minimal canonical metadata envelope; it contains no business payload."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from app.services.pipeline_contracts.identifiers import CausationId, CorrelationId, EventId, RunId
from app.services.pipeline_contracts.serialization import (
    canonical_json_bytes,
    integrity_sha256_excluding_root_integrity_hash,
)


CANONICAL_ENVELOPE_SCHEMA_VERSION = "canonical-envelope/v1"
SUPPORTED_CANONICAL_ENVELOPE_SCHEMA_VERSIONS = frozenset({CANONICAL_ENVELOPE_SCHEMA_VERSION})
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class QualityStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class CanonicalEnvelopeV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: EventId
    event_type: NonEmptyText
    schema_version: Literal[CANONICAL_ENVELOPE_SCHEMA_VERSION]
    source: NonEmptyText
    occurred_at: datetime
    available_at: datetime | None = None
    received_at: datetime | None = None
    correlation_id: CorrelationId | None = None
    causation_id: CausationId | None = None
    run_id: RunId | None = None
    stage_version: NonEmptyText
    quality_status: QualityStatus | None = None
    integrity_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None

    @field_validator("occurred_at", "available_at", "received_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("canonical envelope datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_integrity_hash(self) -> "CanonicalEnvelopeV1":
        if self.integrity_hash is not None and self.integrity_hash != self.computed_integrity_hash():
            raise ValueError("integrity_hash does not match canonical envelope content")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def computed_integrity_hash(self) -> str:
        return integrity_sha256_excluding_root_integrity_hash(self)

    def with_computed_integrity_hash(self) -> "CanonicalEnvelopeV1":
        values = self.model_dump(mode="python", exclude_none=False)
        values["integrity_hash"] = self.computed_integrity_hash()
        return CanonicalEnvelopeV1.model_validate(values)
