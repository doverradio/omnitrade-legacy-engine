from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.services.capital_campaign_orchestration.service import build_campaign_orchestration_idempotency_key, fetch_campaign_orchestration_history, fetch_campaign_orchestration_readiness, fetch_campaign_orchestration_status
from app.services.risk import RiskDecisionAction, RiskEvaluationResult


class _FakeDb:
    async def scalar(self, _statement):
        return None

    async def execute(self, _statement):
        raise AssertionError("unexpected execute call in readiness test")


class _OrchestrationReadOnlyDb:
    def __init__(self, *, definition, runtime=None, asset=None, cycles=None) -> None:
        self.definition = definition
        self.runtime = runtime
        self.asset = asset
        self.cycles = cycles or []

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM capital_campaign_definitions" in sql:
            return self.definition
        if "FROM capital_campaigns" in sql:
            return self.runtime
        if "FROM assets" in sql:
            return self.asset
        if "FROM autonomous_cycle_runs" in sql:
            return self.cycles[0] if self.cycles else None
        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM capital_campaign_definitions" in sql:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [self.definition]))
        if "FROM autonomous_cycle_runs" in sql:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.cycles)))
        raise AssertionError(f"unexpected execute call: {sql}")


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def test_campaign_idempotency_key_changes_with_version() -> None:
    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    close_time = datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc)
    first = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD"],
        execution_mode="preview",
    )
    second = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=2,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD"],
        execution_mode="preview",
    )
    assert first != second


def test_campaign_idempotency_key_changes_with_instrument_set_and_execution_mode() -> None:
    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    close_time = datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc)
    baseline = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD", "ETH-USD"],
        execution_mode="preview",
    )
    different_instruments = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD"],
        execution_mode="preview",
    )
    different_mode = build_campaign_orchestration_idempotency_key(
        campaign_id=campaign_id,
        version=1,
        trigger="kraken_btc_15m_candle_close",
        candle_close_time=close_time,
        eligible_instruments=["BTC-USD", "ETH-USD"],
        execution_mode="replay",
    )

    assert baseline != different_instruments
    assert baseline != different_mode


@pytest.mark.asyncio
async def test_campaign_readiness_accepts_draft_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="DRAFT",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={"policy_type": "FIXED_CAPITAL"},
        metadata_evidence={},
    )
    runtime = SimpleNamespace(id=1, exchange="kraken_spot", paper_account_id=None, updated_at=datetime.now(timezone.utc), uuid=campaign.campaign_id)

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=campaign, runtime=runtime, asset=None),
        campaign_id=campaign.campaign_id,
        version=campaign.version,
    )
    assert payload["items"][0]["ready"] is True
    assert payload["items"][0]["allows_draft_preview"] is True


@pytest.mark.asyncio
async def test_campaign_readiness_blocks_legacy_compounding_policy_without_policy_type() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={},
        metadata_evidence={"commissioned_seed_campaign": {"state": "READY"}},
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), updated_at=datetime.now(timezone.utc), uuid=definition.campaign_id)
    asset = SimpleNamespace(id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), symbol="BTC", exchange="kraken_spot", created_at=datetime.now(timezone.utc))

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=asset),
        campaign_id=definition.campaign_id,
        version=definition.version,
    )

    item = payload["items"][0]
    assert item["ready"] is False
    assert item["blockers"] == ["compounding_policy_missing_policy_type"]
    assert item["campaign_snapshot"]["compounding_policy_compatibility"]["status"] == "legacy_missing_policy_type"


@pytest.mark.asyncio
async def test_campaign_snapshot_recognizes_legacy_top_level_commissioned_metadata() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={"policy_type": "REINVEST_ALL_NET_PROFIT"},
        metadata_evidence={
            "state": "READY",
            "authority_metadata": {"commissioned_by": "operator:eric"},
        },
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=None, updated_at=datetime.now(timezone.utc), uuid=definition.campaign_id)

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=None),
        campaign_id=definition.campaign_id,
        version=definition.version,
    )

    snapshot = payload["items"][0]["campaign_snapshot"]
    assert snapshot["commissioned_metadata_present"] is True
    assert snapshot["commissioned_metadata_shape"] == "legacy_top_level_commissioned_fields"


@pytest.mark.asyncio
async def test_campaign_snapshot_does_not_treat_unrelated_metadata_as_commissioned() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={"policy_type": "REINVEST_ALL_NET_PROFIT"},
        metadata_evidence={"dedicated_proving_account": {"paper_account_id": "x"}},
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=None, updated_at=datetime.now(timezone.utc), uuid=definition.campaign_id)

    payload = await fetch_campaign_orchestration_readiness(
        db=_OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=None),
        campaign_id=definition.campaign_id,
        version=definition.version,
    )

    snapshot = payload["items"][0]["campaign_snapshot"]
    assert snapshot["commissioned_metadata_present"] is False
    assert snapshot["commissioned_metadata_shape"] is None


@pytest.mark.asyncio
async def test_campaign_status_and_history_surface_legacy_policy_blocker_without_validation_failure() -> None:
    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    definition = SimpleNamespace(
        campaign_id=campaign_id,
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy={},
        metadata_evidence={"commissioned_seed_campaign": {"state": "READY"}},
        created_at=datetime.now(timezone.utc),
    )
    runtime = SimpleNamespace(id=7, exchange="kraken_spot", paper_account_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), updated_at=datetime.now(timezone.utc), uuid=campaign_id)
    asset = SimpleNamespace(id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), symbol="BTC", exchange="kraken_spot", created_at=datetime.now(timezone.utc))
    cycle = SimpleNamespace(
        cycle_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        state="FAILED_CLOSED",
        cycle_kind="campaign",
        capital_campaign_id=campaign_id,
        capital_campaign_version=1,
        started_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 16, 12, 1, tzinfo=timezone.utc),
        termination_stage="policy_validation",
        failure_reason="legacy_compounding_policy_payload",
        deterministic_explanation=[],
        cycle_context={},
    )
    db = _OrchestrationReadOnlyDb(definition=definition, runtime=runtime, asset=asset, cycles=[cycle])

    status_payload = await fetch_campaign_orchestration_status(db=db, campaign_id=campaign_id, version=1)
    history_payload = await fetch_campaign_orchestration_history(db=db, campaign_id=campaign_id, version=1, limit=50)

    assert status_payload["ready"] is False
    assert status_payload["blockers"] == ["compounding_policy_missing_policy_type"]
    assert status_payload["campaign_snapshot"]["paper_account_id"] == str(runtime.paper_account_id)
    assert history_payload["blockers"] == ["compounding_policy_missing_policy_type"]
    assert history_payload["items"][0]["failure_reason"] == "legacy_compounding_policy_payload"


@pytest.mark.asyncio
async def test_campaign_status_rejects_malformed_compounding_policy_payload_fail_closed() -> None:
    definition = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        owner_identity="operator:human",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        compounding_policy="not-a-dict",
        metadata_evidence={},
        created_at=datetime.now(timezone.utc),
    )
    db = _OrchestrationReadOnlyDb(definition=definition, runtime=None, asset=None, cycles=[])

    payload = await fetch_campaign_orchestration_status(db=db, campaign_id=definition.campaign_id, version=1)

    assert payload["ready"] is False
    assert payload["blockers"] == ["compounding_policy_invalid_payload"]


