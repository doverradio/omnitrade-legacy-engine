from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.pipeline_contracts.identifiers import (
    CausationId,
    EventId,
    LineageAuthority,
    LineageKind,
    LineageReference,
    LiveOrderId,
    MandateId,
    MandateVersionId,
    PackageId,
    ProviderOrderId,
    ProviderFillId,
    RunId,
)


VALUE = UUID("11111111-1111-4111-8111-111111111111")


def test_identifier_models_are_distinct_and_forbid_silent_interchange() -> None:
    class RequiresRun(BaseModel):
        model_config = ConfigDict(extra="forbid")
        run_id: RunId

    event_id = EventId(value=VALUE)
    assert EventId(value=VALUE) != RunId(value=VALUE)
    with pytest.raises(ValidationError):
        RequiresRun(run_id=event_id)
    assert RequiresRun(run_id={"value": str(VALUE)}).run_id == RunId(value=VALUE)


def test_provider_order_and_internal_package_identity_are_not_interchangeable() -> None:
    assert ProviderOrderId(value="KRAKEN-ORDER-1").value == "KRAKEN-ORDER-1"
    with pytest.raises(ValidationError):
        PackageId.model_validate({"value": "KRAKEN-ORDER-1"})


def test_mandate_live_order_and_provider_fill_identity_families_are_distinct() -> None:
    class RequiresMandateVersion(BaseModel):
        mandate_version_id: MandateVersionId

    mandate = MandateId(value=VALUE)
    assert mandate != MandateVersionId(value=VALUE)
    assert LiveOrderId(value=VALUE) != MandateId(value=VALUE)
    assert ProviderFillId(value="fill-1") != ProviderOrderId(value="fill-1")
    with pytest.raises(ValidationError):
        RequiresMandateVersion(mandate_version_id=mandate)


def test_lineage_authorities_remain_distinguishable() -> None:
    refs = {
        authority: LineageReference(
            kind=LineageKind.RISK,
            value=None if authority is LineageAuthority.ABSENT else "observed-risk-id",
            authority=authority,
        )
        for authority in LineageAuthority
    }
    assert set(refs) == {
        LineageAuthority.VERIFIED,
        LineageAuthority.SYNTHETIC,
        LineageAuthority.ABSENT,
        LineageAuthority.LEGACY_UNVERIFIED,
    }
    assert refs[LineageAuthority.SYNTHETIC].authority is not LineageAuthority.VERIFIED
    assert refs[LineageAuthority.ABSENT].value is None


def test_missing_lineage_is_not_silently_replaced() -> None:
    absent = LineageReference(kind=LineageKind.APPROVAL, authority=LineageAuthority.ABSENT)
    assert absent.value is None
    with pytest.raises(ValidationError, match="requires the observed identifier"):
        LineageReference(kind=LineageKind.APPROVAL, authority=LineageAuthority.SYNTHETIC)
    with pytest.raises(ValidationError, match="cannot contain"):
        LineageReference(kind=LineageKind.APPROVAL, value="invented", authority=LineageAuthority.ABSENT)


def test_causation_identity_is_not_a_run_or_event_identity() -> None:
    assert CausationId(value=VALUE) != RunId(value=VALUE)
    assert CausationId(value=VALUE) != EventId(value=VALUE)
