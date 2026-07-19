from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.services.mandates import lifecycle
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
)
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(
        [
            AutonomousCapitalMandate.__table__,
            AutonomousCapitalMandateVersion.__table__,
            AutonomousCapitalMandateAuthorization.__table__,
            AuditLog.__table__,
        ]
    ) as session:
        yield session


_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")


async def _seed_mandate(session: AsyncSession, *, status: str = "DRAFT") -> AutonomousCapitalMandate:
    mandate = AutonomousCapitalMandate(
        mandate_id=uuid.uuid4(),
        owner_actor_id="operator:owner",
        status=status,
        autonomy_level="LEVEL_2",
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=None,
    )
    session.add(mandate)
    await session.flush()
    return mandate


def _version_request(*, mandate_id: uuid.UUID, idempotency_key: str) -> MandateVersionCreateRequest:
    return MandateVersionCreateRequest(
        mandate_id=mandate_id,
        actor="operator:owner",
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("10"),
        max_daily_deployed_usd=Decimal("10"),
        max_daily_realized_loss_usd=Decimal("3"),
        max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=2,
        position_limit=1,
        price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("25"),
        max_fee_bps=Decimal("10"),
        allowed_products=("BTC-USD",),
        allowed_order_sides=("BUY", "SELL", "HOLD"),
        allowed_strategy_versions=(_STRATEGY_IDENTITY,),
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


def _authorization_request(
    *, mandate_id: uuid.UUID, mandate_version_id: uuid.UUID, idempotency_key: str
) -> MandateAuthorizationRequest:
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


def _lifecycle_request(
    *, mandate_id: uuid.UUID, action: str, idempotency_key: str
) -> MandateLifecycleActionRequest:
    return MandateLifecycleActionRequest(
        mandate_id=mandate_id,
        actor="operator:owner",
        action=action,
        reason=f"test:{action.lower()}",
        idempotency_key=idempotency_key,
        audit_correlation_id=uuid.uuid4(),
        software_build_version="build-1",
    )


async def _submit_and_authorize(
    session: AsyncSession, *, mandate: AutonomousCapitalMandate, key_prefix: str
) -> AutonomousCapitalMandateVersion:
    """Drive a fresh DRAFT mandate through SUBMIT_FOR_AUTHORIZATION + version
    creation + authorization, mirroring the real operator/API call sequence."""
    await lifecycle.apply_mandate_lifecycle_action(
        db=session,
        request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key=f"{key_prefix}-submit"),
    )
    version = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key=f"{key_prefix}-version"))
    await lifecycle.authorize_mandate_version(
        db=session,
        request=_authorization_request(mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id, idempotency_key=f"{key_prefix}-auth"),
    )
    await session.refresh(version)
    return version


@pytest.mark.asyncio
async def test_authorization_sets_only_the_exact_version_authorized() -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key="k-submit"),
        )
        version_a = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key="k-version-a"))
        version_b = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key="k-version-b"))

        await lifecycle.authorize_mandate_version(
            db=session,
            request=_authorization_request(mandate_id=mandate.mandate_id, mandate_version_id=version_a.mandate_version_id, idempotency_key="k-auth-a"),
        )

        await session.refresh(version_a)
        await session.refresh(version_b)

        assert version_a.is_authorized is True
        assert version_a.authorized_at is not None
        assert version_b.is_authorized is False
        assert version_b.authorized_at is None


@pytest.mark.asyncio
async def test_activation_sets_the_correct_governing_version_active() -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key="k-submit"),
        )
        version_a = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key="k-version-a"))
        version_b = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key="k-version-b"))
        await lifecycle.authorize_mandate_version(
            db=session,
            request=_authorization_request(mandate_id=mandate.mandate_id, mandate_version_id=version_a.mandate_version_id, idempotency_key="k-auth-a"),
        )

        activated = await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="k-activate"),
        )

        await session.refresh(version_a)
        await session.refresh(version_b)

        assert activated.status == "ACTIVE"
        assert version_a.is_active is True
        assert version_b.is_active is False