@pytest.mark.asyncio
async def test_worker_preview_ignores_draft_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="DRAFT",
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        campaign_modes=[],
        aggression_mode="BALANCED",
        accounting_state=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        remaining_unallocated_capital=Decimal("1"),
    )

    class _CandleDb(_FakeDb):
        def __init__(self) -> None:
            super().__init__()
            self.scalar_calls = 0

        async def scalar(self, _statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return SimpleNamespace(id=UUID("12345678-1234-1234-1234-1234567890ab"))
            return SimpleNamespace(
                asset_id=UUID("12345678-1234-1234-1234-1234567890ab"),
                open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
                close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc),
            )

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        async def commit(self):
            return None

    async def _get_campaign_definition(**_kwargs):
        return campaign

    async def _list_campaign_definitions(**_kwargs):
        return SimpleNamespace(items=[campaign])

    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.get_campaign_definition", _get_campaign_definition)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.list_campaign_definitions", _list_campaign_definitions)

    payload = await run_campaign_orchestration_preview_for_candle(db=_CandleDb(), campaign_id=campaign.campaign_id, version=campaign.version, allow_draft_preview=False)
    assert payload["cycle_count"] == 0


@pytest.mark.asyncio
async def test_worker_preview_persists_null_mandate_and_campaign_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle
    from app.models.autonomous_cycle_run import AutonomousCycleRun

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=7,
        status="READY",
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        campaign_modes=[],
        aggression_mode="BALANCED",
        accounting_state=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        remaining_unallocated_capital=Decimal("1"),
    )

    class _CandleDb(_FakeDb):
        def __init__(self) -> None:
            super().__init__()
            self.scalar_calls = 0
            self.added = None

        async def scalar(self, _statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return SimpleNamespace(id=UUID("12345678-1234-1234-1234-1234567890ab"))
            if self.scalar_calls == 2:
                return SimpleNamespace(asset_id=UUID("12345678-1234-1234-1234-1234567890ab"), open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc))
            return None

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, item):
            self.added = item

        async def flush(self):
            return None

        async def commit(self):
            return None

    async def _get_campaign_definition(**_kwargs):
        return campaign

    async def _list_campaign_definitions(**_kwargs):
        return SimpleNamespace(items=[campaign])

    async def _compose_campaign_authoritative_cycle(**_kwargs):
        return SimpleNamespace(
            composition={
                "failed_closed": False,
                "decision_record_id": "11111111-1111-1111-1111-111111111111",
                "selected_decision": {"decision_kind": "NO_ACTION", "risk_verdict": "NOT_APPLICABLE"},
                "deterministic_explanation": ["stub"],
            },
            preview=SimpleNamespace(model_dump=lambda **_dump_kwargs: {"campaign": "preview"}),
        )

    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.get_campaign_definition", _get_campaign_definition)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.list_campaign_definitions", _list_campaign_definitions)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.compose_campaign_authoritative_cycle", _compose_campaign_authoritative_cycle)

    db = _CandleDb()
    payload = await run_campaign_orchestration_preview_for_candle(db=db, campaign_id=campaign.campaign_id, version=campaign.version, allow_draft_preview=False)

    assert payload["cycle_count"] == 1
    assert isinstance(db.added, AutonomousCycleRun)
    assert db.added.mandate_id is None
    assert db.added.mandate_version_id is None
    assert db.added.cycle_kind == "campaign"
    assert db.added.capital_campaign_id == campaign.campaign_id
    assert db.added.capital_campaign_version == campaign.version
    assert str(db.added.decision_record_id) == "11111111-1111-1111-1111-111111111111"


# --- candle ORM expiration across the scorecard-timeout rollback boundary ---
#
# Production evidence: after the proposal/aggregate_row/decision_record
# snapshot fixes in authoritative.py, a later cycle still crashed with
# MissingGreenlet, one step further out. Root cause: `candle`
# (_resolve_latest_btc_candle, a real Candle ORM row) is loaded in THIS
# module, in run_campaign_orchestration_preview_for_candle, before the
# per-campaign loop -- and compose_campaign_authoritative_cycle can trigger
# a scorecard-fetch timeout deep inside strategy aggregate resolution whose
# recovery, if the health probe also fails, runs a real session rollback.
# That expires every ORM instance the session still tracks, including
# `candle`, loaded well before compose_campaign_authoritative_cycle was ever
# called. candle.close_time (idempotency key) and candle.asset_id/
# .open_time/.close_time (cycle_context) were read AFTER the composition
# call returns, in plain synchronous code -- exactly the shape that raises
# MissingGreenlet outside SQLAlchemy's asyncio greenlet bridge. These tests
# use a real SQLAlchemy session (SQLite) so the expiration is genuine, not
# assumed, and directly fake only compose_campaign_authoritative_cycle
# (forcing the real rollback it would trigger deep inside) to isolate this
# function's own bug from the already-covered authoritative.py internals.


def _install_sqlite_uuid_and_jsonb_compilers() -> None:
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles

    @compiles(PG_UUID, "sqlite")
    def _compile_uuid_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "CHAR(36)"

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
        return "JSON"


_install_sqlite_uuid_and_jsonb_compilers()


def _fix_sqlite_server_defaults(table) -> None:  # noqa: ANN001
    from sqlalchemy import text as _text
    from sqlalchemy.schema import DefaultClause
    from sqlalchemy.sql.elements import TextClause

    for column in table.columns:
        default = column.server_default
        if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
            raw = default.arg.text.strip().split("::", 1)[0]
            if raw.endswith("()") and not raw.startswith("("):
                raw = f"({raw})"
            column.server_default = DefaultClause(_text(raw))


@asynccontextmanager
async def _real_candle_session():
    """A genuinely async SQLAlchemy session (AsyncSession + aiosqlite), not a
    sync Session wrapped in async-shaped methods. This matters: a sync
    Session has no greenlet bridge, so touching an expired attribute after
    rollback() just triggers an ordinary synchronous re-SELECT and silently
    succeeds either way -- it cannot reproduce MissingGreenlet, which is
    specifically raised when SQLAlchemy's asyncio bridge tries to run an
    implicit lazy-load outside of an awaited/greenlet_spawn context. Only a
    real AsyncSession can prove this regression test actually fails without
    the fix and passes with it."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.models.asset import Asset as AssetModel
    from app.models.autonomous_cycle_run import AutonomousCycleRun as AutonomousCycleRunModel
    from app.models.candle import Candle as CandleModel

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_now(dbapi_conn, _record) -> None:  # noqa: ANN001
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid4().hex)

    tables = [AssetModel.__table__, CandleModel.__table__, AutonomousCycleRunModel.__table__]
    for table in tables:
        _fix_sqlite_server_defaults(table)

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: AssetModel.metadata.create_all(sync_conn, tables=tables))

    try:
        yield engine
    finally:
        await engine.dispose()


class _CountingAsyncSession(AsyncSession):
    """A real AsyncSession that counts rollback() calls, so the test can
    assert the escalation path's real rollback actually ran."""

    rollback_calls: int

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "rollback_calls", 0)

    async def rollback(self) -> None:
        object.__setattr__(self, "rollback_calls", self.rollback_calls + 1)
        await super().rollback()


async def _seed_asset_and_candle(engine, *, asset_id: UUID, candle_id: int = 1) -> None:  # noqa: ANN001
    from app.models.asset import Asset as AssetModel
    from app.models.candle import Candle as CandleModel

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        session.add(
            AssetModel(
                id=asset_id,
                symbol="BTC",
                asset_class="crypto",
                exchange="kraken_spot",
                base_currency="USD",
                supports_fractional=True,
                min_order_notional=Decimal("5"),
                qty_step_size=Decimal("0.0001"),
                is_active=True,
                created_at=now,
            )
        )
        session.add(
            CandleModel(
                id=candle_id,
                asset_id=asset_id,
                interval="15m",
                open_time=now - timedelta(minutes=15),
                close_time=now,
                open=Decimal("66000"),
                high=Decimal("66100"),
                low=Decimal("65900"),
                close=Decimal("66050"),
                volume=Decimal("1.5"),
                source="kraken",
                created_at=now,
            )
        )
        await session.commit()


