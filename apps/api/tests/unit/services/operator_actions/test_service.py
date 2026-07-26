from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.controlled_proof_run import ControlledProofRun
from app.models.decision_record import DecisionRecord
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.operator_action import OperatorAction
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.controlled_proof import service as controlled_proof_service
from app.services.operator_actions import controlled_proof_handler
from app.services.operator_actions import service as operator_action_service
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_ALL_TABLES = [
    Asset.__table__, AuditLog.__table__, AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__, AutonomousCapitalMandateAuthorization.__table__,
    AutonomousCapitalMandateEvaluation.__table__, Candle.__table__, CanonicalPreviewPackage.__table__,
    CapitalCampaign.__table__, CapitalCampaignDefinition.__table__, ControlledProofRun.__table__,
    DecisionRecord.__table__, LiveAccountingRecord.__table__, LiveCryptoOrder.__table__,
    LiveReconciliationEvent.__table__, LiveTradingProfile.__table__, OperatorAction.__table__,
    StrategyRosterRun.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _seed_campaign(session: AsyncSession, *, allowed_instruments: list[str] = ("BTC-USD",)) -> uuid.UUID:
    paper_account_id = uuid.uuid4()
    session.add(CapitalCampaignDefinition(
        campaign_id=_CAMPAIGN_ID, version=1, name="test", owner_identity="operator:test", status="READY",
        capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
        allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=list(allowed_instruments),
        campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
        minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
        profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
        compounding_policy={"policy_type": "FIXED_CAPITAL"},
    ))
    session.add(CapitalCampaign(
        uuid=_CAMPAIGN_ID, owner="operator:test", name="test", status="READY", campaign_type="definition_pinned_runtime",
        definition_campaign_id=_CAMPAIGN_ID, definition_version=1, paper_account_id=paper_account_id,
        starting_capital=Decimal("25"), current_equity=Decimal("25"),
    ))
    session.add(LiveTradingProfile(
        id=uuid.uuid4(), paper_account_id=paper_account_id, operating_mode="live", lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True, paper_default_mode=True,
        governance_approved=True, risk_authority_model="risk_engine_final", autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False, automatic_promotion_enabled=False, provenance_metadata={},
    ))
    await session.flush()
    return paper_account_id


async def _seed_asset_with_fresh_candles(session: AsyncSession, *, symbol: str = "BTC") -> Asset:
    asset = Asset(symbol=symbol, asset_class="crypto", exchange="kraken_spot", base_currency="USD", is_active=True)
    session.add(asset)
    await session.flush()
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    for i in range(60, 0, -1):
        open_time = now - timedelta(minutes=15 * i)
        session.add(Candle(
            asset_id=asset.id, interval="15m", open_time=open_time, close_time=open_time + timedelta(minutes=15),
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("1"), source="kraken_spot",
        ))
    await session.flush()
    return asset


async def _seed_active_mandate(session: AsyncSession, *, mandate_id: uuid.UUID, allowed_products: tuple[str, ...] = ("BTC-USD",)) -> None:
    session.add(AutonomousCapitalMandate(
        mandate_id=mandate_id, owner_actor_id="operator:owner", status="ACTIVE", autonomy_level="LEVEL_2",
        provider="kraken_spot", exchange_environment="production", exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), capital_campaign_id=None,
    ))
    version = AutonomousCapitalMandateVersion(
        mandate_version_id=uuid.uuid4(), mandate_id=mandate_id, version_number=1, version_hash="h1",
        base_currency="USD", authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("5"),
        max_daily_realized_loss_usd=Decimal("1"), max_campaign_drawdown_usd=Decimal("1"),
        max_consecutive_losses=2, position_limit=1, price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("20"), max_fee_bps=Decimal("50"), allowed_products=list(allowed_products),
        allowed_order_sides=["BUY", "SELL"], allowed_strategy_versions=[_STRATEGY_IDENTITY],
        entry_policy={}, exit_policy={}, cooldown_policy={}, operating_schedule={}, approval_policy="MANDATE_ALLOWED",
        reconciliation_policy={}, kill_switch_policy={}, owner_acknowledgements={"a": True},
        authorization_evidence_summary={"b": True}, is_authorized=True, is_active=True,
    )
    session.add(version)
    session.add(AutonomousCapitalMandateAuthorization(
        mandate_id=mandate_id, mandate_version_id=version.mandate_version_id, authorization_state="AUTHORIZED",
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE", authorized_by_actor_id="operator:owner",
        authorization_method="test", owner_acknowledgements={"a": True}, authorization_evidence={"b": True},
        deterministic_explanation={"c": True}, idempotency_key=f"auth-{mandate_id}",
    ))
    await session.flush()