@pytest.mark.asyncio
async def test_unauthorized_activation_remains_rejected() -> None:
    async with _real_session() as session:
        # A mandate can only reach a status from which ACTIVATE/RESUME is a legal
        # transition (AUTHORIZED/PAUSED/EXIT_ONLY) by first going through
        # authorize_mandate_version(), which atomically requires a real authorized
        # version. To prove the activation-time governing-version gate itself (not
        # just the state-transition table), seed a PAUSED mandate directly with no
        # authorized version at all and attempt RESUME.
        mandate = await _seed_mandate(session, status="PAUSED")

        with pytest.raises(InvalidRequestError):
            await lifecycle.apply_mandate_lifecycle_action(
                db=session,
                request=_lifecycle_request(mandate_id=mandate.mandate_id, action="RESUME", idempotency_key="k-resume-unauthorized"),
            )

        await session.refresh(mandate)
        assert mandate.status == "PAUSED"


@pytest.mark.asyncio
async def test_duplicate_authorization_and_activation_are_idempotent() -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key="k-submit"),
        )
        version = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key="k-version"))

        auth_request = _authorization_request(mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id, idempotency_key="k-auth")
        first_auth = await lifecycle.authorize_mandate_version(db=session, request=auth_request)
        second_auth = await lifecycle.authorize_mandate_version(db=session, request=auth_request)
        assert first_auth.mandate_authorization_id == second_auth.mandate_authorization_id

        authorization_count = (
            await session.execute(select(AutonomousCapitalMandateAuthorization).where(AutonomousCapitalMandateAuthorization.mandate_id == mandate.mandate_id))
        ).scalars().all()
        assert len(authorization_count) == 1

        activate_request = _lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="k-activate")
        first_activation = await lifecycle.apply_mandate_lifecycle_action(db=session, request=activate_request)
        second_activation = await lifecycle.apply_mandate_lifecycle_action(db=session, request=activate_request)
        assert first_activation.status == "ACTIVE"
        assert second_activation.status == "ACTIVE"

        await session.refresh(version)
        assert version.is_active is True

        activation_audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.entity_id == mandate.mandate_id, AuditLog.action == "MANDATE_ACTIVATE")
            )
        ).scalars().all()
        assert len(activation_audit_rows) == 1


@pytest.mark.asyncio
async def test_pause_cannot_leave_a_falsely_active_governing_version() -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="pause")
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="pause-activate"),
        )
        await session.refresh(version)
        assert version.is_active is True

        paused = await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="PAUSE", idempotency_key="pause-pause"),
        )
        await session.refresh(version)
        assert paused.status == "PAUSED"
        assert version.is_active is False

        resumed = await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="RESUME", idempotency_key="pause-resume"),
        )
        await session.refresh(version)
        assert resumed.status == "ACTIVE"
        assert version.is_active is True


@pytest.mark.asyncio
async def test_revocation_cannot_leave_a_falsely_active_governing_version() -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="revoke")
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="revoke-activate"),
        )

        revoked = await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="REVOKE", idempotency_key="revoke-revoke"),
        )
        await session.refresh(version)
        assert revoked.status == "REVOKED"
        assert version.is_active is False
        # is_authorized is immutable authorization/audit evidence -- revocation must
        # not erase the historical record that this version was once authorized.
        assert version.is_authorized is True


@pytest.mark.asyncio
async def test_expiration_cannot_leave_a_falsely_active_governing_version() -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="expire")
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="expire-activate"),
        )

        expired = await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="EXPIRE", idempotency_key="expire-expire"),
        )
        await session.refresh(version)
        assert expired.status == "EXPIRED"
        assert version.is_active is False