def _hold_veto_composition() -> dict[str, Any]:
    return {
        "failed_closed": False,
        "decision_record_id": None,
        "termination_stage": "hold_no_package_created",
        "failure_reason": None,
        "proposed_action": "HOLD",
        "selected_decision": {"decision_kind": "HOLD", "reason": "data_quality_veto", "risk_verdict": "NOT_APPLICABLE"},
        "deterministic_explanation": ["data_quality_veto"],
        "candidate_instruments": ["BTC-USD"],
        "risk_outputs": {},
    }


@pytest.mark.asyncio
async def test_candle_survives_real_orm_expiration_after_scorecard_timeout_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle

    asset_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        status="READY",
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
        campaign_modes=[],
        aggression_mode="BALANCED",
        accounting_state=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        remaining_unallocated_capital=Decimal("5"),
    )

    async def _get_campaign_definition(**_kwargs):
        return campaign

    holder: dict[str, Any] = {}

    async def _compose_campaign_authoritative_cycle(*, db, campaign_definition, trigger, candle):
        # Reproduces exactly what _recover_session_after_scorecard_failure's
        # escalation path does deep inside strategy aggregate resolution
        # when a scorecard-fetch timeout's health probe also fails: a real
        # session rollback, expiring every ORM instance the session tracks
        # -- including `candle`, held by the caller of this function.
        holder["candle"] = candle
        await db.rollback()
        return SimpleNamespace(composition=_hold_veto_composition(), preview=None)

    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.get_campaign_definition", _get_campaign_definition)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.compose_campaign_authoritative_cycle", _compose_campaign_authoritative_cycle)

    async with _real_candle_session() as engine:
        await _seed_asset_and_candle(engine, asset_id=asset_id)

        async with _CountingAsyncSession(engine) as db:
            payload = await run_campaign_orchestration_preview_for_candle(
                db=db, campaign_id=campaign.campaign_id, version=campaign.version, allow_draft_preview=False,
            )

            # The escalation path really did run a real rollback...
            assert db.rollback_calls == 1
            # ...which really did expire the candle instance compose_campaign_authoritative_cycle held.
            assert sa_inspect(holder["candle"]).expired is True

            # And yet the cycle completed correctly: fail-closed HOLD veto,
            # not a crash. This only holds because
            # run_campaign_orchestration_preview_for_candle snapshots
            # candle.asset_id/.open_time/.close_time into plain values
            # before compose_campaign_authoritative_cycle is ever called --
            # without that, the idempotency-key build and cycle_context
            # construction below would touch the now-expired candle
            # instance through SQLAlchemy's asyncio greenlet bridge outside
            # of an awaited context, raising the real
            # sqlalchemy.exc.MissingGreenlet this test guards against (this
            # uses a genuine AsyncSession over aiosqlite specifically so
            # that bridge is exercised for real, not simulated).
            assert payload["cycle_count"] == 1
            assert payload["cycles"][0]["termination_stage"] == "hold_no_package_created"


@pytest.mark.asyncio
async def test_authoritative_open_candidate_selects_best(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = _campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD", "ETH-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(
        account_equity=Decimal("25"),
        start_of_day_equity=Decimal("25"),
        current_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("25"),
        max_drawdown_pct=Decimal("0.10"),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=3,
        last_loss_at=None,
        cooldown_duration_minutes=Decimal("1440"),
        evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
        data_is_stale=False,
        data_has_gaps=False,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
        risk_policy_source="module_fallback_default",
    )

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.10"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    assert result.composition["risk_outputs"]["BTC-USD"]["risk_event_id"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    assert result.composition["decision_record_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result.composition["selected_decision"]["sizing_trace"]["minimum_viable_amount"] == "5"


@pytest.mark.asyncio
async def test_authoritative_risk_veto_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(account_equity=Decimal("25"), start_of_day_equity=Decimal("25"), current_equity=Decimal("25"), max_position_size_pct=Decimal("0.10"), max_daily_loss_pct=Decimal("0.03"), high_water_mark_equity=Decimal("25"), max_drawdown_pct=Decimal("0.10"), consecutive_losses_on_pair=0, cooldown_after_losses=3, last_loss_at=None, cooldown_duration_minutes=Decimal("1440"), evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc), data_is_stale=False, data_has_gaps=False, global_kill_switch_engaged_state=False, global_kill_switch_rearm_required=False, account_kill_switch_engaged_state=False, account_kill_switch_rearm_required=False, global_kill_switch_state_observed=True, account_kill_switch_state_observed=True, risk_policy_source="module_fallback_default")

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.REJECT, reason_code="global_kill_switch_engaged", approved_quantity=Decimal("0"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["risk_outputs"]["BTC-USD"]["verdict"] == "VETO"
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"


@pytest.mark.asyncio
async def test_authoritative_risk_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return({"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("25"),
                start_of_day_equity=Decimal("25"),
                current_equity=Decimal("25"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("25"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("risk engine unavailable")))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_authoritative_stale_market_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "STALE", "reason": "stale_market_data", "freshness": "stale"}, None, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_authoritative_missing_strategy_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), version=1, runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), allowed_instruments=["BTC-USD"], remaining_unallocated_capital=Decimal("25"), maximum_position_size=Decimal("10"), minimum_position_size=Decimal("2"), maximum_total_exposure=Decimal("20"))
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((None, "strategy_evidence_unavailable")))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_authoritative_scopes_to_trigger_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    visited: list[str] = []

    async def _load_market_evidence(**kwargs):
        visited.append(kwargs["symbol"])
        return ({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _load_market_evidence)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((None, "strategy_evidence_unavailable")))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert visited == ["BTC-USD"]
    assert result.composition["candidate_instruments"] == ["BTC-USD"]


@pytest.mark.asyncio
async def test_authoritative_strategy_identity_from_metadata_passed_to_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={"canonical_strategy_identity": "ma_crossover@1"},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    captured: dict[str, str | None] = {"preferred": None}

    async def _load_strategy(**kwargs):
        captured["preferred"] = kwargs.get("preferred_strategy_identity")
        return None, "strategy_evidence_unavailable"

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _load_strategy)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert captured["preferred"] == "ma_crossover@1"


