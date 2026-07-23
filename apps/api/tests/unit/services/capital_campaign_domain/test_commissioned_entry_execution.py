from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import InvalidRequestError, ServiceUnavailableError
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignCommissionRequest,
    CommissionedEntryExecutionRequest,
    CommissionedExitRecommendationRequest,
    CommissionedOwnershipReconciliationRequest,
    CommissionedPreviewResponse,
    CommissionedReadinessRequest,
    CommissionedReadinessResponse,
)
from app.services.capital_campaign_domain import commissioned_entry_execution as cee
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationResult, evaluate_signal_risk


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1

    async def scalar(self, _statement):
        return None


class _TransitionRecorder:
    def __init__(self, definition) -> None:
        self.calls: list[str] = []
        self.definition = definition

    async def __call__(self, *, db, campaign_id, version, request):
        _ = (db, campaign_id, version)
        blob = dict(self.definition.metadata_evidence.get("commissioned_seed_campaign") or {})
        previous = str(blob.get("state") or "DRAFT")
        blob["state"] = request.target_state
        self.definition.metadata_evidence["commissioned_seed_campaign"] = blob
        self.calls.append(request.target_state)
        return SimpleNamespace(
            campaign_id=campaign_id,
            version=version,
            previous_state=previous,
            current_state=request.target_state,
            replayed=False,
            transition_count=len(self.calls),
            metadata_evidence=self.definition.metadata_evidence,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


def _definition(campaign_id, version: int, state: str = "READY"):
    return SimpleNamespace(
        campaign_id=campaign_id,
        version=version,
        status="READY",
        metadata_evidence={
            "commissioned_seed_campaign": {
                "state": state,
                "authority_metadata": {
                    "campaign_type": "COMMISSIONED_AUTONOMOUS_SEED",
                    "entry_authority": "OPERATOR_COMMISSIONED",
                    "lifecycle_authority": "OMNITRADE_AUTONOMOUS",
                    "maximum_entry_notional": "5",
                    "repeat_entry_allowed": False,
                    "commissioned_by": "operator",
                },
                "evidence_metadata": [],
                "transition_history": [],
                "seen_idempotency_keys": {},
                "updated_at": _now().isoformat(),
            }
        },
        updated_at=_now(),
    )


def _runtime(campaign_id, version: int):
    return SimpleNamespace(
        id=101,
        uuid=campaign_id,
        definition_version=version,
        status="READY",
        paper_account_id=uuid4(),
    )


def _readiness_request(campaign_id, version: int) -> CommissionedReadinessRequest:
    now = _now()
    return CommissionedReadinessRequest(
        campaign_id=campaign_id,
        version=version,
        provider="kraken_spot",
        environment="production",
        instrument="BTC-USD",
        requested_quote_amount=Decimal("5"),
        quote_currency="USD",
        idempotency_key="preview-idem",
        live_trading_profile_id=uuid4(),
        account_id=uuid4(),
        mandate_id=uuid4(),
        mandate_version_id=uuid4(),
        expected_mandate_version_number=7,
        expected_risk_policy_id="risk-v1",
        expected_risk_policy_version="1.0.0",
        approval_checkpoint_type="first_live_enablement",
        authorization_expires_at=now + timedelta(minutes=10),
        provider_capability_evidence={"supported": True, "observed_at": now.isoformat()},
        connectivity_evidence={"reachable": True, "observed_at": now.isoformat()},
        balance_evidence={"available_quote_balance": "25", "observed_at": now.isoformat()},
        market_data_evidence={"observed_at": now.isoformat(), "max_age_seconds": 120},
        price_evidence={"reference_price": "50000", "observed_at": now.isoformat(), "max_age_seconds": 120},
        minimum_order_evidence={"minimum_quote_amount": "5", "minimum_base_quantity": "0.00001", "observed_at": now.isoformat()},
        fee_slippage_evidence={"estimated_entry_fee": "0.01", "estimated_future_exit_fee": "0.01", "estimated_slippage": "0.01"},
        runtime_readiness_evidence={"ready": True, "observed_at": now.isoformat()},
        reconciliation_evidence={},
        manual_review_evidence={"required": False},
    )


def _readiness_response(campaign_id, version: int) -> CommissionedReadinessResponse:
    return CommissionedReadinessResponse(
        campaign_id=campaign_id,
        version=version,
        readiness_verdict="READY",
        blockers=[],
        warnings=[],
        checks=[],
        authority_classification="OPERATOR_COMMISSIONED",
        strategy_signal_classification="NOT_REQUIRED_FOR_COMMISSIONED_ENTRY",
        commissioned_state="READY",
        expected_entry_quantity=Decimal("0.00009"),
        applicable_capital_cap=Decimal("5"),
        estimated_entry_fee=Decimal("0.01"),
        estimated_future_exit_fee=Decimal("0.01"),
        estimated_slippage=Decimal("0.01"),
        evidence_timestamps={},
        evidence_provenance={},
        stale_after=_now() + timedelta(minutes=5),
    )


def _preview_response(campaign_id, version: int, readiness_verdict: str = "READY") -> CommissionedPreviewResponse:
    return CommissionedPreviewResponse(
        campaign_id=campaign_id,
        version=version,
        authority_classification="OPERATOR_COMMISSIONED",
        strategy_signal_classification="NOT_REQUIRED_FOR_COMMISSIONED_ENTRY",
        execution_venue={"provider": "kraken_spot", "environment": "production"},
        instrument="BTC-USD",
        proposed_quote_amount=Decimal("5"),
        estimated_base_quantity=Decimal("0.00009"),
        reference_price=Decimal("50000"),
        reference_price_timestamp=_now(),
        estimated_entry_fee=Decimal("0.01"),
        estimated_future_exit_fee=Decimal("0.01"),
        estimated_slippage=Decimal("0.01"),
        total_estimated_round_trip_costs=Decimal("0.03"),
        applicable_capital_cap=Decimal("5"),
        mandate_identity={"mandate_id": str(uuid4()), "mandate_version_id": str(uuid4()), "expected_mandate_version_number": 7},
        risk_policy_identity={"risk_policy_id": "risk-v1", "risk_policy_version": "1.0.0"},
        readiness_verdict=readiness_verdict,
        blockers=[],
        warnings=[],
        evidence_timestamps={},
        evidence_provenance={},
        preview_identity_hash="preview-hash-1",
        stale_after=_now() + timedelta(minutes=5),
        no_database_writes=True,
        no_order_submission=True,
        no_position_creation=True,
    )


def _commission_request(campaign_id, version: int, readiness_request: CommissionedReadinessRequest) -> CommissionedCampaignCommissionRequest:
    now = _now()
    return CommissionedCampaignCommissionRequest(
        campaign_id=campaign_id,
        version=version,
        actor="operator:human",
        commissioning_reason="bounded_seed_entry",
        preview_identity_hash="preview-hash-1",
        requested_quote_amount=Decimal("5"),
        idempotency_key="commission-idem-1",
        authorization_expires_at=now + timedelta(minutes=10),
        commissioned_until=now + timedelta(minutes=20),
        readiness_request=readiness_request,
    )


def _execution_request(campaign_id, version: int, readiness_request: CommissionedReadinessRequest) -> CommissionedEntryExecutionRequest:
    return CommissionedEntryExecutionRequest(
        campaign_id=campaign_id,
        version=version,
        actor="operator:human",
        idempotency_key="entry-idem-1",
        readiness_request=readiness_request,
        expected_preview_identity_hash="preview-hash-1",
        live_crypto_order_id=uuid4(),
        confirmation_challenge_id=uuid4(),
        confirmation_phrase="BUY BTC",
        submit_idempotency_token="submit-idem-1",
        risk_signal_id=uuid4(),
        paper_account_id=uuid4(),
        asset_id=uuid4(),
        requested_base_quantity=Decimal("0.00009"),
        reference_price=Decimal("50000"),
        account_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.25"),
        min_order_notional=Decimal("5"),
        qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
    )


def _ownership_request(campaign_id, version: int, live_crypto_order_id) -> CommissionedOwnershipReconciliationRequest:
    return CommissionedOwnershipReconciliationRequest(
        campaign_id=campaign_id,
        version=version,
        actor="operator:human",
        idempotency_key="ownership-reconcile-1",
        live_crypto_order_id=live_crypto_order_id,
    )


def test_commissioned_buy_risk_request_propagates_authorized_quote_amount() -> None:
    campaign_id = uuid4()
    request = _execution_request(campaign_id, 1, _readiness_request(campaign_id, 1))

    risk_request = cee._build_risk_request(request)

    assert risk_request.side == "buy"
    assert risk_request.campaign_authorized_notional == Decimal("5")


def test_commissioned_buy_risk_request_uses_campaign_authority_for_minimum_rescue() -> None:
    campaign_id = uuid4()
    request = _execution_request(campaign_id, 1, _readiness_request(campaign_id, 1)).model_copy(
        update={
            "requested_base_quantity": Decimal("0.00007642280150705764571917677358"),
            "reference_price": Decimal("65425.5"),
            "account_equity": Decimal("23.7205"),
            "max_position_size_pct": Decimal("0.10"),
        }
    )

    risk_request = cee._build_risk_request(request)
    result = evaluate_signal_risk(request=risk_request, reference_price=request.reference_price)

    assert risk_request.campaign_authorized_notional > request.account_equity * request.max_position_size_pct
    assert result.action == RiskDecisionAction.RESIZE
    assert result.reason_code == "position_sized_up_to_minimum_viable_order"
    assert result.approved_quantity * request.reference_price >= request.min_order_notional


def _exit_request(campaign_id, version: int) -> CommissionedExitRecommendationRequest:
    return CommissionedExitRecommendationRequest(
        campaign_id=campaign_id,
        version=version,
        actor="operator:human",
        idempotency_key="exit-recommendation-1",
        risk_signal_id=uuid4(),
        paper_account_id=uuid4(),
        asset_id=uuid4(),
        account_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.25"),
        min_order_notional=Decimal("5"),
        qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
    )


@pytest.mark.asyncio
async def test_commission_success_from_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="READY")
    runtime = _runtime(campaign_id, 1)
    db = _FakeDb()
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)

    response = await cee.commission_commissioned_campaign(
        db=db,
        request=_commission_request(campaign_id, 1, readiness_request),
    )

    assert response.current_state == "COMMISSIONED"
    assert response.authority_classification == "OPERATOR_COMMISSIONED"
    assert definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"]["preview_identity_hash"] == "preview-hash-1"


