from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.services.historical_simulation.run_mode import EvidenceClass, EvidenceContext, RunMode


def test_run_mode_has_required_values() -> None:
    required = {"PRODUCTION_LIVE", "FORWARD_PAPER", "HISTORICAL_SIMULATION", "COUNTERFACTUAL", "UNIT_TEST"}
    assert required.issubset({member.value for member in RunMode})


def test_evidence_class_has_required_values() -> None:
    required = {"PRODUCTION_LIVE", "FORWARD_PAPER", "HISTORICAL_POINT_IN_TIME", "COUNTERFACTUAL", "UNIT_TEST"}
    assert required.issubset({member.value for member in EvidenceClass})


def test_evidence_context_minimal_construction_defaults_optional_fields_to_none() -> None:
    context = EvidenceContext(run_mode=RunMode.UNIT_TEST, evidence_class=EvidenceClass.UNIT_TEST)
    assert context.run_id is None
    assert context.dataset_id is None
    assert context.dataset_version is None
    assert context.knowledge_cutoff_at is None
    assert context.random_seed is None
    assert context.created_at.tzinfo is not None


def test_evidence_context_carries_full_provenance() -> None:
    run_id = uuid4()
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = EvidenceContext(
        run_mode=RunMode.HISTORICAL_SIMULATION,
        evidence_class=EvidenceClass.HISTORICAL_POINT_IN_TIME,
        run_id=run_id,
        dataset_id="btc-usd-15m",
        dataset_version="v1",
        knowledge_cutoff_at=cutoff,
        random_seed=42,
    )
    assert context.run_id == run_id
    assert context.dataset_id == "btc-usd-15m"
    assert context.dataset_version == "v1"
    assert context.knowledge_cutoff_at == cutoff
    assert context.random_seed == 42


def test_evidence_context_is_immutable() -> None:
    context = EvidenceContext(run_mode=RunMode.UNIT_TEST, evidence_class=EvidenceClass.UNIT_TEST)
    with pytest.raises(FrozenInstanceError):
        context.run_id = uuid4()  # type: ignore[misc]