@pytest.mark.asyncio
async def test_authoritative_no_action_reason_is_minimum_order_continuity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("1"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("1"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("1"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("1"),
                start_of_day_equity=Decimal("1"),
                current_equity=Decimal("1"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("1"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"


# Regression for production incident: a valid buy_agreement_threshold_met
# decision was still skipping READY package creation with
# position_below_minimum_order_size. Root cause: campaign_definition's
# remaining_unallocated_capital (and runtime_campaign's available-authority
# fields) trace back to runtime.current_equity, which is written exactly
# once -- at campaign draft creation/edit time -- and is never updated by
# any trade, fill, or reconciliation code afterward (confirmed: the only
# assignment to CapitalCampaign.current_equity in the whole codebase is in
# capital_campaign_domain/service.py's draft create/edit path). A
# well-funded campaign (capital_budget=1000) whose frozen equity snapshot
# happens to sit below the $5 proving floor was therefore permanently
# blocked from its very first entry, with no way to self-correct. This is a
# campaign allocation bug, not a position-sizing calculation defect or an
# incorrect minimum-order validation -- the $5 floor/asset minimum are
# working exactly as configured; capital_budget just never got consulted as
# a rescue ceiling.
@pytest.mark.asyncio
async def test_authoritative_capital_budget_rescues_stale_remaining_unallocated_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        capital_budget=Decimal("1000"),
        remaining_unallocated_capital=Decimal("1"),
        maximum_position_size=Decimal("5"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("1"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("1000"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(
        account_equity=Decimal("1000"),
        start_of_day_equity=Decimal("1000"),
        current_equity=Decimal("1000"),
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("1000"),
        max_drawdown_pct=Decimal("0.10"),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=3,
        last_loss_at=None,
        cooldown_duration_minutes=Decimal("1440"),
        evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
        data_is_stale=False,
        data_has_gaps=False,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
        risk_policy_source="module_fallback_default",
    )

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.05"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"


@pytest.mark.asyncio
async def test_authoritative_genuinely_insufficient_capital_budget_still_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The capital_budget rescue must never mask a campaign whose real,
    current capital_budget is ALSO below the floor -- and must never touch
    the independent real-cash or hard-exposure caps."""
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        capital_budget=Decimal("1"),
        remaining_unallocated_capital=Decimal("1"),
        maximum_position_size=Decimal("5"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("1"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("1000"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(
        account_equity=Decimal("1000"),
        start_of_day_equity=Decimal("1000"),
        current_equity=Decimal("1000"),
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.03"),
        high_water_mark_equity=Decimal("1000"),
        max_drawdown_pct=Decimal("0.10"),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=3,
        last_loss_at=None,
        cooldown_duration_minutes=Decimal("1440"),
        evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
        data_is_stale=False,
        data_has_gaps=False,
        global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False,
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
        risk_policy_source="module_fallback_default",
    )

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"


@pytest.mark.asyncio
async def test_authoritative_liquid_cash_499_rejects_without_risk_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("500"),
        maximum_position_size=Decimal("500"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("500"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("500"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("4.99"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("500"),
                start_of_day_equity=Decimal("500"),
                current_equity=Decimal("500"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("500"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )

    called = {"risk": False}

    def _risk_should_not_run(**_kwargs):
        called["risk"] = True
        raise AssertionError("risk evaluation must not run when liquid cash is below minimum")

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", _risk_should_not_run)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert called["risk"] is False
    assert result.composition["execution_submitted"] is False
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"
    trace = result.composition["selected_decision"]["sizing_trace"]
    assert trace["liquid_cash_cap"] == "4.99"
    assert trace["pre_risk_proposed_amount"] == "4.99"


@pytest.mark.asyncio
async def test_authoritative_liquid_cash_500_permits_exact_five(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("500"),
        maximum_position_size=Decimal("500"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("500"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("500"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("5.00"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("500"),
                start_of_day_equity=Decimal("500"),
                current_equity=Decimal("500"),
                max_position_size_pct=Decimal("1"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("500"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("0.05"), steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    trace = result.composition["selected_decision"]["sizing_trace"]
    assert trace["liquid_cash_cap"] == "5.00"
    assert Decimal(trace["campaign_allocation"]) == Decimal("5")
    assert trace["pre_risk_proposed_amount"] == "5.00"


@pytest.mark.asyncio
async def test_authoritative_liquid_cash_cap_wins_over_campaign_and_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("500"),
        maximum_position_size=Decimal("500"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("500"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(
        id=17,
        paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"),
        exchange="kraken_spot",
        current_equity=Decimal("500"),
        available_authority=Decimal("500"),
        status="READY",
    )
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("23.7205"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)
    strategy = {"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1", "strategy_version": "1", "action": "BUY", "confidence": "0.8", "sample_size": 12, "profitable_after_fees_performance": "4.2", "historical_gross_return_pct": "4.2", "expected_value": "4.2", "evidence_timestamp": "2026-07-15T00:15:00+00:00", "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}}
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))


@pytest.mark.asyncio
async def test_authoritative_hold_preserves_strategy_evidence_in_preview_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={"canonical_strategy_identity": "ma_crossover@1.0.0"},
    )
    runtime_campaign = SimpleNamespace(
        id=17,
        paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"),
        exchange="kraken_spot",
        current_equity=Decimal("25"),
        status="READY",
    )
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(
        asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        close=Decimal("100"),
        close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc),
        interval="15m",
        open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
    )
    asset = SimpleNamespace(
        id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        exchange="kraken_spot",
        base_currency="USD",
        min_order_notional=Decimal("5"),
        qty_step_size=None,
        supports_fractional=True,
    )
    market = {
        "authority_class": "AUTHORITATIVE",
        "reason": "market data resolved from canonical asset and candle tables",
        "freshness": "fresh",
        "close_price": "100",
    }
    strategy = {
        "authority_class": "AUTHORITATIVE",
        "strategy_identity": "ma_crossover@1.0.0",
        "strategy_version": "1.0.0",
        "action": "HOLD",
        "confidence": "0.8",
        "sample_size": 12,
        "profitable_after_fees_performance": "4.2",
        "historical_gross_return_pct": "4.2",
        "expected_value": "4.2",
        "evidence_timestamp": "2026-07-15T00:15:00+00:00",
        "source_identity": {"decision_record_id": "280390e3-180c-4247-8d82-56ab51f463cf"},
    }

    captured: dict[str, object] = {}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    def _capture_preview(*, campaign, request, now):
        captured["strategy_evidence"] = request.strategy_evidence
        captured["lifecycle_snapshots"] = request.lifecycle_snapshots
        captured["risk_preview"] = request.risk_preview
        return SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"})

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", _capture_preview)

    result = await compose_campaign_authoritative_cycle(
        db=_Db(),
        campaign_definition=campaign,
        trigger="kraken_btc_15m_candle_close",
        candle=candle,
    )

    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "strategy_hold_signal"
    assert result.composition["selected_decision"]["decision_record_id"] == "280390e3-180c-4247-8d82-56ab51f463cf"
    assert result.composition["termination_stage"] == "hold_no_package_created"
    assert result.composition["authoritative_evidence"]["strategy_authority"]["authority_source"] == "campaign_metadata_evidence"
    strategy_inputs = captured["strategy_evidence"]
    assert len(strategy_inputs) == 1
    assert strategy_inputs[0].instrument == "BTC-USD"
    assert strategy_inputs[0].authority_class == "AUTHORITATIVE"


@pytest.mark.asyncio
async def test_authoritative_rejected_candidate_preserves_strategy_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("1"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={"canonical_strategy_identity": "ma_crossover@1.0.0"},
    )
    runtime_campaign = SimpleNamespace(
        id=17,
        paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"),
        exchange="kraken_spot",
        current_equity=Decimal("1"),
        status="READY",
    )
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("1"), current_cash_balance=Decimal("1"))
    candle = SimpleNamespace(
        asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        close=Decimal("100"),
        close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc),
        interval="15m",
        open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
    )
    asset = SimpleNamespace(
        id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        exchange="kraken_spot",
        base_currency="USD",
        min_order_notional=Decimal("5"),
        qty_step_size=None,
        supports_fractional=True,
    )
    strategy = {
        "authority_class": "AUTHORITATIVE",
        "strategy_identity": "ma_crossover@1.0.0",
        "strategy_version": "1.0.0",
        "action": "BUY",
        "confidence": "0.8",
        "sample_size": 12,
        "profitable_after_fees_performance": "4.2",
        "historical_gross_return_pct": "4.2",
        "expected_value": "4.2",
        "evidence_timestamp": "2026-07-15T00:15:00+00:00",
        "source_identity": {"decision_record_id": "280390e3-180c-4247-8d82-56ab51f463cf"},
    }
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}

    captured: dict[str, object] = {}

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    def _capture_preview(*, campaign, request, now):
        captured["strategy_evidence"] = request.strategy_evidence
        return SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"})

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context",
        _async_return(
            SimpleNamespace(
                account_equity=Decimal("1"),
                start_of_day_equity=Decimal("1"),
                current_equity=Decimal("1"),
                max_position_size_pct=Decimal("0.10"),
                max_daily_loss_pct=Decimal("0.03"),
                high_water_mark_equity=Decimal("1"),
                max_drawdown_pct=Decimal("0.10"),
                consecutive_losses_on_pair=0,
                cooldown_after_losses=3,
                last_loss_at=None,
                cooldown_duration_minutes=Decimal("1440"),
                evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
                data_is_stale=False,
                data_has_gaps=False,
                global_kill_switch_engaged_state=False,
                global_kill_switch_rearm_required=False,
                account_kill_switch_engaged_state=False,
                account_kill_switch_rearm_required=False,
                global_kill_switch_state_observed=True,
                account_kill_switch_state_observed=True,
                risk_policy_source="module_fallback_default",
            )
        ),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", _capture_preview)

    result = await compose_campaign_authoritative_cycle(
        db=_Db(),
        campaign_definition=campaign,
        trigger="kraken_btc_15m_candle_close",
        candle=candle,
    )

    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "position_below_minimum_order_size"
    strategy_inputs = captured["strategy_evidence"]
    assert len(strategy_inputs) == 1
    assert strategy_inputs[0].instrument == "BTC-USD"


@pytest.mark.asyncio
async def test_authoritative_strategy_hold_signal_returns_hold_no_package_created(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("4.33159379773015"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence",
        _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1.0.0", "strategy_version": "ma_crossover@1.0.0", "action": "HOLD", "source_identity": {"decision_record_id": "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"}}, None)),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is False
    assert result.composition["termination_stage"] == "hold_no_package_created"
    assert result.composition["proposed_action"] == "HOLD"
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["decision_record_id"] == "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"


@pytest.mark.asyncio
async def test_authoritative_incoherent_strategy_identity_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence",
        _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "donchian_breakout@1.0.0", "strategy_version": "ma_crossover@1.0.0", "action": "BUY", "source_identity": {"decision_record_id": "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"}}, None)),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["reason"] == "strategy_identity_incoherent"


@pytest.mark.asyncio
async def test_authoritative_historical_package_conflict_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        metadata_evidence={},
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"), current_cash_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=Decimal("100"), close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=Decimal("5"), qty_step_size=None, supports_fractional=True)

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_campaign_strategy_authority", _async_return({"authority_source": "canonical_preview_package_continuity_only", "preferred_strategy_identity": None, "historical_strategy_identity": "donchian_breakout@1.0.0"}))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return(({"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": "100"}, asset, candle)))
    monkeypatch.setattr(
        "app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence",
        _async_return(({"authority_class": "AUTHORITATIVE", "strategy_identity": "ma_crossover@1.0.0", "strategy_version": "ma_crossover@1.0.0", "action": "BUY", "source_identity": {"decision_record_id": "facbd8a9-7784-4cdd-b689-06d4a1d7ebe7"}}, None)),
    )
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": True, "preview": "stub"}))

    result = await compose_campaign_authoritative_cycle(db=_Db(), campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["reason"] == "strategy_continuity_conflict"


# NOTE: test_latest_strategy_evidence_uses_decision_signal_identity_not_scorecard_best
# and test_latest_strategy_evidence_conflicting_generated_signals_fail_closed were
# removed here. Both asserted internal mechanics of the OLD single-best-scorecard
# + single-DecisionRecord-lookup implementation of resolve_and_persist_strategy_aggregate_evidence
# (a `best_scorecard` variable and a scan of decision_record.supporting_strategies
# for a match) that no longer exist -- that function is now a governed, deterministic
# multi-strategy aggregator (app/services/strategy_roster/decision_aggregator.py) and
# always produces its own paired DecisionRecord rather than searching for a
# pre-existing one. The principle those tests encoded -- that the strategy evidence
# used for the authoritative decision must come from a governed record, not raw
# unvetted scorecard ranking -- is preserved and re-tested against the new
# implementation in:
#   tests/unit/services/strategy_roster/test_decision_aggregator.py
#     (test_ma_crossover_does_not_automatically_win_against_stronger_evidence)
#   tests/unit/services/capital_campaign_orchestration/test_strategy_decision_aggregator_integration.py


_UNSET = object()


def _net_edge_authoritative_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profitable_after_fees_performance: str | None,
    expected_value: str | None = None,
    approved_quantity: Decimal,
    price: Decimal = Decimal("100"),
    min_order_notional: Decimal = Decimal("5"),
    historical_gross_return_pct: str | None | object = _UNSET,
):
    """Shared fixture for compose_campaign_authoritative_cycle net-edge-gate tests.

    Mirrors test_authoritative_open_candidate_selects_best's mocking style so a
    single BUY candidate reaches the net-edge gate with a risk-approved
    quantity supplied directly by the caller (no capital-cap rejection noise).
    """
    campaign = SimpleNamespace(
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version=1,
        runtime_campaign_uuid=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        allowed_instruments=["BTC-USD"],
        remaining_unallocated_capital=Decimal("25"),
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
    )
    runtime_campaign = SimpleNamespace(id=17, paper_account_id=UUID("12345678-1234-1234-1234-1234567890ab"), exchange="kraken_spot", current_equity=Decimal("25"), status="READY")
    paper_account = SimpleNamespace(id=runtime_campaign.paper_account_id, starting_balance=Decimal("25"))
    candle = SimpleNamespace(asset_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), close=price, close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc), interval="15m", open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc))
    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), exchange="kraken_spot", base_currency="USD", min_order_notional=min_order_notional, qty_step_size=None, supports_fractional=True)
    market = {"authority_class": "AUTHORITATIVE", "reason": "market data resolved from canonical asset and candle tables", "freshness": "fresh", "close_price": format(price, "f")}
    strategy = {
        "authority_class": "AUTHORITATIVE",
        "strategy_identity": "ma_crossover@1",
        "strategy_version": "1",
        "action": "BUY",
        "confidence": "0.8",
        "sample_size": 12,
        "profitable_after_fees_performance": profitable_after_fees_performance,
        # historical_gross_return_pct is what the net-edge gate actually
        # reads as its gross-edge input; by default these tests mirror the
        # same value passed for profitable_after_fees_performance under the
        # correct (pre-fee) field name too, unless a test explicitly needs
        # the two to diverge (the exact scenario that caused the production
        # double-fee-count defect).
        "historical_gross_return_pct": (
            profitable_after_fees_performance if historical_gross_return_pct is _UNSET else historical_gross_return_pct
        ),
        "expected_value": expected_value,
        "evidence_timestamp": "2026-07-15T00:15:00+00:00",
        "source_identity": {"decision_record_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
    }
    position = {"authority_class": "AUTHORITATIVE", "position": None, "lifecycle": None, "profitability": None}
    risk_context = SimpleNamespace(
        account_equity=Decimal("25"), start_of_day_equity=Decimal("25"), current_equity=Decimal("25"),
        max_position_size_pct=Decimal("0.10"), max_daily_loss_pct=Decimal("0.03"), high_water_mark_equity=Decimal("25"),
        max_drawdown_pct=Decimal("0.10"), consecutive_losses_on_pair=0, cooldown_after_losses=3, last_loss_at=None,
        cooldown_duration_minutes=Decimal("1440"), evaluation_time=datetime(2026, 7, 15, 0, 16, tzinfo=timezone.utc),
        data_is_stale=False, data_has_gaps=False, global_kill_switch_engaged_state=False, global_kill_switch_rearm_required=False,
        account_kill_switch_engaged_state=False, account_kill_switch_rearm_required=False, global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True, risk_policy_source="module_fallback_default",
    )

    class _Db:
        async def scalar(self, _statement):
            return paper_account

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_market_evidence", _async_return((market, asset, candle)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_and_persist_strategy_aggregate_evidence", _async_return((strategy, None)))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_position_evidence", _async_return(position))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.resolve_execution_risk_context", _async_return(risk_context))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.evaluate_signal_risk", lambda **_kwargs: RiskEvaluationResult(action=RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=approved_quantity, steps=[]))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.persist_risk_decision", _async_return(SimpleNamespace(risk_event_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))))
    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative.build_campaign_preview", lambda **_kwargs: SimpleNamespace(model_dump=lambda **_dump_kwargs: {"no_action": False, "preview": "stub"}))
    return campaign, _Db(), candle


@pytest.mark.asyncio
async def test_net_edge_positive_gross_but_negative_after_costs_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    # gross edge 0.02% < round-trip fees (0.02%) + slippage (0.01%) = 0.03% -> net negative despite positive gross.
    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="0.02", approved_quantity=Decimal("0.05")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "non_positive_net_edge"
    assert result.composition["eligible_candidates"] == []
    assert result.composition["rejected_candidates"][0]["reason"] == "non_positive_net_edge"


@pytest.mark.asyncio
async def test_net_edge_positive_above_threshold_permits_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="4.2", approved_quantity=Decimal("0.05")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    assert result.composition["failed_closed"] is False
    candidate = result.composition["eligible_candidates"][0]
    assert Decimal(candidate["expected_net_dollars"]) == Decimal("0.2085")


@pytest.mark.asyncio
async def test_net_edge_exactly_zero_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    # gross edge 0.03% exactly equals round-trip fees (0.02%) + slippage (0.01%) -> net edge is exactly zero.
    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="0.03", approved_quantity=Decimal("0.05")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["decision_kind"] == "HOLD"
    assert result.composition["selected_decision"]["reason"] == "non_positive_net_edge"


@pytest.mark.asyncio
async def test_net_edge_missing_expected_edge_fails_closed_with_distinct_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    # Brand-new proving campaign: no scorecard history yet for the dominant
    # contributor, so both expected-edge sources are None. This must fail
    # closed with a reason distinct from "we evaluated the edge and it was
    # non-positive" -- silently treating "unknown" as "zero" was the
    # confirmed production defect.
    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance=None, expected_value=None, approved_quantity=Decimal("0.05")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["failed_closed"] is True
    assert result.composition["selected_decision"]["decision_kind"] == "MANUAL_REVIEW_REQUIRED"
    assert result.composition["selected_decision"]["reason"] == "expected_edge_unavailable"
    assert result.composition["rejected_candidates"][0]["reason"] == "expected_edge_unavailable"


@pytest.mark.asyncio
async def test_net_edge_uses_raw_gross_not_fee_adjusted_avoiding_double_fee_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the confirmed production defect and its fix: the scorecard's
    fee-adjusted historical figure (profitable_after_fees_performance) is
    already net of outcome-scoring's own round-trip fee assumption. Feeding
    it into the net-edge gate's "gross edge" slot double-charges that cost --
    the gate's own entry/exit fee constants get subtracted a second time on
    top of an already fee-adjusted number. This is exactly the production
    shape: profitable_after_fees_performance is slightly negative (-0.0153),
    but the true pre-fee (raw) historical return is positive enough that,
    once the gate's own cost model is applied to the CORRECT (raw) input
    exactly once, the trade is economically justified."""
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch,
        profitable_after_fees_performance="-0.0153",  # already fee-adjusted, per production logs
        historical_gross_return_pct="0.1847",  # the true raw figure (-0.0153 + a 0.20-point historical fee)
        approved_quantity=Decimal("0.05"),
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)

    # net = 0.1847 (raw) - 0.02 (entry+exit) - 0.01 (slippage) - 0 (buffer) = 0.1547 -- positive.
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"
    candidate = result.composition["eligible_candidates"][0]
    assert Decimal(candidate["expected_net_edge_pct"]) == Decimal("0.1547")


@pytest.mark.asyncio
async def test_net_edge_does_not_fall_back_to_fee_adjusted_figure_when_raw_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards against silently re-introducing the double-fee-count defect: if
    historical_gross_return_pct is unavailable, the gate must fail closed to
    expected_edge_unavailable, never silently substitute the fee-adjusted
    profitable_after_fees_performance as a stand-in gross figure."""
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch,
        profitable_after_fees_performance="4.2",
        historical_gross_return_pct=None,
        expected_value=None,
        approved_quantity=Decimal("0.05"),
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["reason"] == "expected_edge_unavailable"


@pytest.mark.asyncio
async def test_net_edge_five_dollar_notional_arithmetic_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    # Exact production-shape case: $5 notional (0.05 BTC @ $100), 4.2% gross edge.
    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="4.2", approved_quantity=Decimal("0.05"), price=Decimal("100")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    candidate = result.composition["eligible_candidates"][0]
    assert Decimal(candidate["expected_net_edge_pct"]) == Decimal("4.17")
    assert Decimal(candidate["expected_net_dollars"]) == Decimal("0.2085")
    assert Decimal(candidate["expected_fees"]) == Decimal("0.0010")
    assert Decimal(candidate["expected_slippage"]) == Decimal("0.0005")
    assert result.composition["rejected_candidates"] == []


@pytest.mark.asyncio
async def test_net_edge_round_trip_fees_counted_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="4.2", approved_quantity=Decimal("0.05")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    candidate = result.composition["eligible_candidates"][0]
    approved_notional = Decimal("5")
    entry_fee = (Decimal("0.01") / Decimal("100")) * approved_notional
    exit_fee = (Decimal("0.01") / Decimal("100")) * approved_notional
    # expected_fees must equal ENTRY + EXIT summed once each, not a single
    # leg doubled and not a full round trip applied twice.
    assert Decimal(candidate["expected_fees"]) == entry_fee + exit_fee
    assert Decimal(candidate["expected_fees"]) == Decimal("0.001")


@pytest.mark.asyncio
async def test_non_positive_net_edge_hold_preserves_existing_decision_record_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    # The strategy-roster aggregate's BUY vote already has an immutable
    # DecisionRecord (created by _persist_strategy_aggregate_decision,
    # surfaced here as strategy["source_identity"]["decision_record_id"]).
    # A campaign-level HOLD/rejection on top of that vote must not orphan
    # this linkage -- production showed decision_record_id=None on every
    # non_positive_net_edge rejection despite the record existing.
    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="0.02", approved_quantity=Decimal("0.05")
    )
    result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)
    assert result.composition["selected_decision"]["reason"] == "non_positive_net_edge"
    assert result.composition["decision_record_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result.composition["selected_decision"]["decision_record_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result.composition["rejected_candidates"][0]["decision_record_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_net_edge_diagnostic_log_reflects_values_actually_used(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="4.2", approved_quantity=Decimal("0.05")
    )
    with caplog.at_level(logging.INFO, logger="app.services.capital_campaign_orchestration.authoritative"):
        result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)

    net_edge_records = [record for record in caplog.records if record.getMessage().startswith("net_edge_evaluated ")]
    assert len(net_edge_records) == 1
    message = net_edge_records[0].getMessage()
    assert "instrument=BTC-USD" in message
    assert "side=buy" in message
    assert "reference_price=100" in message
    assert "approved_notional=5.00" in message
    assert "expected_gross_edge_pct=4.2" in message
    assert "entry_fee_pct=0.01" in message
    assert "exit_fee_pct=0.01" in message
    assert "expected_net_edge_pct=4.17" in message
    assert "final_reason_code=accepted" in message
    assert "edge_provenance=scorecard_historical_gross_return_pct" in message
    assert result.composition["selected_decision"]["decision_kind"] == "OPEN_POSITION_PROPOSED"


@pytest.mark.asyncio
async def test_non_positive_net_edge_rejection_explained_log_contains_every_component(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Production showed non_positive_net_edge rejections with no numeric
    trail: only the final reason code, no decision_record_id, and no visible
    fee/slippage/edge breakdown. This asserts the dedicated rejection-explain
    log carries every component of the calculation the rejection reason
    depends on, so it is explainable from logs alone."""
    from app.services.capital_campaign_orchestration.authoritative import compose_campaign_authoritative_cycle

    # gross edge 0.02% < round-trip fees (0.02%) + slippage (0.01%) = 0.03% -> net negative.
    campaign, db, candle = _net_edge_authoritative_mocks(
        monkeypatch, profitable_after_fees_performance="0.02", approved_quantity=Decimal("0.05")
    )
    with caplog.at_level(logging.INFO, logger="app.services.capital_campaign_orchestration.authoritative"):
        result = await compose_campaign_authoritative_cycle(db=db, campaign_definition=campaign, trigger="kraken_btc_15m_candle_close", candle=candle)

    rejection_records = [
        record for record in caplog.records if record.getMessage().startswith("non_positive_net_edge_rejection_explained ")
    ]
    assert len(rejection_records) == 1
    message = rejection_records[0].getMessage()
    assert "decision_record_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in message
    assert "strategy_id=ma_crossover@1" in message
    assert "asset=BTC-USD" in message
    assert "side=buy" in message
    assert "approved_notional=5.00" in message
    assert "expected_gross_edge_pct=0.02" in message
    assert "expected_gross_dollars=0.0010" in message
    assert "fees_pct=0.02" in message
    assert "fees_dollars=0.0010" in message
    assert "slippage_pct=0.01" in message
    assert "slippage_dollars=0.0005" in message
    assert "historical_profitability_metric=0.02" in message
    assert "profitability_source=scorecard_historical_gross_return_pct" in message
    assert "expected_net_edge_pct=-0.01" in message
    assert "expected_net_dollars=-0.0005" in message
    assert "rejection_threshold_net_dollars=0" in message
    assert "pnl_override_active=False" in message
    assert "final_reason_code=non_positive_net_edge" in message
    assert result.composition["selected_decision"]["reason"] == "non_positive_net_edge"
    assert result.composition["decision_record_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_market_evidence_15m_freshness_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    close_time = datetime(2026, 7, 16, 16, 30, tzinfo=timezone.utc)
    candle = SimpleNamespace(id=101, interval="15m", close=Decimal("100"), close_time=close_time, open_time=close_time.replace(minute=15))

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _load_closed(**_kwargs):
        return candle

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _load_closed)

    payload_15, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=15))
    assert payload_15["freshness_verdict"] == "fresh"

    payload_18, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=18))
    assert payload_18["freshness_verdict"] == "fresh"

    payload_20, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=20))
    assert payload_20["freshness_verdict"] == "fresh"
    assert payload_20["maximum_age_minutes"] == 20

    payload_21, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=close_time + timedelta(minutes=20, seconds=1))
    assert payload_21["reason"] == "stale_market_data"
    assert payload_21["freshness_verdict"] == "stale"


