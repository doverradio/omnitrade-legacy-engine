from __future__ import annotations

import builtins
import os
import socket
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.pipeline_contracts.context import (
    EXECUTION_CONTEXT_SCHEMA_VERSION,
    ExecutionContextV1,
    FixedClock,
    OperatingMode,
    VersionManifest,
)
from app.services.pipeline_contracts.identifiers import CampaignId, CorrelationId, RunId


FIXED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
RUN_ID = RunId(value=UUID("11111111-1111-4111-8111-111111111111"))


def _context(mode: OperatingMode = OperatingMode.UNIT_TEST) -> ExecutionContextV1:
    return ExecutionContextV1.from_clock(
        clock=FixedClock(FIXED_AT),
        mode=mode,
        run_id=RUN_ID,
        pipeline_version="pipeline/v1",
        version_manifest=VersionManifest(
            schema_versions={"envelope": "canonical-envelope/v1"},
            configuration_versions={"strategy": "sha256:abc"},
            policy_versions={"risk": "risk/v1"},
        ),
        correlation_id=CorrelationId(value=UUID("22222222-2222-4222-8222-222222222222")),
        campaign_identity_ref=CampaignId(value=UUID("33333333-3333-4333-8333-333333333333")),
    )


def test_injected_clock_and_mapping_construction_are_deterministic() -> None:
    first = _context()
    reordered = first.model_copy(update={
        "version_manifest": VersionManifest(
            policy_versions={"risk": "risk/v1"},
            configuration_versions={"strategy": "sha256:abc"},
            schema_versions={"envelope": "canonical-envelope/v1"},
        )
    })
    assert first.effective_at == FIXED_AT
    assert first.canonical_bytes() == reordered.canonical_bytes()


def test_all_governed_modes_are_descriptive_and_grant_no_authority() -> None:
    assert {mode.value for mode in OperatingMode} == {
        "LIVE", "CONTROLLED_PROOF", "HISTORICAL_REPLAY", "SIMULATION", "UNIT_TEST"
    }
    for mode in OperatingMode:
        context = _context(mode)
        assert context.mode is mode
        assert context.grants_authority is False


def test_context_contains_no_business_payload_and_forbids_extra_fields() -> None:
    assert "payload" not in ExecutionContextV1.model_fields
    values = _context().model_dump()
    values["payload"] = {"action": "BUY"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ExecutionContextV1.model_validate(values)


def test_context_version_is_required_and_unknown_fails_closed() -> None:
    assert _context().schema_version == EXECUTION_CONTEXT_SCHEMA_VERSION
    values = _context().model_dump()
    values.pop("schema_version")
    with pytest.raises(ValidationError):
        ExecutionContextV1.model_validate(values)
    values["schema_version"] = "execution-context/v2"
    with pytest.raises(ValidationError):
        ExecutionContextV1.model_validate(values)


def test_clock_and_context_reject_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 7, 15, 12, 0))
    values = _context().model_dump()
    values["effective_at"] = datetime(2026, 7, 15, 12, 0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        ExecutionContextV1.model_validate(values)


def test_constructing_context_has_no_environment_network_or_filesystem_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("pure contract construction attempted external access")

    monkeypatch.setattr(os, "getenv", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(builtins, "open", _forbidden)

    context = _context(OperatingMode.LIVE)
    assert context.grants_authority is False
    assert context.effective_at == FIXED_AT
