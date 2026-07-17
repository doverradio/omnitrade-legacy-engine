from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignAuthorityMetadata,
    CommissionedCampaignEvidenceMetadata,
    CommissionedCampaignTransitionRequest,
)
from app.services.capital_campaign_domain.commissioned_state_machine import (
    transition_commissioned_campaign_state,
    validate_commissioned_state_transition,
)


class _FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._rows)


class _FakeDb:
    def __init__(self, definition: CapitalCampaignDefinition, runtime: CapitalCampaign | None) -> None:
        self.definition = definition
        self.runtime = runtime
        self.audit_rows: list[AuditLog] = []
        self.commit_calls = 0

    def add(self, obj) -> None:
        if isinstance(obj, AuditLog):
            self.audit_rows.append(obj)

    async def scalar(self, statement):
        text = str(statement)
        if "capital_campaign_definitions" in text:
            return self.definition
        if "capital_campaigns" in text:
            return self.runtime
        return None

    async def execute(self, statement):
        text = str(statement)
        if "audit_logs" in text:
            return _FakeExecuteResult(self.audit_rows)
        return _FakeExecuteResult([])

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commit_calls += 1


def _definition(*, status: str = "DRAFT", metadata_evidence: dict | None = None) -> CapitalCampaignDefinition:
    now = datetime.now(timezone.utc)
    return CapitalCampaignDefinition(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        name="Commissioned Campaign",
        owner_identity="operator",
        status=status,
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        base_currency="USD",
        allowed_asset_classes=["crypto"],
        allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD"],
        campaign_modes=["OPPORTUNITY_SEEKING"],
        maximum_open_positions=1,
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("10"),
        profitability_policy_id="pfp-1.1",
        profitability_policy_version="1.0.0",
        risk_policy_id="risk-v1",
        risk_policy_version="1.0.0",
        compounding_policy={"policy_type": "REINVEST_PERCENTAGE"},
        profit_distribution_policy={"reinvestment_percentage": "50"},
        aggression_mode="BALANCED",
        initial_capital=Decimal("25"),
        allocated_capital=Decimal("0"),
        reserved_capital=Decimal("0"),
        deployed_capital=Decimal("0"),
        realized_gross_pnl=Decimal("0"),
        fees=Decimal("0"),
        realized_net_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        distributable_profit=Decimal("0"),
        compounded_profit=Decimal("0"),
        withdrawn_profit=Decimal("0"),
        current_campaign_equity=Decimal("25"),
        maximum_drawdown=Decimal("0"),
        available_capital=Decimal("25"),
        activated_at=None,
        paused_at=None,
        completed_at=None,
        metadata_evidence=metadata_evidence or {},
        created_at=now,
        updated_at=now,
    )


def _runtime(*, status: str = "DRAFT") -> CapitalCampaign:
    now = datetime.now(timezone.utc)
    return CapitalCampaign(
        id=1,
        uuid=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        owner="operator",
        name="Commissioned Campaign",
        description=None,
        status=status,
        campaign_type="definition_pinned_runtime",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        definition_campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        definition_version=1,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_legal_transitions_and_metadata_persistence() -> None:
    definition = _definition()
    runtime = _runtime()
    db = _FakeDb(definition=definition, runtime=runtime)

    authority = CommissionedCampaignAuthorityMetadata(
        maximum_entry_notional=Decimal("25"),
        commissioned_by="operator",
        commissioned_at=datetime.now(timezone.utc),
    )
    evidence = [
        CommissionedCampaignEvidenceMetadata(
            evidence_code="entry_gate_ready",
            source="unit_test",
            observed_at=datetime.now(timezone.utc),
            payload={"ok": True},
        )
    ]

    sequence = [
        "READY",
        "COMMISSIONED",
        "BUY_PENDING",
        "BUY_SUBMITTED",
        "BUY_RECONCILIATION_PENDING",
        "ACTIVE_POSITION",
        "SELL_EVALUATION",
        "SELL_PENDING",
        "SELL_SUBMITTED",
        "SELL_RECONCILIATION_PENDING",
        "COMPLETED",
    ]

    previous = "DRAFT"
    for idx, state in enumerate(sequence):
        response = await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state=state,
                actor="operator",
                reason=f"step-{idx}",
                authority_metadata=authority if idx == 1 else None,
                evidence_metadata=evidence if idx in {1, 3, 9} else [],
                expected_current_state=previous,
            ),
        )
        assert response.current_state == state
        assert response.previous_state == previous
        previous = state

    blob = definition.metadata_evidence["commissioned_seed_campaign"]
    assert blob["state"] == "COMPLETED"
    assert len(blob["transition_history"]) == len(sequence)
    assert blob["authority_metadata"]["commissioned_by"] == "operator"
    assert len(blob["evidence_metadata"]) == 3
    assert definition.status == "COMPLETED"
    assert runtime.status == "COMPLETED"
    assert len(db.audit_rows) == len(sequence)


