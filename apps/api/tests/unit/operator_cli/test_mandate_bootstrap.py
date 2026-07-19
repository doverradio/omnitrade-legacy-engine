from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.operator_cli.service as service
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.mandates.contracts import MandateLifecycleActionRequest
from tests.support.real_sqlite_session import real_sqlite_session

_BOOTSTRAP_TABLES = [
    AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__,
    AutonomousCapitalMandateAuthorization.__table__,
    AuditLog.__table__,
    ExchangeConnection.__table__,
    LiveTradingProfile.__table__,
    PaperAccount.__table__,
    CapitalCampaign.__table__,
]


class _SessionContext:
    """Mirrors AsyncSessionLocal()'s async-context-manager shape but never closes the
    underlying session, so the same real sqlite session can back multiple sequential
    `async with AsyncSessionLocal() as db:` blocks within one test (needed to exercise
    idempotent reruns and resume-after-failure against durable, already-committed state)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def __aenter__(self) -> AsyncSession:
        return self._db

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _BootstrapEnvironment:
    def __init__(self, *, db: AsyncSession, exchange_connection_id: uuid.UUID, live_trading_profile_id: uuid.UUID, paper_account_id: uuid.UUID) -> None:
        self.db = db
        self.exchange_connection_id = exchange_connection_id
        self.live_trading_profile_id = live_trading_profile_id
        self.paper_account_id = paper_account_id

    def kwargs(
        self,
        *,
        idempotency_key: str,
        capital_campaign_id: int | None = 2,
        confirm: bool = True,
        audit_correlation_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        return dict(
            owner_actor_id="operator:owner",
            autonomy_level="LEVEL_2",
            provider="kraken_spot",
            environment="production",
            exchange_connection_id=self.exchange_connection_id,
            live_trading_profile_id=self.live_trading_profile_id,
            paper_account_id=self.paper_account_id,
            capital_campaign_id=capital_campaign_id,
            mandate_expires_at=None,
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
            allowed_strategy_versions=("ma_crossover@1.0.0",),
            approval_policy="MANDATE_ALLOWED",
            entry_policy={},
            exit_policy={},
            cooldown_policy={},
            operating_schedule={},
            reconciliation_policy={},
            kill_switch_policy={},
            owner_acknowledgements={"accepted": True},
            authorization_evidence_summary={"source": "owner"},
            authorization_method="owner_signature",
            authorization_evidence={"signature": "hash"},
            deterministic_explanation={"reason": "explicit_owner_authorization"},
            authorization_expires_at=None,
            actor="operator:human",
            reason="campaign_2_bootstrap",
            idempotency_key=idempotency_key,
            audit_correlation_id=audit_correlation_id,
            confirm=confirm,
        )


@asynccontextmanager
async def _bootstrap_environment(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_BootstrapEnvironment]:
    """A real sqlite session with the mandate tables PLUS the exchange connection / live
    trading profile / paper account / capital campaign rows that create_mandate()'s
    _validate_relationships() genuinely checks for existence -- so mandate_bootstrap() here
    exercises the exact same validation the FastAPI route would run in production, not a
    hand-waved shortcut around it."""
    async with real_sqlite_session(_BOOTSTRAP_TABLES) as db:
        exchange_connection_id = uuid.uuid4()
        live_trading_profile_id = uuid.uuid4()
        paper_account_id = uuid.uuid4()

        db.add(
            ExchangeConnection(
                exchange_connection_id=exchange_connection_id,
                provider="kraken_spot",
                connection_name="kraken-campaign-2",
                environment="production",
                credentials_encrypted="encrypted",
                api_key_masked="****",
                api_secret_masked="****",
            )
        )
        db.add(
            PaperAccount(
                id=paper_account_id,
                owner_user_id=uuid.uuid4(),
                name="campaign-2-paper",
                asset_class="crypto",
                starting_balance=Decimal("25"),
                current_cash_balance=Decimal("25"),
            )
        )
        db.add(
            LiveTradingProfile(
                id=live_trading_profile_id,
                paper_account_id=paper_account_id,
                provenance_metadata={},
            )
        )
        db.add(
            CapitalCampaign(
                id=2,
                owner="operator:owner",
                name="Campaign 2",
                campaign_type="crypto",
                starting_capital=Decimal("25"),
                current_equity=Decimal("25"),
            )
        )
        await db.flush()
        await db.commit()

        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))

        yield _BootstrapEnvironment(
            db=db,
            exchange_connection_id=exchange_connection_id,
            live_trading_profile_id=live_trading_profile_id,
            paper_account_id=paper_account_id,
        )


@pytest.mark.asyncio
async def test_mandate_bootstrap_succeeds_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _bootstrap_environment(monkeypatch) as env:
        result = await service.mandate_bootstrap(**env.kwargs(idempotency_key="bootstrap-1"))

        assert result["status"] == "ACTIVE"
        assert result["capital_campaign_id"] == 2
        assert result["governing_version_is_authorized"] is True
        assert result["governing_version_is_active"] is True
        assert [item["stage"] for item in result["stages"]] == [
            "create_mandate",
            "create_mandate_version",
            "submit_for_authorization",
            "authorize_version",
            "activate_mandate",
        ]

        mandates = (await env.db.execute(select(AutonomousCapitalMandate))).scalars().all()
        versions = (await env.db.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        authorizations = (await env.db.execute(select(AutonomousCapitalMandateAuthorization))).scalars().all()
        assert len(mandates) == 1
        assert len(versions) == 1
        assert len(authorizations) == 1
        assert mandates[0].capital_campaign_id == 2
        assert versions[0].is_authorized is True
        assert versions[0].is_active is True


@pytest.mark.asyncio
async def test_mandate_bootstrap_rejects_without_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _bootstrap_environment(monkeypatch) as env:
        with pytest.raises(PermissionError, match="confirm"):
            await service.mandate_bootstrap(**env.kwargs(idempotency_key="bootstrap-noconfirm", confirm=False))

        mandates = (await env.db.execute(select(AutonomousCapitalMandate))).scalars().all()
        assert mandates == []


@pytest.mark.asyncio
async def test_mandate_bootstrap_idempotent_rerun_creates_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _bootstrap_environment(monkeypatch) as env:
        kwargs = env.kwargs(idempotency_key="bootstrap-idempotent")

        first = await service.mandate_bootstrap(**kwargs)
        second = await service.mandate_bootstrap(**kwargs)

        assert first["mandate_id"] == second["mandate_id"]
        assert first["mandate_version_id"] == second["mandate_version_id"]
        assert first["mandate_authorization_id"] == second["mandate_authorization_id"]
        assert second["status"] == "ACTIVE"

        mandates = (await env.db.execute(select(AutonomousCapitalMandate))).scalars().all()
        versions = (await env.db.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        authorizations = (await env.db.execute(select(AutonomousCapitalMandateAuthorization))).scalars().all()
        assert len(mandates) == 1
        assert len(versions) == 1
        assert len(authorizations) == 1


@pytest.mark.asyncio
async def test_mandate_bootstrap_authorization_failure_reports_stage_and_preserves_prior_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _bootstrap_environment(monkeypatch) as env:

        async def _failing_authorize(*, db, request):
            _ = db, request
            raise RuntimeError("authorization evidence rejected")

        monkeypatch.setattr(service, "authorize_mandate_version", _failing_authorize)

        with pytest.raises(service.MandateBootstrapStageError) as excinfo:
            await service.mandate_bootstrap(**env.kwargs(idempotency_key="bootstrap-auth-fail"))

        error = excinfo.value
        assert error.stage == "authorize_version"
        assert [item["stage"] for item in error.completed_stages] == [
            "create_mandate",
            "create_mandate_version",
            "submit_for_authorization",
        ]
        assert "authorize_version" in str(error)

        mandates = (await env.db.execute(select(AutonomousCapitalMandate))).scalars().all()
        versions = (await env.db.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        assert len(mandates) == 1
        assert mandates[0].status == "PENDING_AUTHORIZATION"
        assert len(versions) == 1
        assert versions[0].is_authorized is False
        assert versions[0].is_active is False


@pytest.mark.asyncio
async def test_mandate_bootstrap_activation_failure_reports_stage_and_preserves_prior_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _bootstrap_environment(monkeypatch) as env:
        real_apply_action = service.apply_mandate_lifecycle_action

        async def _fail_only_on_activate(*, db, request: MandateLifecycleActionRequest):
            if request.action == "ACTIVATE":
                raise RuntimeError("activation blocked")
            return await real_apply_action(db=db, request=request)

        monkeypatch.setattr(service, "apply_mandate_lifecycle_action", _fail_only_on_activate)

        with pytest.raises(service.MandateBootstrapStageError) as excinfo:
            await service.mandate_bootstrap(**env.kwargs(idempotency_key="bootstrap-activate-fail"))

        error = excinfo.value
        assert error.stage == "activate_mandate"
        assert [item["stage"] for item in error.completed_stages] == [
            "create_mandate",
            "create_mandate_version",
            "submit_for_authorization",
            "authorize_version",
        ]

        mandates = (await env.db.execute(select(AutonomousCapitalMandate))).scalars().all()
        versions = (await env.db.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        assert mandates[0].status == "AUTHORIZED"
        assert versions[0].is_authorized is True
        assert versions[0].is_active is False


@pytest.mark.asyncio
async def test_mandate_bootstrap_resumes_successfully_after_earlier_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the applicable 'rollback' behavior for this saga-style orchestrator: each
    stage's own transaction is atomic (an audit failure inside authorize_mandate_version
    rolls that one stage back), but there is no cross-stage undo -- and none is needed,
    because rerunning with the same idempotency_key resumes correctly and produces no
    duplicate mandate/version/authorization rows, exactly like the FastAPI routes would
    if an operator retried a failed request with the same Idempotency-Key."""
    async with _bootstrap_environment(monkeypatch) as env:
        kwargs = env.kwargs(idempotency_key="bootstrap-resume")

        async def _failing_authorize(*, db, request):
            _ = db, request
            raise RuntimeError("transient evidence store outage")

        monkeypatch.setattr(service, "authorize_mandate_version", _failing_authorize)
        with pytest.raises(service.MandateBootstrapStageError):
            await service.mandate_bootstrap(**kwargs)

        monkeypatch.undo()  # reverts BOTH the authorize_mandate_version patch and the AsyncSessionLocal patch
        monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(env.db))

        result = await service.mandate_bootstrap(**kwargs)

        assert result["status"] == "ACTIVE"
        assert result["governing_version_is_authorized"] is True
        assert result["governing_version_is_active"] is True

        mandates = (await env.db.execute(select(AutonomousCapitalMandate))).scalars().all()
        versions = (await env.db.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        authorizations = (await env.db.execute(select(AutonomousCapitalMandateAuthorization))).scalars().all()
        assert len(mandates) == 1
        assert len(versions) == 1
        assert len(authorizations) == 1


@pytest.mark.asyncio
async def test_mandate_bootstrap_propagates_one_audit_correlation_id_through_every_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _bootstrap_environment(monkeypatch) as env:
        correlation_id = uuid.uuid4()

        result = await service.mandate_bootstrap(
            **env.kwargs(idempotency_key="bootstrap-audit", audit_correlation_id=correlation_id)
        )

        assert result["audit_correlation_id"] == str(correlation_id)

        mandate_id = uuid.UUID(result["mandate_id"])
        audit_rows = (
            (
                await env.db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "autonomous_capital_mandate",
                        AuditLog.entity_id == mandate_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        actions = {row.action for row in audit_rows}
        assert actions == {
            "MANDATE_CREATED",
            "MANDATE_VERSION_CREATED",
            "MANDATE_SUBMIT_FOR_AUTHORIZATION",
            "MANDATE_VERSION_AUTHORIZED",
            "MANDATE_ACTIVATE",
        }
        # create_mandate() takes no audit_correlation_id parameter at all (a pre-existing
        # trait of that lifecycle function, unrelated to this orchestrator -- reusing it
        # unmodified means MANDATE_CREATED's audit row cannot carry one). Every stage that
        # *can* accept audit_correlation_id must actually receive the shared one.
        for row in audit_rows:
            if row.action == "MANDATE_CREATED":
                assert "audit_correlation_id" not in row.after_state
                continue
            assert row.after_state.get("audit_correlation_id") == str(correlation_id)

        authorization = (
            await env.db.execute(
                select(AutonomousCapitalMandateAuthorization).where(
                    AutonomousCapitalMandateAuthorization.mandate_id == mandate_id
                )
            )
        ).scalar_one()
        assert authorization.audit_correlation_id == correlation_id

        expected_idempotency_keys = {
            "MANDATE_CREATED": "bootstrap-audit:create-mandate",
            "MANDATE_VERSION_CREATED": "bootstrap-audit:create-version",
            "MANDATE_SUBMIT_FOR_AUTHORIZATION": "bootstrap-audit:submit-for-authorization",
            "MANDATE_ACTIVATE": "bootstrap-audit:activate",
        }
        for row in audit_rows:
            if row.action in expected_idempotency_keys:
                assert row.after_state.get("idempotency_key") == expected_idempotency_keys[row.action]
        assert authorization.idempotency_key == "bootstrap-audit:authorize"


def test_orchestration_stage_logging_format_unchanged_for_canonical_proving_commission(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pins the exact stderr line format canonical-proving-commission has always emitted,
    proving the _log_commission_stage/_await_db_operation refactor (generalized to support
    mandate-bootstrap) changed nothing observable for the existing command."""
    service._commission_stage_sequence_by_key.clear()

    service._log_commission_stage(stage="mandate_lookup", status="started", root_idempotency_key="root-1", package_id="pkg-1")

    captured = capsys.readouterr()
    assert captured.err.strip() == (
        "[canonical-proving-commission] [1] stage=mandate_lookup status=started root_idempotency_key=root-1 package_id=pkg-1"
    )
