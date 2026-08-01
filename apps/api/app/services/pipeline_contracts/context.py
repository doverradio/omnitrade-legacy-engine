"""Execution context is descriptive metadata, never an authority grant."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from collections.abc import Mapping
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

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
    """Immutable version namespaces.

    Omitted and explicitly empty namespaces both normalize to ``()`` because
    they have the same meaning: no version was supplied for that namespace.
    Non-empty mappings normalize to immutable key-sorted pairs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_versions: tuple[tuple[NonEmptyText, NonEmptyText], ...] = ()
    configuration_versions: tuple[tuple[NonEmptyText, NonEmptyText], ...] = ()
    policy_versions: tuple[tuple[NonEmptyText, NonEmptyText], ...] = ()

    @field_validator("schema_versions", "configuration_versions", "policy_versions", mode="before")
    @classmethod
    def normalize_version_namespace(cls, value: object) -> object:
        if value is None:
            raise ValueError("version namespaces cannot be null")
        entries = value.items() if isinstance(value, Mapping) else value
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            for entry in entries:  # type: ignore[union-attr]
                key, version = entry
                if not isinstance(key, str) or not isinstance(version, str):
                    raise ValueError("version namespace keys and values must be strings")
                key = key.strip()
                version = version.strip()
                if not key or not version:
                    raise ValueError("version namespace keys and values must be non-empty")
                if key in seen:
                    raise ValueError("version namespace keys must be unique")
                seen.add(key)
                normalized.append((key, version))
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("version namespace"):
                raise
            raise ValueError("version namespace must be a mapping or key/value pairs") from exc
        return tuple(sorted(normalized))


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