@pytest.mark.asyncio
async def test_illegal_transition_rejected() -> None:
    definition = _definition()
    runtime = _runtime()
    db = _FakeDb(definition=definition, runtime=runtime)

    with pytest.raises(InvalidRequestError):
        await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state="ACTIVE_POSITION",
                actor="operator",
                reason="skip states",
            ),
        )


@pytest.mark.asyncio
async def test_terminal_protection_rejects_transition() -> None:
    definition = _definition(
        status="COMPLETED",
        metadata_evidence={
            "commissioned_seed_campaign": {
                "state": "COMPLETED",
                "authority_metadata": None,
                "evidence_metadata": [],
                "transition_history": [],
                "seen_idempotency_keys": {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    runtime = _runtime(status="COMPLETED")
    db = _FakeDb(definition=definition, runtime=runtime)

    with pytest.raises(InvalidRequestError):
        await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state="SELL_EVALUATION",
                actor="operator",
                reason="should fail",
            ),
        )


@pytest.mark.asyncio
async def test_deterministic_replay_by_idempotency_key() -> None:
    definition = _definition()
    runtime = _runtime()
    db = _FakeDb(definition=definition, runtime=runtime)

    first = await transition_commissioned_campaign_state(
        db=db,
        campaign_id=definition.campaign_id,
        version=1,
        request=CommissionedCampaignTransitionRequest(
            target_state="READY",
            actor="operator",
            reason="ready",
            idempotency_key="key-1",
        ),
    )
    second = await transition_commissioned_campaign_state(
        db=db,
        campaign_id=definition.campaign_id,
        version=1,
        request=CommissionedCampaignTransitionRequest(
            target_state="READY",
            actor="operator",
            reason="ready",
            idempotency_key="key-1",
        ),
    )

    assert first.replayed is False
    assert second.replayed is True
    assert second.previous_state == "DRAFT"
    assert second.current_state == "READY"
    assert second.transition_count == 1
    assert len(definition.metadata_evidence["commissioned_seed_campaign"]["transition_history"]) == 1


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_intent_fails_closed() -> None:
    definition = _definition()
    runtime = _runtime()
    db = _FakeDb(definition=definition, runtime=runtime)

    await transition_commissioned_campaign_state(
        db=db,
        campaign_id=definition.campaign_id,
        version=1,
        request=CommissionedCampaignTransitionRequest(
            target_state="READY",
            actor="operator",
            reason="first-intent",
            idempotency_key="key-1",
        ),
    )

    with pytest.raises(InvalidRequestError):
        await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state="READY",
                actor="operator",
                reason="different-intent",
                idempotency_key="key-1",
            ),
        )


@pytest.mark.asyncio
async def test_duplicate_transition_without_idempotency_rejected() -> None:
    definition = _definition()
    runtime = _runtime()
    db = _FakeDb(definition=definition, runtime=runtime)

    await transition_commissioned_campaign_state(
        db=db,
        campaign_id=definition.campaign_id,
        version=1,
        request=CommissionedCampaignTransitionRequest(
            target_state="READY",
            actor="operator",
            reason="first",
        ),
    )

    with pytest.raises(InvalidRequestError):
        await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state="READY",
                actor="operator",
                reason="duplicate",
            ),
        )


@pytest.mark.asyncio
async def test_authority_metadata_cannot_be_silently_overwritten() -> None:
    definition = _definition()
    runtime = _runtime()
    db = _FakeDb(definition=definition, runtime=runtime)

    original_authority = CommissionedCampaignAuthorityMetadata(
        maximum_entry_notional=Decimal("25"),
        commissioned_by="operator",
    )
    conflicting_authority = CommissionedCampaignAuthorityMetadata(
        maximum_entry_notional=Decimal("10"),
        commissioned_by="operator",
    )

    await transition_commissioned_campaign_state(
        db=db,
        campaign_id=definition.campaign_id,
        version=1,
        request=CommissionedCampaignTransitionRequest(
            target_state="READY",
            actor="operator",
            reason="ready",
            authority_metadata=original_authority,
        ),
    )

    with pytest.raises(InvalidRequestError):
        await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state="COMMISSIONED",
                actor="operator",
                reason="commission",
                authority_metadata=conflicting_authority,
            ),
        )


@pytest.mark.asyncio
async def test_inconsistent_runtime_or_definition_mapping_fails_closed() -> None:
    definition = _definition(status="READY")
    runtime = _runtime(status="DRAFT")
    db = _FakeDb(definition=definition, runtime=runtime)

    with pytest.raises(InvalidRequestError):
        await transition_commissioned_campaign_state(
            db=db,
            campaign_id=definition.campaign_id,
            version=1,
            request=CommissionedCampaignTransitionRequest(
                target_state="READY",
                actor="operator",
                reason="should-fail",
            ),
        )


def test_validate_transition_helper() -> None:
    validate_commissioned_state_transition(current_state="READY", target_state="COMMISSIONED")

    with pytest.raises(InvalidRequestError):
        validate_commissioned_state_transition(current_state="READY", target_state="ACTIVE_POSITION")