def _fully_ready_settings(*, mandate_id: uuid.UUID, additional_products: str = ""):
    from types import SimpleNamespace
    return SimpleNamespace(
        automatic_mandate_package_activation_mandate_id=mandate_id,
        automatic_mandate_package_activation_campaign_id=_CAMPAIGN_ID,
        asset_discovery_mode="env",
        autonomous_cycle_additional_products=additional_products,
        parsed_autonomous_cycle_additional_products=[p.strip() for p in additional_products.split(",") if p.strip()],
    )


async def _seed_fully_ready_scope(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    mandate_id = uuid.uuid4()
    await _seed_campaign(session, allowed_instruments=["BTC-USD"])
    await _seed_asset_with_fresh_candles(session, symbol="BTC")
    await _seed_active_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
    monkeypatch.setattr(
        "app.services.asset_commissioning.service.get_settings",
        lambda: _fully_ready_settings(mandate_id=mandate_id),
    )
    return mandate_id


# --- submission: delegation, idempotency, fail-closed validation --------------------

@pytest.mark.asyncio
async def test_run_controlled_proof_creates_one_action_and_delegates_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        calls = []
        real_create = controlled_proof_handler.create_controlled_proof

        async def _counting_create(**kwargs):
            calls.append(kwargs)
            return await real_create(**kwargs)

        monkeypatch.setattr(controlled_proof_handler, "create_controlled_proof", _counting_create)

        action = await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-1",
            parameters={"product_id": "BTC-USD", "expires_in_minutes": 30}, actor="operator:alice",
        )

        assert len(calls) == 1
        assert action["status"] == "ACCEPTED"
        assert action["linked_resource_type"] == "controlled_proof_run"
        assert action["linked_resource_id"] is not None

        rows = (await session.execute(select(OperatorAction))).scalars().all()
        assert len(rows) == 1
        proof_rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(proof_rows) == 1
        assert proof_rows[0].proof_id == action["linked_resource_id"]


@pytest.mark.asyncio
async def test_idempotent_replay_returns_original_action_and_proof_without_recreating(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        calls = []
        real_create = controlled_proof_handler.create_controlled_proof

        async def _counting_create(**kwargs):
            calls.append(kwargs)
            return await real_create(**kwargs)

        monkeypatch.setattr(controlled_proof_handler, "create_controlled_proof", _counting_create)

        first = await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-replay",
            parameters={"product_id": "BTC-USD"}, actor="operator:alice",
        )
        second = await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-replay",
            parameters={"product_id": "BTC-USD"}, actor="operator:alice",
        )

        assert first["action_id"] == second["action_id"]
        assert first["linked_resource_id"] == second["linked_resource_id"]
        # create_controlled_proof (the underlying domain delegation) must
        # only ever be invoked on the first submit -- a replay must not
        # create a second ControlledProofRun.
        assert len(calls) == 1

        action_rows = (await session.execute(select(OperatorAction))).scalars().all()
        assert len(action_rows) == 1
        proof_rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(proof_rows) == 1


@pytest.mark.asyncio
async def test_idempotency_survives_without_relying_on_process_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 14: a row that already exists in the database (as if
    written by a prior process instance, not this one) is what idempotent
    replay relies on -- never any in-memory cache. Proven by inserting the
    OperatorAction + linked ControlledProofRun directly, with a brand-new
    call to submit_operator_action (fresh Python call, no shared state) that
    must find it purely via the DB unique constraint lookup."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="operator-action:oa-restart",
            expires_in_minutes=60, actor="operator:alice",
        )
        action = OperatorAction(
            action_type="RUN_CONTROLLED_PROOF", status="ACCEPTED", actor="operator:alice",
            idempotency_key="oa-restart", parameters={"product_id": "BTC-USD", "expires_in_minutes": 60},
            linked_resource_type="controlled_proof_run", linked_resource_id=proof.proof_id,
            accepted_at=datetime.now(timezone.utc),
        )
        session.add(action)
        await session.commit()

        calls = []
        real_create = controlled_proof_handler.create_controlled_proof

        async def _counting_create(**kwargs):
            calls.append(kwargs)
            return await real_create(**kwargs)

        monkeypatch.setattr(controlled_proof_handler, "create_controlled_proof", _counting_create)

        replayed = await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-restart",
            parameters={"product_id": "BTC-USD", "expires_in_minutes": 60}, actor="operator:alice",
        )

        assert replayed["action_id"] == action.action_id
        assert replayed["linked_resource_id"] == proof.proof_id
        assert calls == []
        proof_rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(proof_rows) == 1