@pytest.mark.asyncio
async def test_commission_rejected_from_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="DRAFT")
    runtime = _runtime(campaign_id, 1)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))

    with pytest.raises(InvalidRequestError):
        await cee.commission_commissioned_campaign(
            db=_FakeDb(),
            request=_commission_request(campaign_id, 1, readiness_request),
        )


@pytest.mark.asyncio
async def test_commission_rejects_expired_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="READY")
    runtime = _runtime(campaign_id, 1)

    preview = _preview_response(campaign_id, 1)
    preview.stale_after = _now() - timedelta(seconds=1)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(preview))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))

    with pytest.raises(InvalidRequestError):
        await cee.commission_commissioned_campaign(
            db=_FakeDb(),
            request=_commission_request(campaign_id, 1, readiness_request),
        )


@pytest.mark.asyncio
async def test_commission_rejects_material_preview_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="READY")
    runtime = _runtime(campaign_id, 1)

    preview = _preview_response(campaign_id, 1)
    preview.proposed_quote_amount = Decimal("4")

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(preview))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))

    with pytest.raises(InvalidRequestError):
        await cee.commission_commissioned_campaign(
            db=_FakeDb(),
            request=_commission_request(campaign_id, 1, readiness_request),
        )


@pytest.mark.asyncio
async def test_execute_success_transitions_to_buy_reconciliation_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    runtime = _runtime(campaign_id, 1)
    db = _FakeDb()
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)
    monkeypatch.setattr(
        cee,
        "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.00009"), steps=[]),
    )
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    async def _submit(self, *, db, request):
        _ = db
        return SimpleNamespace(
            live_crypto_order=SimpleNamespace(status="ACKNOWLEDGED", provider_order_id="provider-1"),
            provider_create_order_responded=True,
            provider_reconciliation_status="PENDING",
            safe_provider_response={},
            order_submitted=True,
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _submit)

    response = await cee.execute_commissioned_entry(
        db=db,
        request=_execution_request(campaign_id, 1, readiness_request),
    )

    assert response.current_state == "BUY_RECONCILIATION_PENDING"
    assert response.no_position_ownership_created is True
    assert "ACTIVE_POSITION" not in transitions.calls