@pytest.mark.asyncio
async def test_market_evidence_future_candle_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    now = datetime(2026, 7, 16, 16, 48, tzinfo=timezone.utc)
    candle = SimpleNamespace(id=102, interval="15m", close=Decimal("100"), close_time=now + timedelta(minutes=1), open_time=now)

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _load_closed(**_kwargs):
        return candle

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _load_closed)

    payload, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=now)
    assert payload["reason"] == "stale_market_data"
    assert payload["freshness_verdict"] == "fail_closed_future_timestamp"


@pytest.mark.asyncio
async def test_market_evidence_incomplete_current_candle_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    now = datetime(2026, 7, 16, 16, 48, tzinfo=timezone.utc)

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _no_closed(**_kwargs):
        return None

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _no_closed)

    payload, _, candle = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="15m", now=now)
    assert candle is None
    assert payload["reason"] == "market_data_unavailable"


@pytest.mark.asyncio
async def test_market_evidence_unrelated_interval_uses_safe_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_market_evidence

    asset = SimpleNamespace(id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), symbol="BTC", exchange="kraken_spot", base_currency="USD", asset_class="crypto", is_active=True)
    close_time = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)
    candle = SimpleNamespace(id=103, interval="1m", close=Decimal("100"), close_time=close_time, open_time=close_time - timedelta(minutes=1))

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _Db:
        async def execute(self, _statement):
            return _Result([asset])

    async def _load_closed(**_kwargs):
        return candle

    monkeypatch.setattr("app.services.capital_campaign_orchestration.authoritative._load_latest_closed_candle", _load_closed)

    payload_fresh, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="1m", now=close_time + timedelta(minutes=1))
    assert payload_fresh["freshness_verdict"] == "fresh"

    payload_stale, _, _ = await _load_market_evidence(db=_Db(), symbol="BTC-USD", exchange="kraken_spot", candle_interval="1m", now=close_time + timedelta(minutes=2, seconds=1))
    assert payload_stale["reason"] == "stale_market_data"
    assert payload_stale["freshness_verdict"] == "stale"


