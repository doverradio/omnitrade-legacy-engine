"""Distinct, non-interchangeable identity and lineage primitives."""

from __future__ import annotations

from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _UUIDIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: UUID


class EventId(_UUIDIdentity):
    pass


class RunId(_UUIDIdentity):
    pass


class CorrelationId(_UUIDIdentity):
    pass


class CausationId(_UUIDIdentity):
    pass


class AssetId(_UUIDIdentity):
    pass


class InstrumentId(_UUIDIdentity):
    pass


class PackageId(_UUIDIdentity):
    pass


class ExecutionClaimId(_UUIDIdentity):
    pass


class ProofId(_UUIDIdentity):
    pass


class LiveOrderId(_UUIDIdentity):
    pass


class ReconciliationId(_UUIDIdentity):
    pass


class AccountingId(_UUIDIdentity):
    pass


class CampaignId(_UUIDIdentity):
    pass


class MandateId(_UUIDIdentity):
    pass


class MandateVersionId(_UUIDIdentity):
    pass


class PortfolioId(_UUIDIdentity):
    pass


class ProviderOrderId(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: NonEmptyText


class ProviderFillId(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: NonEmptyText


class LineageAuthority(str, Enum):
    VERIFIED = "VERIFIED"
    SYNTHETIC = "SYNTHETIC"
    ABSENT = "ABSENT"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"


class LineageKind(str, Enum):
    EVENT = "EVENT"
    RUN = "RUN"
    ASSET = "ASSET"
    INSTRUMENT = "INSTRUMENT"
    PROVIDER_ORDER = "PROVIDER_ORDER"
    PACKAGE = "PACKAGE"
    EXECUTION_CLAIM = "EXECUTION_CLAIM"
    PROOF = "PROOF"
    RECONCILIATION = "RECONCILIATION"
    ACCOUNTING = "ACCOUNTING"
    RISK = "RISK"
    APPROVAL = "APPROVAL"


class LineageReference(BaseModel):
    """A reference plus an explicit statement about its evidentiary authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: LineageKind
    value: NonEmptyText | None = None
    authority: LineageAuthority

    @model_validator(mode="after")
    def validate_presence(self) -> "LineageReference":
        if self.authority is LineageAuthority.ABSENT and self.value is not None:
            raise ValueError("ABSENT lineage cannot contain an identifier")
        if self.authority is not LineageAuthority.ABSENT and self.value is None:
            raise ValueError("non-ABSENT lineage requires the observed identifier")
        return self


class AssetIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    asset_id: AssetId
    symbol: NonEmptyText | None = None


class InstrumentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instrument_id: InstrumentId
    canonical_symbol: NonEmptyText | None = None


IdentityReference = Annotated[
    EventId
    | RunId
    | CorrelationId
    | CausationId
    | AssetId
    | InstrumentId
    | PackageId
    | ExecutionClaimId
    | ProofId
    | LiveOrderId
    | ReconciliationId
    | AccountingId
    | CampaignId
    | MandateId
    | MandateVersionId
    | PortfolioId
    | ProviderOrderId
    | ProviderFillId,
    Field(discriminator=None),
]