@pytest.mark.asyncio
async def test_risk_veto_blocks_provider_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)
    monkeypatch.setattr(
        cee,
        "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.REJECT, reason_code="risk_veto", approved_quantity=Decimal("0"), steps=[]),
    )
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    async def _forbidden_submit(self, *, db, request):
        _ = (db, request)
        raise AssertionError("submit must not be called on risk veto")

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _forbidden_submit)

    response = await cee.execute_commissioned_entry(
        db=_FakeDb(),
        request=_execution_request(campaign_id, 1, readiness_request),
    )

    assert response.vetoed is True
    assert response.current_state == "VETOED"


@pytest.mark.asyncio
async def test_execute_ambiguous_timeout_enters_reconciliation_required(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)
    monkeypatch.setattr(
        cee,
        "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.00009"), steps=[]),
    )
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    async def _timeout_submit(self, *, db, request):
        _ = (db, request)
        raise ServiceUnavailableError(message="timeout", details={})

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _timeout_submit)

    response = await cee.execute_commissioned_entry(
        db=_FakeDb(),
        request=_execution_request(campaign_id, 1, readiness_request),
    )

    assert response.current_state == "RECONCILIATION_REQUIRED"
    assert response.provider_submission_classification == "ambiguous_submission"


@pytest.mark.asyncio
async def test_existing_economic_key_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    definition.metadata_evidence["commissioned_seed_campaign"]["entry_execution"] = {
        "economic_idempotency_key": "different-key",
        "terminal": False,
    }
    runtime = _runtime(campaign_id, 1)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))

    with pytest.raises(InvalidRequestError):
        await cee.execute_commissioned_entry(
            db=_FakeDb(),
            request=_execution_request(campaign_id, 1, readiness_request),
        )


