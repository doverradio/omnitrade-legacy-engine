"""Execution context is descriptive metadata, never an authority grant."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.services.pipeline_contracts.identifiers import (
    CampaignId,
    CausationId,
    CorrelationId,
    PortfolioId,
    RunId,
)
from app.services.pipeline_contracts.serialization import canonical_json_bytes


EXECUTION_CONTEXT_SCHEMA_VERSION = "execution-context/v1"
SUPPORTED_EXECUTION_CONTEXT_SCHEMA_VERSIONS = frozenset({EXECUTION_CONTEXT_SCHEMA_VERSION})
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class OperatingMode(str, Enum):
    LIVE = "LIVE"
    CONTROLLED_PROOF = "CONTROLLED_PROOF"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    SIMULATION = "SIMULATION"
    UNIT_TEST = "UNIT_TEST"


class Clock(Protocol):
    def now(self) -> datetime: ...


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._value = value

    def now(self) -> datetime:
        return self._value


class VersionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_versions: dict[NonEmptyText, NonEmptyText] = Field(default_factory=dict)
    configuration_versions: dict[NonEmptyText, NonEmptyText] = Field(default_factory=dict)
    policy_versions: dict[NonEmptyText, NonEmptyText] = Field(default_factory=dict)


class ExecutionContextV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Annotated[str, StringConstraints(pattern=r"^execution-context/v1$")]
    mode: OperatingMode
    run_id: RunId
    pipeline_version: NonEmptyText
    version_manifest: VersionManifest
    effective_at: datetime
    correlation_id: CorrelationId | None = None
    causation_id: CausationId | None = None
    operator_identity_ref: NonEmptyText | None = None
    campaign_identity_ref: CampaignId | None = None
    portfolio_identity_ref: PortfolioId | None = None

    @field_validator("effective_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Execution Context effective_at must be timezone-aware")
        return value

    @classmethod
    def from_clock(
        cls,
        *,
        clock: Clock,
        mode: OperatingMode,
        run_id: RunId,
        pipeline_version: str,
        version_manifest: VersionManifest,
        correlation_id: CorrelationId | None = None,
        causation_id: CausationId | None = None,
        operator_identity_ref: str | None = None,
        campaign_identity_ref: CampaignId | None = None,
        portfolio_identity_ref: PortfolioId | None = None,
    ) -> "ExecutionContextV1":
        return cls(
            schema_version=EXECUTION_CONTEXT_SCHEMA_VERSION,
            mode=mode,
            run_id=run_id,
            pipeline_version=pipeline_version,
            version_manifest=version_manifest,
            effective_at=clock.now(),
            correlation_id=correlation_id,
            causation_id=causation_id,
            operator_identity_ref=operator_identity_ref,
            campaign_identity_ref=campaign_identity_ref,
            portfolio_identity_ref=portfolio_identity_ref,
        )

    @property
    def grants_authority(self) -> bool:
        return False

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)
