from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_snapshot import DecisionSnapshot
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.risk_kill_switch import RiskKillSwitch
from app.services.live_crypto_orders import _evaluate_level2_mandate_authorization
from app.services.mandates import lifecycle
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
)
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_PROVIDER = "kraken_spot"
_ENVIRONMENT = "production"
_PRODUCT = "BTC-USD"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(
        [
            AutonomousCapitalMandate.__table__,
            AutonomousCapitalMandateVersion.__table__,
            AutonomousCapitalMandateAuthorization.__table__,
            AutonomousCapitalMandateEvaluation.__table__,
            AuditLog.__table__,
            CryptoOrderPreview.__table__,
            DecisionSnapshot.__table__,
            RiskEvent.__table__,
            LiveTradingProfile.__table__,
            PaperAccount.__table__,
            CapitalCampaign.__table__,
            RiskKillSwitch.__table__,
        ]
    ) as session:
        yield session


def _version_request(
    *,
    mandate_id: uuid.UUID,
    idempotency_key: str,
    max_order_notional_usd: Decimal = Decimal("5"),
    allowed_strategy_versions: tuple[str, ...] = (_STRATEGY_IDENTITY,),
    allowed_products: tuple[str, ...] = (_PRODUCT,),
    allowed_order_sides: tuple[str, ...] = ("BUY", "SELL", "HOLD"),
) -> MandateVersionCreateRequest:
    return MandateVersionCreateRequest(
        mandate_id=mandate_id,
        actor="operator:owner",
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=max_order_notional_usd,
        max_open_exposure_usd=Decimal("25"),
        max_daily_deployed_usd=Decimal("25"),
        max_daily_realized_loss_usd=Decimal("10"),
        max_campaign_drawdown_usd=Decimal("10"),
        max_consecutive_losses=5,
        position_limit=1,
        price_evidence_max_age_seconds=300,
        max_slippage_bps=Decimal("50"),
        max_fee_bps=Decimal("50"),
        allowed_products=allowed_products,
        allowed_order_sides=allowed_order_sides,
        allowed_strategy_versions=allowed_strategy_versions,
        entry_policy={},
        exit_policy={},
        cooldown_policy={},
        operating_schedule={},
        approval_policy="MANDATE_ALLOWED",
        reconciliation_policy={},
        kill_switch_policy={},
        owner_acknowledgements={"accepted": True},
        authorization_evidence_summary={"source": "owner"},
        idempotency_key=idempotency_key,
        audit_correlation_id=uuid.uuid4(),
    )


def _authorization_request(*, mandate_id: uuid.UUID, mandate_version_id: uuid.UUID, idempotency_key: str) -> MandateAuthorizationRequest:
    return MandateAuthorizationRequest(
        mandate_id=mandate_id,
        mandate_version_id=mandate_version_id,
        actor="operator:owner",
        authorization_method="owner_signature",
        owner_acknowledgements={"accepted": True},
        authorization_evidence={"signature": "hash"},
        deterministic_explanation={"reason": "explicit_owner_authorization"},
        expires_at=None,
        idempotency_key=idempotency_key,
        audit_correlation_id=uuid.uuid4(),
    )


def _lifecycle_request(*, mandate_id: uuid.UUID, action: str, idempotency_key: str) -> MandateLifecycleActionRequest:
    return MandateLifecycleActionRequest(
        mandate_id=mandate_id,
        actor="operator:owner",
        action=action,
        reason=f"test:{action.lower()}",
        idempotency_key=idempotency_key,
        audit_correlation_id=uuid.uuid4(),
        software_build_version="build-1",
    )


async def _seed_active_level2_mandate(
    session: AsyncSession,
    *,
    exchange_connection_id: uuid.UUID,
    live_trading_profile_id: uuid.UUID,
    key_prefix: str,
    max_order_notional_usd: Decimal = Decimal("5"),
    allowed_strategy_versions: tuple[str, ...] = (_STRATEGY_IDENTITY,),
    expires_at: datetime | None = None,
) -> AutonomousCapitalMandate:
    """Drives a mandate through the exact real lifecycle a human operator
    uses (SUBMIT_FOR_AUTHORIZATION -> version -> authorize -> ACTIVATE),
    mirroring tests/unit/services/mandates/test_mandate_lifecycle_real_session.py,
    so this test proves the wiring against a mandate that reached ACTIVE the
    same way a real one would -- not a hand-crafted row bypassing the
    lifecycle rules."""
    mandate = AutonomousCapitalMandate(
        mandate_id=uuid.uuid4(),
        owner_actor_id="operator:owner",
        status="DRAFT",
        autonomy_level="LEVEL_2",
        provider=_PROVIDER,
        exchange_environment=_ENVIRONMENT,
        exchange_connection_id=exchange_connection_id,
        live_trading_profile_id=live_trading_profile_id,
        paper_account_id=None,
        capital_campaign_id=None,
        expires_at=expires_at,
    )
    session.add(mandate)
    await session.flush()

    await lifecycle.apply_mandate_lifecycle_action(
        db=session,
        request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key=f"{key_prefix}-submit"),
    )
    version = await lifecycle.create_mandate_version(
        db=session,
        request=_version_request(
            mandate_id=mandate.mandate_id,
            idempotency_key=f"{key_prefix}-version",
            max_order_notional_usd=max_order_notional_usd,
            allowed_strategy_versions=allowed_strategy_versions,
        ),
    )
    await lifecycle.authorize_mandate_version(
        db=session,
        request=_authorization_request(mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id, idempotency_key=f"{key_prefix}-auth"),
    )
    await lifecycle.apply_mandate_lifecycle_action(
        db=session,
        request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key=f"{key_prefix}-activate"),
    )
    await session.refresh(mandate)
    return mandate