@pytest.mark.asyncio
async def test_repeated_execute_replays_without_second_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)
    submit_calls = {"count": 0}

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)
    monkeypatch.setattr(
        cee,
        "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.00009"), steps=[]),
    )
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    async def _submit(self, *, db, request):
        _ = (db, request)
        submit_calls["count"] += 1
        return SimpleNamespace(
            live_crypto_order=SimpleNamespace(status="ACKNOWLEDGED", provider_order_id="provider-1"),
            provider_create_order_responded=True,
            provider_reconciliation_status="PENDING",
            safe_provider_response={},
            order_submitted=True,
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _submit)

    req = _execution_request(campaign_id, 1, readiness_request)
    first = await cee.execute_commissioned_entry(db=_FakeDb(), request=req)
    second = await cee.execute_commissioned_entry(db=_FakeDb(), request=req)

    assert first.current_state == "BUY_RECONCILIATION_PENDING"
    assert second.replayed is True
    assert submit_calls["count"] == 1


@pytest.mark.asyncio
async def test_execute_resumes_from_buy_pending_without_second_pre_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    live_crypto_order_id = uuid4()
    definition = _definition(campaign_id, 1, state="BUY_PENDING")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    definition.metadata_evidence["commissioned_seed_campaign"]["entry_execution"] = {
        "economic_idempotency_key": cee._build_economic_idempotency_key(
            request=_execution_request(campaign_id, 1, readiness_request),
            commissioning_identity="commissioning-1",
        ),
        "risk_event_id": str(uuid4()),
        "risk_action": "approve",
        "decision_record_id": str(uuid4()),
        "live_crypto_order_id": str(live_crypto_order_id),
        "terminal": False,
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)
    submit_calls = {"count": 0}

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("readiness must not rerun on BUY_PENDING resume")))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("preview must not rerun on BUY_PENDING resume")))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)

    async def _submit(self, *, db, request):
        _ = (db, request)
        submit_calls["count"] += 1
        return SimpleNamespace(
            live_crypto_order=SimpleNamespace(status="ACKNOWLEDGED", provider_order_id="provider-1"),
            provider_create_order_responded=True,
            provider_reconciliation_status="PENDING",
            safe_provider_response={},
            order_submitted=True,
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _submit)

    request = _execution_request(campaign_id, 1, readiness_request)
    request = request.model_copy(update={"live_crypto_order_id": live_crypto_order_id})
    response = await cee.execute_commissioned_entry(db=_FakeDb(), request=request)

    assert response.current_state == "BUY_RECONCILIATION_PENDING"
    assert response.risk_action == "approve"
    assert submit_calls["count"] == 1
    assert "BUY_SUBMITTED" in transitions.calls


@pytest.mark.asyncio
async def test_concurrent_execute_attempts_submit_once(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)
    submit_calls = {"count": 0}

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)
    monkeypatch.setattr(
        cee,
        "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.00009"), steps=[]),
    )
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    async def _submit(self, *, db, request):
        _ = (db, request)
        submit_calls["count"] += 1
        await asyncio.sleep(0.05)
        return SimpleNamespace(
            live_crypto_order=SimpleNamespace(status="ACKNOWLEDGED", provider_order_id="provider-1"),
            provider_create_order_responded=True,
            provider_reconciliation_status="PENDING",
            safe_provider_response={},
            order_submitted=True,
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _submit)

    req = _execution_request(campaign_id, 1, readiness_request)
    await asyncio.gather(
        cee.execute_commissioned_entry(db=_FakeDb(), request=req),
        cee.execute_commissioned_entry(db=_FakeDb(), request=req),
    )

    assert submit_calls["count"] == 1


