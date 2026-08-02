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
async def test_sell_proceeds_above_entry_cap_are_not_new_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    mandate = _mandate()
    mandate_version = _mandate_version(mandate_id=mandate.mandate_id)
    mandate_version.allowed_order_sides = ["SELL"]
    definition = _definition(
        campaign_id=campaign_id,
        version=1,
        status="ACTIVE",
        metadata_evidence=_commissioned_metadata(state="ACTIVE_POSITION", cap="5"),
    )
    runtime = _runtime(campaign_id=campaign_id, version=1, status="ACTIVE")
    request = _request(
        campaign_id=campaign_id,
        version=1,
        mandate_id=mandate.mandate_id,
        mandate_version_id=mandate_version.mandate_version_id,
        live_profile_id=uuid4(),
    )
    request.side = "SELL"
    request.requested_quote_amount = Decimal("7.20")
    request.reconciliation_evidence = {"owned_base_quantity": "0.00008"}

    _patch_ready_baseline(
        monkeypatch,
        definition=definition,
        runtime=runtime,
        mandate=mandate,
        mandate_version=mandate_version,
    )
    readiness = await crp.assess_commissioned_campaign_readiness(db=_FakeDb(), request=request)

    capital_check = next(item for item in readiness.checks if item["code"] == "capital_cap")
    assert capital_check["status"] == "pass"
    assert capital_check["detail"]["capital_deployment_amount"] == Decimal("0")
    assert "requested_quote_amount_above_authorized_cap" not in readiness.blockers


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


# --- _has_reconciliation_conflict: real-session latest-per-order regression coverage ---
#
# Confirmed duplicate of the canonical campaign-binding production defect:
# _has_reconciliation_conflict used to match ANY historical row in
# _RECONCILIATION_BLOCKING_STATUSES for a campaign (ORDER BY recorded_at,
# sequence_number DESC LIMIT 1, with no per-order grouping), so an order's
# own superseded reconciliation_required/conflict history kept reporting a
# conflict forever even after a later reconciliation pass recorded that same
# order as filled. Now delegates to
# reconciliation_guard.has_unresolved_reconciliation_for_campaign, the same
# shared "latest event per identified order, fail-closed for identityless
# events" helper canonical_campaign_binding's counters use -- these tests
# exercise the real function against a real SQLite session (no
# monkeypatching of _has_reconciliation_conflict itself, unlike every other
# test in this file).


def _install_sqlite_type_compilers_for_commissioned_readiness_tests() -> None:
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles

    # @compiles registration is process-global (dispatches on the type
    # class, not any particular engine/session), so this only needs to run
    # once at import time -- mirrors the equivalent registrations in
    # tests/unit/services/test_canonical_campaign_binding.py and
    # tests/integration/test_continuous_pipeline_worker.py, duplicated here
    # rather than imported since those modules have their own unrelated,
    # heavyweight import-time side effects this file should not take on.
    @compiles(PG_UUID, "sqlite")
    def _compile_uuid_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "CHAR(36)"

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "JSON"


_install_sqlite_type_compilers_for_commissioned_readiness_tests()


def _reconciliation_conflict_sqlite_session():
    from contextlib import contextmanager

    from sqlalchemy import create_engine, event as sa_event, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.schema import DefaultClause
    from sqlalchemy.sql.elements import TextClause

    from app.models.live_crypto_order import LiveCryptoOrder
    from app.models.live_reconciliation_event import LiveReconciliationEvent

    @contextmanager
    def _session():
        engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)

        @sa_event.listens_for(engine, "connect")
        def _register_functions(dbapi_conn, _record) -> None:  # noqa: ANN001
            dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
            dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid4().hex)

        tables = [LiveCryptoOrder.__table__, LiveReconciliationEvent.__table__]
        for table in tables:
            for column in table.columns:
                default = column.server_default
                if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
                    raw = default.arg.text.strip().split("::", 1)[0]
                    if raw.endswith("()") and not raw.startswith("("):
                        raw = f"({raw})"
                    column.server_default = DefaultClause(text(raw))
        LiveCryptoOrder.metadata.create_all(engine, tables=tables)
        try:
            with Session(engine) as session:
                yield session, _AwaitableReconciliationConflictSession(session)
        finally:
            engine.dispose()

    return _session()


