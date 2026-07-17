from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.capital_campaign_domain import CommissionedReadinessRequest
from app.services.capital_campaign_domain import commissioned_readiness_preview as crp


class _FakeDb:
    def __init__(self) -> None:
        self.add_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, _obj) -> None:
        self.add_calls += 1

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1


class _Snapshot:
    def __init__(self, *, position_size: Decimal) -> None:
        self.position_size = position_size


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


def _definition(*, campaign_id, version: int, status: str = "READY", metadata_evidence: dict | None = None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        campaign_id=campaign_id,
        version=version,
        status=status,
        metadata_evidence=metadata_evidence or {},
        risk_policy_id="risk-v1",
        risk_policy_version="1.0.0",
    )


def _runtime(*, campaign_id, version: int, status: str = "READY"):
    return SimpleNamespace(
        id=101,
        uuid=campaign_id,
        definition_version=version,
        status=status,
        paper_account_id=uuid4(),
    )


def _commissioned_metadata(*, state: str = "READY", cap: str = "5") -> dict:
    return {
        "commissioned_seed_campaign": {
            "state": state,
            "authority_metadata": {
                "campaign_type": "COMMISSIONED_AUTONOMOUS_SEED",
                "entry_authority": "OPERATOR_COMMISSIONED",
                "lifecycle_authority": "OMNITRADE_AUTONOMOUS",
                "maximum_entry_notional": cap,
                "repeat_entry_allowed": False,
                "commissioned_by": "operator",
                "commissioned_at": datetime.now(timezone.utc).isoformat(),
            },
            "evidence_metadata": [],
            "transition_history": [],
            "seen_idempotency_keys": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    }


def _mandate():
    return SimpleNamespace(
        mandate_id=uuid4(),
        provider="kraken_spot",
        exchange_environment="production",
    )


def _mandate_version(*, mandate_id, version_number: int = 7):
    return SimpleNamespace(
        mandate_version_id=uuid4(),
        mandate_id=mandate_id,
        version_number=version_number,
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("25"),
        max_daily_deployed_usd=Decimal("25"),
        max_daily_realized_loss_usd=Decimal("5"),
        max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=5,
        position_limit=1,
        price_evidence_max_age_seconds=120,
        max_slippage_bps=Decimal("50"),
        max_fee_bps=Decimal("50"),
        allowed_products=["BTC-USD"],
        allowed_order_sides=["BUY"],
        allowed_strategy_versions=["ma_crossover@1.0.0"],
        approval_policy="HUMAN_REQUIRED",
        is_authorized=True,
        is_active=True,
    )


def _request(*, campaign_id, version: int, mandate_id, mandate_version_id, live_profile_id):
    now = datetime.now(timezone.utc)
    return CommissionedReadinessRequest(
        campaign_id=campaign_id,
        version=version,
        provider="kraken_spot",
        environment="production",
        instrument="BTC-USD",
        requested_quote_amount=Decimal("5"),
        idempotency_key="commissioned-preview-key",
        live_trading_profile_id=live_profile_id,
        mandate_id=mandate_id,
        mandate_version_id=mandate_version_id,
        expected_mandate_version_number=7,
        expected_risk_policy_id="risk-v1",
        expected_risk_policy_version="1.0.0",
        authorization_expires_at=now + timedelta(minutes=10),
        provider_capability_evidence={"supported": True, "source": "provider_capability_snapshot", "observed_at": now.isoformat()},
        connectivity_evidence={"reachable": True, "source": "connectivity_probe", "observed_at": now.isoformat()},
        balance_evidence={"available_quote_balance": "25", "source": "balance_snapshot", "observed_at": now.isoformat()},
        market_data_evidence={"observed_at": now.isoformat(), "max_age_seconds": 120, "source": "market_candle"},
        price_evidence={"reference_price": "50000", "observed_at": now.isoformat(), "max_age_seconds": 120, "source": "price_reference"},
        minimum_order_evidence={"minimum_quote_amount": "5", "minimum_base_quantity": "0.00001", "source": "venue_rules", "observed_at": now.isoformat()},
        fee_slippage_evidence={"estimated_entry_fee": "0.01", "estimated_future_exit_fee": "0.01", "estimated_slippage": "0.01", "source": "fee_model"},
        runtime_readiness_evidence={"ready": True, "source": "runtime_status", "observed_at": now.isoformat()},
        manual_review_evidence={"required": False},
    )


def _patch_ready_baseline(monkeypatch: pytest.MonkeyPatch, *, definition, runtime, mandate, mandate_version):
    monkeypatch.setattr(crp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(crp, "_load_runtime_campaign", _async_return(runtime))
    monkeypatch.setattr(crp, "_load_mandate", _async_return(mandate))
    monkeypatch.setattr(crp, "_load_mandate_version", _async_return(mandate_version))
    monkeypatch.setattr(crp, "_has_open_order_conflict", _async_return(False))
    monkeypatch.setattr(crp, "_has_reconciliation_conflict", _async_return(False))
    monkeypatch.setattr(crp, "load_position_snapshots", _async_return([]))
    monkeypatch.setattr(
        crp,
        "evaluate_live_approval_gate",
        _async_return(SimpleNamespace(allowed=True, reason=None)),
    )


@pytest.mark.asyncio
async def test_fully_ready_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_profile_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=live_profile_id,
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)
    db = _FakeDb()

    readiness = await crp.assess_commissioned_campaign_readiness(db=db, request=request)

    assert readiness.readiness_verdict == "READY"
    assert readiness.blockers == []
    assert readiness.authority_classification == "OPERATOR_COMMISSIONED"
    assert readiness.strategy_signal_classification == "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY"


@pytest.mark.asyncio
async def test_missing_authority_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence={})
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert readiness.readiness_verdict == "BLOCKED"
    assert "missing_commissioned_authority" in readiness.blockers