@pytest.mark.asyncio
async def test_provider_adapter_not_called_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    # The commissioned campaign layer should not directly invoke provider adapters.
    # If it does, this patch would raise.
    monkeypatch.setattr(
        "app.services.exchange_connections.providers.registry.get_exchange_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider adapter must not be called directly")),
    )

    campaign_id = uuid4()
    readiness_request = _readiness_request(campaign_id, 1)
    definition = _definition(campaign_id, 1, state="COMMISSIONED")
    definition.metadata_evidence["commissioned_seed_campaign"]["commissioning"] = {
        "commissioning_identity": "commissioning-1",
        "preview_identity_hash": "preview-hash-1",
        "commissioned_until": (_now() + timedelta(minutes=10)).isoformat(),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "assess_commissioned_campaign_readiness", _async_return(_readiness_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "generate_commissioned_campaign_preview", _async_return(_preview_response(campaign_id, 1)))
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)
    monkeypatch.setattr(
        cee,
        "evaluate_signal_risk",
        lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.00009"), steps=[]),
    )
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    async def _submit(self, *, db, request):
        _ = (db, request)
        return SimpleNamespace(
            live_crypto_order=SimpleNamespace(status="ACKNOWLEDGED", provider_order_id="provider-1"),
            provider_create_order_responded=True,
            provider_reconciliation_status="PENDING",
            safe_provider_response={},
            order_submitted=True,
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _submit)

    response = await cee.execute_commissioned_entry(
        db=_FakeDb(),
        request=_execution_request(campaign_id, 1, readiness_request),
    )

    assert response.current_state == "BUY_RECONCILIATION_PENDING"
    assert response.strategy_signal_classification == "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY"


@pytest.mark.asyncio
async def test_ownership_reconciliation_creates_active_position_from_partial_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_crypto_order_id = uuid4()
    definition = _definition(campaign_id, 1, state="BUY_RECONCILIATION_PENDING")
    definition.metadata_evidence["commissioned_seed_campaign"]["entry_execution"] = {
        "live_crypto_order_id": str(live_crypto_order_id),
        "decision_record_id": str(uuid4()),
        "risk_event_id": str(uuid4()),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)

    async def _reconcile(self, *, db, live_crypto_order_id, request):
        _ = (self, db, live_crypto_order_id, request)
        return SimpleNamespace(
            reconciliation_status="PARTIALLY_FILLED",
            provider_order_id="provider-order-1",
            campaign_correlation_status="verified",
            balance_mismatch_state="ok",
            live_crypto_order=SimpleNamespace(
                audit_correlation_id=uuid4(),
                safe_provider_response={
                    "create_order_payload": {"size": "0.00020"},
                    "reconciliation": {"observed_at": _now().isoformat()},
                },
            ),
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "reconcile", _reconcile)

    buy_rows = [
        SimpleNamespace(
            provider_order_id="provider-order-1",
            provider_fill_id="fill-1",
            filled_quantity=Decimal("0.00005"),
            gross_notional=Decimal("2.5"),
            fee_amount=Decimal("0.01"),
            recorded_at=_now(),
            created_at=_now(),
        ),
        SimpleNamespace(
            provider_order_id="provider-order-1",
            provider_fill_id="fill-2",
            filled_quantity=Decimal("0.00010"),
            gross_notional=Decimal("5.0"),
            fee_amount=Decimal("0.01"),
            recorded_at=_now(),
            created_at=_now(),
        ),
    ]
    monkeypatch.setattr(cee, "_load_buy_fill_accounting_rows", lambda **_kwargs: asyncio.sleep(0, result=buy_rows))

    response = await cee.reconcile_commissioned_buy_ownership(
        db=_FakeDb(),
        request=_ownership_request(campaign_id, 1, live_crypto_order_id),
    )

    assert response.ownership_proven is True
    assert response.current_state == "ACTIVE_POSITION"
    assert response.executed_quantity == Decimal("0.00015")
    assert response.attributable_remaining_quantity == Decimal("0.00005")
    assert response.provider_fill_ids == ["fill-1", "fill-2"]
    assert "ACTIVE_POSITION" in transitions.calls