class _AwaitableReconciliationConflictSession:
    """Minimal AsyncSession-shaped adapter over a real synchronous ORM
    Session, scoped to exactly what _has_reconciliation_conflict (via
    reconciliation_guard) needs (db.scalar)."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self._session = session

    async def scalar(self, statement):
        return self._session.scalar(statement)

    async def execute(self, statement):
        return self._session.execute(statement)


def _seed_reconciliation_conflict_order(session, *, live_crypto_order_id) -> None:  # noqa: ANN001
    from app.models.live_crypto_order import LiveCryptoOrder

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    session.add(
        LiveCryptoOrder(
            live_crypto_order_id=live_crypto_order_id,
            crypto_order_preview_id=uuid4(),
            exchange_connection_id=uuid4(),
            provider="kraken_spot",
            environment="production",
            product_id="ETH-USD",
            side="buy",
            order_type="market",
            requested_quote_size=Decimal("5"),
            client_order_id=f"client-{live_crypto_order_id}",
            status="PARTIALLY_FILLED",
            risk_event_id=None,
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=f"KRAKEN-{live_crypto_order_id}",
            provider_status="partially_filled",
            submitted_at=now - timedelta(minutes=10),
            acknowledged_at=now - timedelta(minutes=9),
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={},
            audit_correlation_id=uuid4(),
            operator_confirmation_id=None,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
        )
    )
    session.commit()


def _seed_reconciliation_conflict_event(
    session,  # noqa: ANN001
    *,
    live_trading_profile_id,  # noqa: ANN001
    capital_campaign_id: int | None,
    reconciliation_status: str,
    sequence_number: int,
    live_crypto_order_id=None,  # noqa: ANN001
):
    from app.models.live_reconciliation_event import LiveReconciliationEvent

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    event_id = uuid4()
    session.add(
        LiveReconciliationEvent(
            id=event_id,
            idempotency_key=f"idem-{event_id}",
            event_hash=f"hash-{event_id}",
            live_trading_profile_id=live_trading_profile_id,
            live_crypto_order_id=live_crypto_order_id,
            capital_campaign_id=capital_campaign_id,
            source_execution_event_id=uuid4(),
            source_execution_event_type="execution_intent_created",
            sequence_number=sequence_number,
            event_type="order_reconciled",
            reconciliation_status=reconciliation_status,
            provider_name="kraken_spot",
            provider_order_id=None if live_crypto_order_id is None else f"KRAKEN-{live_crypto_order_id}",
            provider_fill_id=None,
            event_payload={},
            provenance={},
            immutable_contract_version="1.0.0",
            provider_recorded_at=now,
            recorded_at=now,
            created_at=now,
        )
    )
    session.commit()
    return event_id


@pytest.mark.asyncio
async def test_has_reconciliation_conflict_ignores_superseded_history_once_order_resolves() -> None:
    """Historical reconciliation_required evidence superseded by a later
    'filled' event must not report a conflict."""
    profile_id = uuid4()
    order_id = uuid4()

    with _reconciliation_conflict_sqlite_session() as (raw_session, db):
        _seed_reconciliation_conflict_order(raw_session, live_crypto_order_id=order_id)
        _seed_reconciliation_conflict_event(raw_session, live_trading_profile_id=profile_id, capital_campaign_id=4, live_crypto_order_id=order_id, reconciliation_status="reconciliation_required", sequence_number=1)
        _seed_reconciliation_conflict_event(raw_session, live_trading_profile_id=profile_id, capital_campaign_id=4, live_crypto_order_id=order_id, reconciliation_status="filled", sequence_number=2)

        result = await crp._has_reconciliation_conflict(db=db, runtime_campaign_id=4)

    assert result is False


@pytest.mark.asyncio
async def test_has_reconciliation_conflict_true_when_latest_event_is_genuinely_unresolved() -> None:
    """Fail-closed behavior must be preserved: a genuinely latest-unresolved
    event still reports a conflict."""
    profile_id = uuid4()
    order_id = uuid4()

    with _reconciliation_conflict_sqlite_session() as (raw_session, db):
        _seed_reconciliation_conflict_order(raw_session, live_crypto_order_id=order_id)
        _seed_reconciliation_conflict_event(raw_session, live_trading_profile_id=profile_id, capital_campaign_id=4, live_crypto_order_id=order_id, reconciliation_status="conflict", sequence_number=1)

        result = await crp._has_reconciliation_conflict(db=db, runtime_campaign_id=4)

    assert result is True


@pytest.mark.asyncio
async def test_has_reconciliation_conflict_identityless_evidence_remains_blocking() -> None:
    """A blocking-status event with no live_crypto_order_id can never be
    proven superseded -- it must keep reporting a conflict unconditionally."""
    profile_id = uuid4()

    with _reconciliation_conflict_sqlite_session() as (raw_session, db):
        _seed_reconciliation_conflict_event(raw_session, live_trading_profile_id=profile_id, capital_campaign_id=4, live_crypto_order_id=None, reconciliation_status="unknown", sequence_number=1)

        result = await crp._has_reconciliation_conflict(db=db, runtime_campaign_id=4)

    assert result is True


@pytest.mark.asyncio
async def test_has_reconciliation_conflict_campaign_scoping_cannot_be_bypassed() -> None:
    """An unresolved event that belongs to a DIFFERENT capital campaign must
    never report a conflict for this campaign -- BTC (campaign version 3)
    and ETH (campaign version 4) must be able to reach commissioned
    readiness independently."""
    profile_id = uuid4()
    btc_order, eth_order = uuid4(), uuid4()

    with _reconciliation_conflict_sqlite_session() as (raw_session, db):
        _seed_reconciliation_conflict_order(raw_session, live_crypto_order_id=btc_order)
        _seed_reconciliation_conflict_order(raw_session, live_crypto_order_id=eth_order)
        _seed_reconciliation_conflict_event(raw_session, live_trading_profile_id=profile_id, capital_campaign_id=3, live_crypto_order_id=btc_order, reconciliation_status="conflict", sequence_number=1)
        _seed_reconciliation_conflict_event(raw_session, live_trading_profile_id=profile_id, capital_campaign_id=4, live_crypto_order_id=eth_order, reconciliation_status="filled", sequence_number=2)

        btc_result = await crp._has_reconciliation_conflict(db=db, runtime_campaign_id=3)
        eth_result = await crp._has_reconciliation_conflict(db=db, runtime_campaign_id=4)

    assert btc_result is True
    assert eth_result is False