def test_campaign_level_skip_reason_flags_status_not_eligible() -> None:
    from app.services.capital_campaign_orchestration.service import _campaign_level_skip_reason

    campaign = SimpleNamespace(status="COMPLETED", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"])
    assert _campaign_level_skip_reason(campaign=campaign, allow_draft_preview=False) == "status_not_eligible"


def test_campaign_level_skip_reason_flags_instrument_not_allowed() -> None:
    from app.services.capital_campaign_orchestration.service import _campaign_level_skip_reason

    campaign = SimpleNamespace(status="READY", allowed_instruments=["ETH-USD"], allowed_venues=["kraken_spot"])
    assert _campaign_level_skip_reason(campaign=campaign, allow_draft_preview=False) == "instrument_not_allowed"


def test_campaign_level_skip_reason_flags_venue_not_allowed() -> None:
    from app.services.capital_campaign_orchestration.service import _campaign_level_skip_reason

    campaign = SimpleNamespace(status="READY", allowed_instruments=["BTC-USD"], allowed_venues=["coinbase_spot"])
    assert _campaign_level_skip_reason(campaign=campaign, allow_draft_preview=False) == "venue_not_allowed"


def test_campaign_level_skip_reason_flags_draft_preview_not_allowed() -> None:
    from app.services.capital_campaign_orchestration.service import _campaign_level_skip_reason

    campaign = SimpleNamespace(status="DRAFT", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"])
    assert _campaign_level_skip_reason(campaign=campaign, allow_draft_preview=False) == "draft_preview_not_allowed"


def test_campaign_level_skip_reason_none_when_eligible() -> None:
    from app.services.capital_campaign_orchestration.service import _campaign_level_skip_reason

    campaign = SimpleNamespace(status="READY", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"])
    assert _campaign_level_skip_reason(campaign=campaign, allow_draft_preview=False) is None

    draft_campaign = SimpleNamespace(status="DRAFT", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"])
    assert _campaign_level_skip_reason(campaign=draft_campaign, allow_draft_preview=True) is None


@pytest.mark.asyncio
async def test_preview_for_candle_reports_considered_eligible_and_skipped_campaigns(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle

    btc_asset_id = UUID("12345678-1234-1234-1234-1234567890ab")
    eligible_id = UUID("11111111-1111-1111-1111-111111111111")
    draft_id = UUID("22222222-2222-2222-2222-222222222222")
    missing_runtime_id = UUID("33333333-3333-3333-3333-333333333333")
    mismatched_version_id = UUID("44444444-4444-4444-4444-444444444444")

    raw_rows = [
        SimpleNamespace(campaign_id=eligible_id, version=1, status="READY", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"]),
        SimpleNamespace(campaign_id=draft_id, version=1, status="DRAFT", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"]),
        SimpleNamespace(campaign_id=missing_runtime_id, version=1, status="READY", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"]),
        SimpleNamespace(campaign_id=mismatched_version_id, version=1, status="READY", allowed_instruments=["BTC-USD"], allowed_venues=["kraken_spot"]),
    ]

    eligible_campaign_response = SimpleNamespace(
        campaign_id=eligible_id,
        version=1,
        status="READY",
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
    )
    draft_campaign_response = SimpleNamespace(
        campaign_id=draft_id,
        version=1,
        status="DRAFT",
        allowed_instruments=["BTC-USD"],
        allowed_venues=["kraken_spot"],
    )

    class _MultiCampaignPreviewDb:
        def __init__(self) -> None:
            self._responses = [
                SimpleNamespace(id=btc_asset_id),
                SimpleNamespace(
                    asset_id=btc_asset_id,
                    open_time=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
                    close_time=datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc),
                ),
                None,
                SimpleNamespace(definition_version=99),
                None,
            ]
            self._index = 0
            self.added = None

        async def scalar(self, _statement):
            value = self._responses[self._index]
            self._index += 1
            return value

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(raw_rows)))

        def add(self, item):
            self.added = item

        async def flush(self):
            return None

        async def commit(self):
            return None

    async def _list_campaign_definitions(**_kwargs):
        return SimpleNamespace(items=[eligible_campaign_response, draft_campaign_response])

    async def _compose_campaign_authoritative_cycle(**_kwargs):
        return SimpleNamespace(
            composition={
                "failed_closed": False,
                "termination_stage": "hold_no_package_created",
                "decision_record_id": None,
                "selected_decision": {"decision_kind": "HOLD", "risk_verdict": "NOT_APPLICABLE", "reason": "strategy_hold_signal"},
                "deterministic_explanation": [],
                "rejected_candidates": [{"instrument": "BTC-USD", "reason": "strategy_hold_signal"}],
            },
            preview=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        )

    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.list_campaign_definitions", _list_campaign_definitions)
    monkeypatch.setattr("app.services.capital_campaign_orchestration.service.compose_campaign_authoritative_cycle", _compose_campaign_authoritative_cycle)

    db = _MultiCampaignPreviewDb()
    payload = await run_campaign_orchestration_preview_for_candle(db=db, campaign_id=None, allow_draft_preview=False)

    considered_ids = {c["campaign_id"] for c in payload["considered_campaigns"]}
    assert considered_ids == {str(eligible_id), str(draft_id), str(missing_runtime_id), str(mismatched_version_id)}

    assert payload["eligible_campaigns"] == [{"campaign_id": str(eligible_id), "version": 1}]

    skipped_by_id = {s["campaign_id"]: s["reason"] for s in payload["skipped_campaigns"]}
    assert skipped_by_id[str(draft_id)] == "draft_preview_not_allowed"
    assert skipped_by_id[str(missing_runtime_id)] == "runtime_campaign_missing"
    assert skipped_by_id[str(mismatched_version_id)] == "runtime_definition_version_mismatch"

    # An eligible campaign whose authoritative composition resolves to HOLD must
    # still be reported as considered/eligible -- campaign-level selection is
    # decoupled from the per-instrument decision outcome.
    assert payload["cycle_count"] == 1
    assert db.added.termination_stage == "hold_no_package_created"


class _FakeNestedDb:
    """Minimal db double that actually engages an async savepoint context
    manager, so these tests exercise the real transactional contract
    (isolate-via-savepoint, then log, then continue) rather than passing
    only because a fake without begin_nested() happened to raise an
    AttributeError that got swallowed the same way a real failure would."""

    def __init__(self, *, scalar_results: list[object]) -> None:
        self._scalar_results = list(scalar_results)
        self._call_index = 0
        self.savepoint_enter_count = 0
        self.savepoint_rollback_count = 0

    async def scalar(self, _statement):
        value = self._scalar_results[self._call_index]
        self._call_index += 1
        if isinstance(value, Exception):
            raise value
        return value

    @asynccontextmanager
    async def begin_nested(self):
        self.savepoint_enter_count += 1
        try:
            yield
        except Exception:
            self.savepoint_rollback_count += 1
            raise


# Regression for continued intermittent PendingRollbackError after the prior
# nested-transaction fix: _load_campaign_strategy_authority runs
# unconditionally at the start of every authoritative composition cycle --
# before roster/aggregate evidence is even loaded -- and had two bare
# except-Exception blocks around live db.scalar reads with no rollback and
# no logging. A real database-level failure there poisoned the entire rest
# of the cycle's transaction, surfacing much later (and seemingly
# unrelated) at whatever statement ran next -- exactly the same failure
# shape as the aggregate-evidence scorecard-fetch sites fixed previously,
# just one call earlier in the same chain.
@pytest.mark.asyncio
async def test_campaign_strategy_authority_package_lookup_failure_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_campaign_strategy_authority

    db = _FakeNestedDb(scalar_results=[RuntimeError("simulated package lookup failure")])
    caplog.set_level(logging.WARNING)

    result = await _load_campaign_strategy_authority(
        db=db,
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        campaign_version=1,
        metadata_evidence={},
    )

    assert result == {"authority_source": "none", "preferred_strategy_identity": None}
    assert db.savepoint_enter_count == 1
    assert db.savepoint_rollback_count == 1

    matching = [
        record
        for record in caplog.records
        if record.getMessage().startswith("campaign_strategy_authority_package_lookup_failed")
    ]
    assert len(matching) == 1
    assert matching[0].exc_info is not None
    assert str(matching[0].exc_info[1]) == "simulated package lookup failure"

    # The session is still usable for an unrelated query afterward -- the
    # savepoint isolated the failure rather than leaving the db unusable.
    db._scalar_results.append("still usable")
    assert await db.scalar("unrelated") == "still usable"


@pytest.mark.asyncio
async def test_campaign_strategy_authority_strategy_lookup_failure_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.capital_campaign_orchestration.authoritative import _load_campaign_strategy_authority

    package = SimpleNamespace(
        package_id=uuid4(),
        strategy_id=uuid4(),
        parameter_set_id=uuid4(),
        strategy_version="1.0.0",
    )
    db = _FakeNestedDb(scalar_results=[package, RuntimeError("simulated strategy lookup failure")])
    caplog.set_level(logging.WARNING)

    result = await _load_campaign_strategy_authority(
        db=db,
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        campaign_version=1,
        metadata_evidence={},
    )

    assert result["authority_source"] == "canonical_preview_package_continuity_only"
    assert result["historical_strategy_identity"] is None
    assert db.savepoint_enter_count == 2
    assert db.savepoint_rollback_count == 1

    matching = [
        record
        for record in caplog.records
        if record.getMessage().startswith("campaign_strategy_authority_strategy_lookup_failed")
    ]
    assert len(matching) == 1
    assert matching[0].exc_info is not None

    db._scalar_results.append("still usable")
    assert await db.scalar("unrelated") == "still usable"


@pytest.mark.asyncio
async def test_campaign_strategy_authority_healthy_path_unaffected_by_savepoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Control case: no fault injected. Wrapping both lookups in a savepoint
    must not change the successful-resolution result."""
    from app.services.capital_campaign_orchestration.authoritative import _load_campaign_strategy_authority

    package = SimpleNamespace(
        package_id=uuid4(),
        strategy_id=uuid4(),
        parameter_set_id=uuid4(),
        strategy_version="1.0.0",
    )
    strategy = SimpleNamespace(slug="ma_crossover")
    db = _FakeNestedDb(scalar_results=[package, strategy])
    caplog.set_level(logging.WARNING)

    result = await _load_campaign_strategy_authority(
        db=db,
        campaign_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        campaign_version=1,
        metadata_evidence={},
    )

    assert result["authority_source"] == "canonical_preview_package_continuity_only"
    assert result["historical_strategy_identity"] == "ma_crossover@1.0.0"
    assert db.savepoint_rollback_count == 0
    assert not any("lookup_failed" in record.getMessage() for record in caplog.records)