@pytest.mark.asyncio
async def test_active_mandate_always_has_an_authorized_and_active_governing_version() -> None:
    """Direct regression test for the diagnosed production defect: mandate_id
    5a628191-a6de-4283-bc6d-fd6df0e89a74 reached status=ACTIVE while its governing
    version (3522e58a-3a70-4847-9278-44ca3800dca1) had is_authorized=False and
    is_active=False. With the fix, an ACTIVE mandate's governing version must
    always satisfy both flags -- the same two checks
    canonical_proving_commission_bundle() enforces at its mandate_version_lookup
    stage."""
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="invariant")
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="invariant-activate"),
        )
        await session.refresh(mandate)
        await session.refresh(version)

        assert mandate.status == "ACTIVE"
        assert version.is_authorized is True
        assert version.is_active is True


@pytest.mark.asyncio
async def test_commissioning_can_resolve_a_legitimately_authorized_and_active_version() -> None:
    """Mirrors the exact gate canonical_proving_commission_bundle() runs at its
    mandate_version_lookup stage (apps/api/app/operator_cli/service.py:6071-6074)."""
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="commission")
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="commission-activate"),
        )

        governing_version = await lifecycle._load_governing_authorized_version(db=session, mandate_id=mandate.mandate_id)

        assert governing_version is not None
        assert governing_version.mandate_id == mandate.mandate_id
        assert bool(governing_version.is_authorized) is True
        assert bool(governing_version.is_active) is True


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_authorization_version_state_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        await lifecycle.apply_mandate_lifecycle_action(
            db=session,
            request=_lifecycle_request(mandate_id=mandate.mandate_id, action="SUBMIT_FOR_AUTHORIZATION", idempotency_key="k-submit"),
        )
        version = await lifecycle.create_mandate_version(db=session, request=_version_request(mandate_id=mandate.mandate_id, idempotency_key="k-version"))
        assert version.is_authorized is False

        real_commit = session.commit

        async def _failing_commit() -> None:
            await session.flush()
            raise RuntimeError("audit write failed")

        monkeypatch.setattr(session, "commit", _failing_commit)

        with pytest.raises(RuntimeError, match="audit write failed"):
            await lifecycle.authorize_mandate_version(
                db=session,
                request=_authorization_request(mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id, idempotency_key="k-auth"),
            )

        monkeypatch.setattr(session, "commit", real_commit)
        await session.rollback()
        await session.refresh(version)
        await session.refresh(mandate)

        assert version.is_authorized is False
        assert version.authorized_at is None
        assert mandate.status == "PENDING_AUTHORIZATION"

        authorization_rows = (
            await session.execute(select(AutonomousCapitalMandateAuthorization).where(AutonomousCapitalMandateAuthorization.mandate_id == mandate.mandate_id))
        ).scalars().all()
        assert authorization_rows == []


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_activation_version_state_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="activate-rollback")

        real_commit = session.commit

        async def _failing_commit() -> None:
            await session.flush()
            raise RuntimeError("audit write failed")

        monkeypatch.setattr(session, "commit", _failing_commit)

        with pytest.raises(RuntimeError, match="audit write failed"):
            await lifecycle.apply_mandate_lifecycle_action(
                db=session,
                request=_lifecycle_request(mandate_id=mandate.mandate_id, action="ACTIVATE", idempotency_key="activate-rollback-activate"),
            )

        monkeypatch.setattr(session, "commit", real_commit)
        await session.rollback()
        await session.refresh(version)
        await session.refresh(mandate)

        assert mandate.status == "AUTHORIZED"
        assert version.is_active is False


@pytest.mark.asyncio
async def test_authorized_version_economic_terms_remain_immutable_after_authorization() -> None:
    """The is_active/is_authorized bookkeeping fix must not weaken the existing
    immutability guarantee for the authorized economic terms themselves."""
    async with _real_session() as session:
        mandate = await _seed_mandate(session)
        version = await _submit_and_authorize(session, mandate=mandate, key_prefix="immutable")

        version.max_order_notional_usd = Decimal("999")
        with pytest.raises(ValueError, match="authorized mandate versions are immutable"):
            await session.flush()