async def _seed_scope(session: AsyncSession, *, paper_account_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seeds the real chain _evaluate_level2_mandate_authorization actually
    resolves scope through: CryptoOrderPreview -> RiskEvent (via
    live_order.risk_event_id) -> LiveTradingProfile (via paper_account_id).
    Returns (exchange_connection_id, live_trading_profile_id, preview_id)."""
    exchange_connection_id = uuid.uuid4()
    session.add(
        PaperAccount(
            id=paper_account_id,
            owner_user_id=uuid.uuid4(),
            name="test-account",
            asset_class="crypto",
            starting_balance=Decimal("25"),
            current_cash_balance=Decimal("25"),
        )
    )
    session.add(RiskKillSwitch(scope="global", paper_account_id=None, engaged=False, rearm_required=False))
    session.add(RiskKillSwitch(scope="account", paper_account_id=paper_account_id, engaged=False, rearm_required=False))
    profile = LiveTradingProfile(
        id=uuid.uuid4(),
        paper_account_id=paper_account_id,
        provenance_metadata={"provider": _PROVIDER, "exchange_environment": _ENVIRONMENT},
    )
    session.add(profile)
    risk_event = RiskEvent(
        id=uuid.uuid4(),
        paper_account_id=paper_account_id,
        event_type="ORDER_APPROVAL",
        action_taken="APPROVE",
        detail={},
    )
    session.add(risk_event)
    decision_record_id = uuid.uuid4()
    session.add(
        DecisionSnapshot(
            decision_id=decision_record_id,
            timestamp=datetime.now(timezone.utc),
            asset={},
            exchange=_PROVIDER,
            timeframe="15m",
            ohlcv_context={},
            indicators={},
            generated_features={},
            market_regime={},
            volatility={},
            strategy_inputs={},
            risk_inputs={},
            open_trades={},
            portfolio_exposure={},
            parameter_set_version="baseline",
            strategy_version=_STRATEGY_IDENTITY,
            ai_model_version="n/a",
            decision_engine_version="n/a",
            configuration_version="n/a",
        )
    )
    preview = CryptoOrderPreview(
        crypto_order_preview_id=uuid.uuid4(),
        idempotency_key=f"preview-{uuid.uuid4()}",
        exchange_connection_id=exchange_connection_id,
        provider=_PROVIDER,
        environment=_ENVIRONMENT,
        product_id=_PRODUCT,
        side="BUY",
        order_type="MARKET",
        requested_amount=Decimal("5"),
        requested_amount_currency="USD",
        status="PREVIEW_READY",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        generated_by="system_recommendation",
        decision_record_id=decision_record_id,
    )
    session.add(preview)
    await session.flush()
    return exchange_connection_id, profile.id, preview.crypto_order_preview_id, risk_event.id


def _live_order(
    *,
    preview_id: uuid.UUID,
    risk_event_id: uuid.UUID,
    exchange_connection_id: uuid.UUID,
    requested_quote_size: Decimal = Decimal("5"),
    side: str = "BUY",
    risk_verdict: str = "approve",
) -> SimpleNamespace:
    """_evaluate_level2_mandate_authorization only ever reads plain
    attributes off live_order -- it never queries the live_crypto_orders
    table -- so a SimpleNamespace is a faithful, minimal stand-in."""
    return SimpleNamespace(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=preview_id,
        risk_event_id=risk_event_id,
        decision_record_id=uuid.uuid4(),
        exchange_connection_id=exchange_connection_id,
        provider=_PROVIDER,
        environment=_ENVIRONMENT,
        product_id=_PRODUCT,
        side=side,
        requested_quote_size=requested_quote_size,
        safe_provider_response={"execution_risk_verdict": risk_verdict},
    )


@pytest.mark.asyncio
async def test_valid_active_level2_mandate_authorizes_execution_automatically() -> None:
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="valid",
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is True
        assert mandate_id is not None


@pytest.mark.asyncio
async def test_no_mandate_falls_back_to_manual_confirmation_required() -> None:
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, _profile_id, preview_id, risk_event_id = await _seed_scope(session, paper_account_id=paper_account_id)
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is None


@pytest.mark.asyncio
async def test_expired_mandate_is_blocked() -> None:
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="expired",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is not None  # mandate was found, but eligibility rejected it


@pytest.mark.asyncio
async def test_disabled_mandate_is_blocked() -> None:
    """PAUSED/REVOKED/KILLED mandates fall outside _find_active_level2_mandate_for_scope's
    own status filter entirely (ACTIVE, EXIT_ONLY only) -- this proves that
    path, distinct from the eligibility-rejection path above."""
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        mandate = await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="disabled",
        )
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="PAUSE", idempotency_key="disabled-pause"),
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is None  # PAUSED mandate is not even found as a candidate


@pytest.mark.asyncio
async def test_account_mismatch_is_blocked() -> None:
    """A mandate scoped to a DIFFERENT live_trading_profile_id must never
    authorize an order for this account."""
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, _live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=uuid.uuid4(),  # unrelated profile
            key_prefix="mismatch",
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is None


@pytest.mark.asyncio
async def test_strategy_mismatch_is_blocked() -> None:
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        # _seed_scope links a real DecisionSnapshot with strategy_version=
        # _STRATEGY_IDENTITY ("ma_crossover@1.0.0"); authorizing only a
        # DIFFERENT, validly-formatted identity proves a genuine mismatch is
        # rejected, not a vacuous pass.
        other_strategy_identity = build_strategy_identity(slug="momentum", module_version="2.0.0")
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="strategy-mismatch",
            allowed_strategy_versions=(other_strategy_identity,),
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is not None


@pytest.mark.asyncio
async def test_capital_limit_exceeded_is_blocked() -> None:
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="capital-limit",
            max_order_notional_usd=Decimal("5"),
        )
        await session.commit()

        # Requesting more than the mandate's max_order_notional_usd.
        live_order = _live_order(
            preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id,
            requested_quote_size=Decimal("6"),
        )
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is not None


@pytest.mark.asyncio
async def test_risk_rejected_verdict_is_blocked() -> None:
    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="risk-rejected",
        )
        await session.commit()

        live_order = _live_order(
            preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id,
            risk_verdict="reject",
        )
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is not None


@pytest.mark.asyncio
async def test_mandate_evaluation_is_recorded_for_audit_trail() -> None:
    """evaluate_and_record_mandate persists a real AutonomousCapitalMandateEvaluation
    row plus an AuditLog row -- this proves the audit trail requirement is
    satisfied, not merely that a log line was printed."""
    from sqlalchemy import select as sa_select

    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        mandate = await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="audit",
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, _mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")
        assert authorized is True

        evaluations = list(
            await session.scalars(
                sa_select(AutonomousCapitalMandateEvaluation).where(AutonomousCapitalMandateEvaluation.mandate_id == mandate.mandate_id)
            )
        )
        assert len(evaluations) == 1
        assert evaluations[0].approval_result == "APPROVAL_SATISFIED_BY_ACTIVE_MANDATE"

        audit_rows = list(
            await session.scalars(
                sa_select(AuditLog).where(AuditLog.action == "MANDATE_EVALUATION_RECORDED", AuditLog.entity_id == mandate.mandate_id)
            )
        )
        assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_mandate_evaluation_error_falls_back_to_manual_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compatibility: an unexpected error while evaluating an
    otherwise-found mandate (e.g. missing kill-switch state) must degrade to
    "manual confirmation required" -- exactly the pre-existing behavior --
    never propagate and crash submit()."""
    import app.services.live_crypto_orders as module

    async def _boom(*, db, scope, account_id):
        raise PermissionError(f"{scope} kill switch state unavailable")

    monkeypatch.setattr(module, "_load_kill_switch_state", _boom)

    async with _real_session() as session:
        paper_account_id = uuid.uuid4()
        exchange_connection_id, live_trading_profile_id, preview_id, risk_event_id = await _seed_scope(
            session, paper_account_id=paper_account_id,
        )
        await _seed_active_level2_mandate(
            session,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            key_prefix="kill-switch-missing",
        )
        await session.commit()

        live_order = _live_order(preview_id=preview_id, risk_event_id=risk_event_id, exchange_connection_id=exchange_connection_id)
        authorized, mandate_id = await _evaluate_level2_mandate_authorization(db=session, live_order=live_order, actor="system:level2")

        assert authorized is False
        assert mandate_id is not None