@pytest.mark.asyncio
async def test_ownership_reconciliation_ambiguous_provider_state_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_crypto_order_id = uuid4()
    definition = _definition(campaign_id, 1, state="BUY_RECONCILIATION_PENDING")
    definition.metadata_evidence["commissioned_seed_campaign"]["entry_execution"] = {
        "live_crypto_order_id": str(live_crypto_order_id),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)

    async def _reconcile(self, *, db, live_crypto_order_id, request):
        _ = (self, db, live_crypto_order_id, request)
        return SimpleNamespace(
            reconciliation_status="UNKNOWN",
            provider_order_id="provider-order-1",
            campaign_correlation_status="verified",
            balance_mismatch_state="ok",
            live_crypto_order=SimpleNamespace(
                audit_correlation_id=uuid4(),
                safe_provider_response={
                    "create_order_payload": {"size": "0.00010"},
                    "reconciliation": {"observed_at": _now().isoformat()},
                },
            ),
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "reconcile", _reconcile)
    monkeypatch.setattr(
        cee,
        "_load_buy_fill_accounting_rows",
        lambda **_kwargs: asyncio.sleep(
            0,
            result=[
                SimpleNamespace(
                    provider_order_id="provider-order-1",
                    provider_fill_id="fill-1",
                    filled_quantity=Decimal("0.00010"),
                    gross_notional=Decimal("5.0"),
                    fee_amount=Decimal("0.01"),
                    recorded_at=_now(),
                    created_at=_now(),
                )
            ],
        ),
    )

    response = await cee.reconcile_commissioned_buy_ownership(
        db=_FakeDb(),
        request=_ownership_request(campaign_id, 1, live_crypto_order_id),
    )

    assert response.ownership_proven is False
    assert response.current_state == "RECONCILIATION_REQUIRED"
    assert "reconciliation_not_final_or_confident" in response.blockers
    assert "ACTIVE_POSITION" not in transitions.calls


@pytest.mark.asyncio
async def test_ownership_reconciliation_conflicting_provider_ids_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_crypto_order_id = uuid4()
    definition = _definition(campaign_id, 1, state="BUY_RECONCILIATION_PENDING")
    definition.metadata_evidence["commissioned_seed_campaign"]["entry_execution"] = {
        "live_crypto_order_id": str(live_crypto_order_id),
    }
    runtime = _runtime(campaign_id, 1)
    transitions = _TransitionRecorder(definition)

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "transition_commissioned_campaign_state", transitions)

    async def _reconcile(self, *, db, live_crypto_order_id, request):
        _ = (self, db, live_crypto_order_id, request)
        return SimpleNamespace(
            reconciliation_status="FILLED",
            provider_order_id="provider-order-1",
            campaign_correlation_status="verified",
            balance_mismatch_state="ok",
            live_crypto_order=SimpleNamespace(
                audit_correlation_id=uuid4(),
                safe_provider_response={
                    "create_order_payload": {"size": "0.00010"},
                    "reconciliation": {"observed_at": _now().isoformat()},
                },
            ),
        )

    monkeypatch.setattr(cee.LiveCryptoOrderService, "reconcile", _reconcile)

    monkeypatch.setattr(
        cee,
        "_load_buy_fill_accounting_rows",
        lambda **_kwargs: asyncio.sleep(
            0,
            result=[
                SimpleNamespace(
                    provider_order_id="provider-order-1",
                    provider_fill_id="fill-1",
                    filled_quantity=Decimal("0.00005"),
                    gross_notional=Decimal("2.5"),
                    fee_amount=Decimal("0.01"),
                    recorded_at=_now(),
                    created_at=_now(),
                ),
                SimpleNamespace(
                    provider_order_id="provider-order-2",
                    provider_fill_id="fill-2",
                    filled_quantity=Decimal("0.00005"),
                    gross_notional=Decimal("2.5"),
                    fee_amount=Decimal("0.01"),
                    recorded_at=_now(),
                    created_at=_now(),
                ),
            ],
        ),
    )

    response = await cee.reconcile_commissioned_buy_ownership(
        db=_FakeDb(),
        request=_ownership_request(campaign_id, 1, live_crypto_order_id),
    )

    assert response.ownership_proven is False
    assert response.current_state == "RECONCILIATION_REQUIRED"
    assert "provider_order_id_not_authoritative" in response.blockers


@pytest.mark.asyncio
async def test_ownership_reconciliation_replay_after_active_position(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_crypto_order_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "position_identity": "position-1",
        "provider_order_id": "provider-order-1",
        "provider_fill_ids": ["fill-1"],
        "executed_quantity": "0.00010",
        "average_entry_price": "50000",
        "total_buy_fees": "0.01",
        "attributable_remaining_quantity": "0",
        "evidence_timestamps": {"ownership_verified_at": _now().isoformat()},
        "correlation_ids": {"live_crypto_order_id": str(live_crypto_order_id)},
        "seen_idempotency_keys": {"ownership-reconcile-1": _now().isoformat()},
    }
    runtime = _runtime(campaign_id, 1)

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))

    response = await cee.reconcile_commissioned_buy_ownership(
        db=_FakeDb(),
        request=_ownership_request(campaign_id, 1, live_crypto_order_id),
    )

    assert response.replayed is True
    assert response.current_state == "ACTIVE_POSITION"
    assert response.ownership_proven is True