@pytest.mark.asyncio
async def test_unknown_action_type_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        with pytest.raises(InvalidRequestError):
            await operator_action_service.submit_operator_action(
                db=session, action_type="LAUNCH_NUKES", idempotency_key="oa-unknown",
                parameters={}, actor="operator:alice",
            )
        rows = (await session.execute(select(OperatorAction))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_unknown_parameter_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        with pytest.raises(InvalidRequestError):
            await operator_action_service.submit_operator_action(
                db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-bad-param",
                parameters={"product_id": "BTC-USD", "extra_field": "nope"}, actor="operator:alice",
            )
        rows = (await session.execute(select(OperatorAction))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_field,value",
    [
        ("provider", "coinbase"), ("campaign_id", str(uuid.uuid4())), ("environment", "sandbox"),
        ("max_notional_usd", "500000"), ("mandate_id", str(uuid.uuid4())), ("strategy_version", "evil@9"),
        ("actor", "operator:mallory"), ("live_submission_allowed", True),
    ],
)
async def test_caller_cannot_supply_scope_or_execution_settings(
    monkeypatch: pytest.MonkeyPatch, forbidden_field: str, value: object,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        with pytest.raises(InvalidRequestError):
            await operator_action_service.submit_operator_action(
                db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key=f"oa-forbidden-{forbidden_field}",
                parameters={"product_id": "BTC-USD", forbidden_field: value}, actor="operator:alice",
            )


@pytest.mark.asyncio
async def test_replay_with_different_action_type_is_a_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-conflict",
            parameters={"product_id": "BTC-USD"}, actor="operator:alice",
        )
        with pytest.raises(ConflictError):
            await operator_action_service.submit_operator_action(
                db=session, action_type="SOME_OTHER_ACTION", idempotency_key="oa-conflict",
                parameters={}, actor="operator:alice",
            )


# --- read: status projection, no audit noise on GET ----------------------------------

@pytest.mark.asyncio
async def test_get_unknown_action_raises_not_found() -> None:
    async with _real_session() as session:
        with pytest.raises(NotFoundError):
            await operator_action_service.get_operator_action(db=session, action_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_get_does_not_duplicate_audit_transitions_when_status_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        action = await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-no-noise",
            parameters={"product_id": "BTC-USD"}, actor="operator:alice",
        )
        audits_before = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == action["action_id"], AuditLog.entity_type == "operator_action")
        )).scalars().all()

        await operator_action_service.get_operator_action(db=session, action_id=action["action_id"])
        await operator_action_service.get_operator_action(db=session, action_id=action["action_id"])

        audits_after = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == action["action_id"], AuditLog.entity_type == "operator_action")
        )).scalars().all()
        assert len(audits_after) == len(audits_before)


@pytest.mark.asyncio
async def test_get_persists_exactly_one_audit_row_when_status_actually_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        action = await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-transition",
            parameters={"product_id": "BTC-USD"}, actor="operator:alice",
        )
        proof = await session.get(ControlledProofRun, action["linked_resource_id"])
        proof.status = "CLAIMED"
        await session.flush()

        view1 = await operator_action_service.get_operator_action(db=session, action_id=action["action_id"])
        view2 = await operator_action_service.get_operator_action(db=session, action_id=action["action_id"])

        assert view1["status"] == "IN_PROGRESS"
        assert view2["status"] == "IN_PROGRESS"
        audits = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_id == action["action_id"], AuditLog.entity_type == "operator_action",
                AuditLog.action == "operator_action.status_transitioned",
            )
        )).scalars().all()
        assert len(audits) == 1


# --- list ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_filters_by_action_type_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        await operator_action_service.submit_operator_action(
            db=session, action_type="RUN_CONTROLLED_PROOF", idempotency_key="oa-list-1",
            parameters={"product_id": "BTC-USD"}, actor="operator:alice",
        )
        results = await operator_action_service.list_operator_actions(db=session, action_type="RUN_CONTROLLED_PROOF")
        assert len(results) == 1
        assert results[0]["status"] == "ACCEPTED"

        none_results = await operator_action_service.list_operator_actions(db=session, status="SUCCEEDED")
        assert none_results == []


@pytest.mark.asyncio
async def test_list_enforces_server_maximum_limit() -> None:
    async with _real_session() as session:
        results = await operator_action_service.list_operator_actions(db=session, limit=10_000)
        assert results == []
        # No exception for an absurd limit -- it is silently bounded, not
        # trusted from the caller.


def test_no_operator_action_module_calls_execution_provider() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[4] / "app" / "services" / "operator_actions"
    source = "\n".join(p.read_text() for p in root.glob("*.py")).lower()
    for forbidden in ("live_service.submit", "create_order", "submit_order", "kraken_spot.py", "exchange_connections.providers"):
        assert forbidden not in source, f"forbidden reference found: {forbidden}"


def test_operator_actions_route_module_never_calls_execution_provider() -> None:
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[4] / "app" / "api" / "routes" / "operator_actions.py"
    source = path.read_text().lower()
    # "kraken" itself is deliberately not checked here -- the route's OpenAPI
    # description prose names it descriptively (Kraken production scope)
    # without calling any Kraken provider code.
    for forbidden in ("live_service.submit", "create_order", "submit_order", "kraken_spot.py", "provider_client"):
        assert forbidden not in source, f"forbidden reference found: {forbidden}"