@pytest.mark.asyncio
async def test_expired_authority_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )
    request.authorization_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "expired_operator_authorization" in readiness.blockers


@pytest.mark.asyncio
async def test_capital_cap_violation_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata(cap="3"))
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "requested_quote_amount_above_authorized_cap" in readiness.blockers


@pytest.mark.asyncio
async def test_insufficient_balance_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )
    request.balance_evidence = {"available_quote_balance": "1", "source": "balance_snapshot", "observed_at": datetime.now(timezone.utc).isoformat()}

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "insufficient_balance" in readiness.blockers


@pytest.mark.asyncio
async def test_stale_market_evidence_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )
    request.market_data_evidence = {
        "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "max_age_seconds": 120,
        "source": "market_candle",
    }

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "stale_or_missing_market_evidence" in readiness.blockers


@pytest.mark.asyncio
async def test_provider_capability_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )
    request.provider_capability_evidence = {"supported": False, "source": "provider_capability_snapshot"}

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "unsupported_provider_capability" in readiness.blockers


@pytest.mark.asyncio
async def test_minimum_order_violation_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )
    request.minimum_order_evidence = {
        "minimum_quote_amount": "10",
        "minimum_base_quantity": "0.00001",
        "source": "venue_rules",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "minimum_order_violation" in readiness.blockers


@pytest.mark.asyncio
async def test_unresolved_reconciliation_conflict_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)
    monkeypatch.setattr(crp, "_has_reconciliation_conflict", _async_return(True))

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "unresolved_reconciliation_conflict" in readiness.blockers


@pytest.mark.asyncio
async def test_existing_position_or_entry_conflict_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)
    monkeypatch.setattr(crp, "load_position_snapshots", _async_return([_Snapshot(position_size=Decimal("0.1"))]))

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "existing_position_or_entry_conflict" in readiness.blockers


@pytest.mark.asyncio
async def test_mandate_version_mismatch_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id, version_number=3)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "mandate_version_mismatch" in readiness.blockers


@pytest.mark.asyncio
async def test_inconsistent_state_metadata_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, status="READY", metadata_evidence=_commissioned_metadata(state="COMMISSIONED"))
    runtime = _runtime(campaign_id=campaign_id, version=1, status="READY")
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    assert "inconsistent_commissioned_state_metadata" in readiness.blockers


@pytest.mark.asyncio
async def test_deterministic_preview_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_profile_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=live_profile_id,
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    db = _FakeDb()
    first = await crp.generate_commissioned_campaign_preview(db=db, request=request)
    second = await crp.generate_commissioned_campaign_preview(db=db, request=request)

    assert first.preview_identity_hash == second.preview_identity_hash
    assert first.readiness_verdict == second.readiness_verdict
    assert first.blockers == second.blockers


@pytest.mark.asyncio
async def test_preview_evidence_provenance_and_classifications(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_profile_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=live_profile_id,
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    preview = await crp.generate_commissioned_campaign_preview(db=_FakeDb(), request=request)

    assert preview.evidence_provenance["market_data"] == "market_candle"
    assert preview.authority_classification == "OPERATOR_COMMISSIONED"
    assert preview.strategy_signal_classification == "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY"


@pytest.mark.asyncio
async def test_readiness_and_preview_are_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_profile_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=live_profile_id,
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    db = _FakeDb()
    original_definition_status = definition.status
    original_runtime_status = runtime.status

    readiness = await crp.assess_commissioned_campaign_readiness(db=db, request=request)
    preview = await crp.generate_commissioned_campaign_preview(db=db, request=request)

    assert readiness.readiness_verdict == "READY"
    assert preview.no_database_writes is True
    assert preview.no_order_submission is True
    assert preview.no_position_creation is True
    assert definition.status == original_definition_status
    assert runtime.status == original_runtime_status
    assert db.add_calls == 0
    assert db.flush_calls == 0
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_provider_order_submission_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    live_profile_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    definition = _definition(campaign_id=campaign_id, version=1, metadata_evidence=_commissioned_metadata())
    runtime = _runtime(campaign_id=campaign_id, version=1)
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=live_profile_id,
    )

    _patch_ready_baseline(monkeypatch, definition=definition, runtime=runtime, mandate=mandate, mandate_version=mandate_version)

    create_order_calls = {"count": 0}

    class _NeverCallProvider:
        async def create_order(self, **_kwargs):
            create_order_calls["count"] += 1
            raise AssertionError("create_order must not be called from readiness/preview")

    async def _forbidden_provider(**_kwargs):
        return _NeverCallProvider()

    monkeypatch.setattr(crp, "get_exchange_provider", _forbidden_provider, raising=False)

    await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)
    await crp.generate_commissioned_campaign_preview(db=_FakeDb(), request=request)

    assert create_order_calls["count"] == 0