@pytest.mark.asyncio
async def test_exit_recommendation_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "provider_order_id": "provider-order-1",
        "correlation_ids": {"live_crypto_order_id": str(uuid4())},
    }
    runtime = _runtime(campaign_id, 1)

    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0001"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("50000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))
    monkeypatch.setattr(
        cee,
        "evaluate_position_lifecycle",
        lambda **_kwargs: SimpleNamespace(
            recommendation="HOLD_FOR_PROFIT",
            reason="Hold for threshold.",
            lifecycle_state="HOLDING_FOR_PROFIT",
            expected_net_realized_pnl_if_sold_now=Decimal("0.1"),
            current_market_value=Decimal("5"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        ),
    )

    db = _FakeDb()
    response = await cee.recommend_commissioned_exit(
        db=db,
        request=_exit_request(campaign_id, 1),
    )

    assert response.recommendation_type == "HOLD"
    assert response.no_sell_submitted is True


@pytest.mark.asyncio
async def test_exit_recommendation_profitable_sell_and_risk_called(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "provider_order_id": "provider-order-1",
        "correlation_ids": {"live_crypto_order_id": str(uuid4())},
    }
    runtime = _runtime(campaign_id, 1)

    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0002"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("60000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    calls = {"risk": 0}
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))
    monkeypatch.setattr(
        cee,
        "evaluate_position_lifecycle",
        lambda **_kwargs: SimpleNamespace(
            recommendation="SELL_NOW",
            reason="Profitable now.",
            lifecycle_state="PROFITABLE_EXIT_AVAILABLE",
            expected_net_realized_pnl_if_sold_now=Decimal("2.0"),
            current_market_value=Decimal("12"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        ),
    )

    def _risk(**_kwargs):
        calls["risk"] += 1
        risk_request = _kwargs["request"]
        assert risk_request.side == "sell"
        assert risk_request.campaign_authorized_notional is None
        return RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.0002"), steps=[])

    monkeypatch.setattr(cee, "evaluate_signal_risk", _risk)
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    db = _FakeDb()
    response = await cee.recommend_commissioned_exit(
        db=db,
        request=_exit_request(campaign_id, 1),
    )

    assert response.recommendation_type == "SELL_NOW"
    assert calls["risk"] == 1
    assert response.risk_action == "approve"
    assert any(item.__class__.__name__ == "DecisionRecord" for item in db.added)


@pytest.mark.asyncio
async def test_exit_recommendation_stop_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "provider_order_id": "provider-order-1",
    }
    runtime = _runtime(campaign_id, 1)
    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0002"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("49000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))
    monkeypatch.setattr(
        cee,
        "evaluate_position_lifecycle",
        lambda **_kwargs: SimpleNamespace(
            recommendation="STOP_LOSS_EXIT",
            reason="Stop loss.",
            lifecycle_state="STOP_LOSS_RECOMMENDED",
            expected_net_realized_pnl_if_sold_now=Decimal("-1.0"),
            current_market_value=Decimal("9"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        ),
    )
    monkeypatch.setattr(cee, "evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.0002"), steps=[]))
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    response = await cee.recommend_commissioned_exit(db=_FakeDb(), request=_exit_request(campaign_id, 1))
    assert response.recommendation_type == "STOP_LOSS_EXIT"


@pytest.mark.asyncio
async def test_exit_recommendation_max_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "provider_order_id": "provider-order-1",
    }
    runtime = _runtime(campaign_id, 1)
    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0002"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("50000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))
    monkeypatch.setattr(
        cee,
        "evaluate_position_lifecycle",
        lambda **_kwargs: SimpleNamespace(
            recommendation="MAX_HOLD_EXIT",
            reason="Max hold reached.",
            lifecycle_state="MAX_HOLD_EXIT_RECOMMENDED",
            expected_net_realized_pnl_if_sold_now=Decimal("-0.2"),
            current_market_value=Decimal("10"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        ),
    )
    monkeypatch.setattr(cee, "evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.0002"), steps=[]))
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    response = await cee.recommend_commissioned_exit(db=_FakeDb(), request=_exit_request(campaign_id, 1))
    assert response.recommendation_type == "MAX_HOLD_EXIT"


