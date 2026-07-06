from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.arena.contracts import (
    ArenaAgentIdentityContract,
    ArenaCompetitionIdentityContract,
    ArenaCycleIdentityContract,
    ArenaLifecycleWriteRequest,
    ArenaProvenanceContract,
    ArenaTournamentIdentityContract,
)


def test_contracts_are_frozen_for_immutable_identities() -> None:
    contract = ArenaCompetitionIdentityContract(
        competition_identity="competition-1",
        idempotency_key="idempotency-1",
        master_account_id=uuid.uuid4(),
        paper_portfolio_id=uuid.uuid4(),
    )

    with pytest.raises(AttributeError):
        contract.competition_identity = "competition-2"


def test_hierarchy_contracts_preserve_master_to_agent_chain() -> None:
    competition_identity = "comp-identity"
    tournament = ArenaTournamentIdentityContract(
        tournament_identity="tour-identity",
        idempotency_key="tour-key",
        competition_identity=competition_identity,
        sequence_number=1,
    )
    cycle = ArenaCycleIdentityContract(
        cycle_identity="cycle-identity",
        idempotency_key="cycle-key",
        tournament_identity=tournament.tournament_identity,
        cycle_number=1,
    )
    agent = ArenaAgentIdentityContract(
        agent_identity="agent-identity",
        idempotency_key="agent-key",
        competition_identity=competition_identity,
        strategy_id="momentum",
        strategy_version="v2",
    )

    assert tournament.competition_identity == competition_identity
    assert cycle.tournament_identity == tournament.tournament_identity
    assert agent.competition_identity == competition_identity


def test_write_request_preserves_provenance_contract() -> None:
    provenance = ArenaProvenanceContract(
        source_lineage={"signals": ["sig-1"], "model_outputs": ["m-1"]},
        field_provenance={"status": {"source": "scheduler"}},
    )
    request = ArenaLifecycleWriteRequest(
        status="planned",
        config={"window": "15m"},
        provenance=provenance,
        requested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert request.provenance.source_lineage["signals"] == ["sig-1"]
    assert request.provenance.field_provenance["status"]["source"] == "scheduler"


def test_protocol_shape_supports_contract_implementors() -> None:
    fake = SimpleNamespace(
        ensure_competition=lambda *args, **kwargs: None,
        ensure_tournament=lambda *args, **kwargs: None,
        ensure_cycle=lambda *args, **kwargs: None,
        ensure_participating_agent=lambda *args, **kwargs: None,
    )

    for method_name in (
        "ensure_competition",
        "ensure_tournament",
        "ensure_cycle",
        "ensure_participating_agent",
    ):
        assert hasattr(fake, method_name)