@pytest.mark.asyncio
async def test_exit_recommendation_stale_evidence_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    runtime = _runtime(campaign_id, 1)
    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0002"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("50000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))
    monkeypatch.setattr(
        cee,
        "evaluate_position_lifecycle",
        lambda **_kwargs: SimpleNamespace(
            recommendation="SELL_NOW",
            reason="Would sell if fresh.",
            lifecycle_state="STALE_MARKET_DATA",
            expected_net_realized_pnl_if_sold_now=Decimal("2.0"),
            current_market_value=Decimal("10"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=True,
            stale_indicator=True,
            dust_indicator=False,
            closed_indicator=False,
        ),
    )

    response = await cee.recommend_commissioned_exit(db=_FakeDb(), request=_exit_request(campaign_id, 1))
    assert response.recommendation_type == "HOLD"
    assert "market_evidence_stale_or_missing" in response.blockers


@pytest.mark.asyncio
async def test_exit_recommendation_missing_evidence_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    runtime = _runtime(campaign_id, 1)

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[]))

    response = await cee.recommend_commissioned_exit(db=_FakeDb(), request=_exit_request(campaign_id, 1))
    assert response.recommendation_type == "HOLD"
    assert "position_snapshot_unavailable" in response.blockers


@pytest.mark.asyncio
async def test_exit_recommendation_deterministic_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "provider_order_id": "provider-order-1",
        "correlation_ids": {"live_crypto_order_id": str(uuid4())},
    }
    runtime = _runtime(campaign_id, 1)
    calls = {"lifecycle": 0}

    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0002"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("60000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))

    def _lifecycle(**_kwargs):
        calls["lifecycle"] += 1
        return SimpleNamespace(
            recommendation="SELL_NOW",
            reason="Profitable now.",
            lifecycle_state="PROFITABLE_EXIT_AVAILABLE",
            expected_net_realized_pnl_if_sold_now=Decimal("2.0"),
            current_market_value=Decimal("12"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        )

    monkeypatch.setattr(cee, "evaluate_position_lifecycle", _lifecycle)
    monkeypatch.setattr(cee, "evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.0002"), steps=[]))
    monkeypatch.setattr(cee, "persist_risk_decision", lambda **_kwargs: asyncio.sleep(0, result=SimpleNamespace(risk_event_id=uuid4())))

    req = _exit_request(campaign_id, 1)
    first = await cee.recommend_commissioned_exit(db=_FakeDb(), request=req)
    second = await cee.recommend_commissioned_exit(db=_FakeDb(), request=req)

    assert first.replayed is False
    assert second.replayed is True
    assert first.recommendation_type == second.recommendation_type
    assert calls["lifecycle"] == 1


@pytest.mark.asyncio
async def test_exit_recommendation_provider_adapter_never_called_and_no_sell_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    definition = _definition(campaign_id, 1, state="ACTIVE_POSITION")
    definition.metadata_evidence["commissioned_seed_campaign"]["ownership_reconciliation"] = {
        "provider_order_id": "provider-order-1",
    }
    runtime = _runtime(campaign_id, 1)
    snapshot = SimpleNamespace(
        position_id="position-1",
        position_size=Decimal("0.0002"),
        provider_order_ids=("provider-order-1",),
        symbol="BTC-USD",
        asset_class="crypto",
        current_price=Decimal("60000"),
        entry_price=Decimal("50000"),
        opened_at=_now(),
        market_data_timestamp=_now(),
    )

    monkeypatch.setattr(
        "app.services.exchange_connections.providers.registry.get_exchange_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider adapter must not be called directly")),
    )

    async def _forbidden_submit(self, *, db, request):
        _ = (self, db, request)
        raise AssertionError("submit must not be called during recommendation")

    monkeypatch.setattr(cee.LiveCryptoOrderService, "submit", _forbidden_submit)
    monkeypatch.setattr(cee, "_load_definition_and_runtime_for_update", lambda **_kwargs: asyncio.sleep(0, result=(definition, runtime)))
    monkeypatch.setattr(cee, "load_position_snapshots", lambda **_kwargs: asyncio.sleep(0, result=[snapshot]))
    monkeypatch.setattr(cee, "resolve_lifecycle_policy", lambda **_kwargs: SimpleNamespace(policy_id="pl-1", policy_version="1.0.0", estimated_exit_fee_rate=Decimal("0.001"), estimated_slippage_rate=Decimal("0.001"), stale_price_threshold_minutes=15, max_hold_minutes=60))
    monkeypatch.setattr(
        cee,
        "evaluate_position_lifecycle",
        lambda **_kwargs: SimpleNamespace(
            recommendation="HOLD_FOR_PROFIT",
            reason="Hold.",
            lifecycle_state="HOLDING_FOR_PROFIT",
            expected_net_realized_pnl_if_sold_now=Decimal("0"),
            current_market_value=Decimal("10"),
            minimum_profitable_exit_price=Decimal("51000"),
            break_even_price=Decimal("50010"),
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        ),
    )

    response = await cee.recommend_commissioned_exit(db=_FakeDb(), request=_exit_request(campaign_id, 1))
    assert response.no_sell_submitted is True
