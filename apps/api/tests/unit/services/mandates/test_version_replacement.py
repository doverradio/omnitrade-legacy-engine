from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.services.mandates.contracts import MandateVersionReplacementRequest
from app.services.mandates import replacement
from app.services.mandates import lifecycle
from app.services.strategies.identity import build_strategy_identity
from scripts.replace_governing_mandate_version import _parse_json_object


CANONICAL_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
LEGACY_IDENTITY = "1.0.0"


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


class _FakeDb:
    def __init__(self, *, fail_on_add_number: int | None = None) -> None:
        self.fail_on_add_number = fail_on_add_number
        self.add_calls = 0
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.refreshes = 0
        self.objects: dict[tuple[type, object], object] = {}

    def register(self, cls: type, key: object, value: object) -> None:
        self.objects[(cls, key)] = value

    def add(self, item: object) -> None:
        self.add_calls += 1
        if self.fail_on_add_number is not None and self.add_calls == self.fail_on_add_number:
            raise RuntimeError("audit write failed")
        self.added.append(item)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.added.clear()

    async def refresh(self, item: object) -> None:
        self.refreshes += 1

    async def scalar(self, statement):
        sql = str(statement)
        if "max(autonomous_capital_mandate_versions.version_number)" in sql:
            return 1
        if "FROM autonomous_capital_mandate_authorizations" in sql:
            params = statement.compile().params
            if "idempotency_key" in params and params["idempotency_key"] == "idem-existing":
                return self.objects.get((str, "existing-idempotency"))
        return None

    async def scalars(self, _statement):
        return []

    async def get(self, cls: type, key: object):
        return self.objects.get((cls, key))


def _mandate(*, status: str = "ACTIVE") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        mandate_id=uuid.uuid4(),
        owner_actor_id="operator:owner:primary",
        status=status,
        autonomy_level="LEVEL_2",
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=101,
        authorized_at=None,
        activated_at=None,
        paused_at=None,
        expires_at=None,
        revoked_at=None,
        updated_at=now,
    )


def _version(*, mandate_id: uuid.UUID, number: int, allowed_strategy_versions: tuple[str, ...], is_authorized: bool = True, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        mandate_version_id=uuid.uuid4(),
        mandate_id=mandate_id,
        version_number=number,
        version_hash=f"hash-{number}",
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
        allowed_products=["BTC-USD"],
        allowed_order_sides=["BUY", "SELL", "HOLD"],
        allowed_strategy_versions=list(allowed_strategy_versions),
        entry_policy={"mode": "stable"},
        exit_policy={"mode": "stable"},
        cooldown_policy={"mode": "stable"},
        operating_schedule={"timezone": "UTC"},
        approval_policy="MANDATE_ALLOWED",
        reconciliation_policy={"mode": "stable"},
        kill_switch_policy={"mode": "stable"},
        owner_acknowledgements={"owner_actor": "operator:owner:primary"},
        authorization_evidence_summary={"source_authorization_id": str(uuid.uuid4())},
        is_authorized=is_authorized,
        is_active=is_active,
        created_at=datetime.now(timezone.utc),
        authorized_at=datetime.now(timezone.utc),
    )


def _authorization(
    *,
    mandate_id: uuid.UUID,
    version_id: uuid.UUID,
    auth_id: uuid.UUID | None = None,
    version_number: int = 1,
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        mandate_authorization_id=auth_id or uuid.uuid4(),
        mandate_id=mandate_id,
        mandate_version_id=version_id,
        mandate_version_number=version_number,
        autonomy_level="LEVEL_2",
        authorization_state="AUTHORIZED",
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        authorized_by_actor_id="operator:owner:primary",
        audit_correlation_id=uuid.uuid4(),
        recorded_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


@pytest.mark.asyncio
async def test_dry_run_performs_zero_writes_and_reports_full_state(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([auth]))

    report = await replacement.dry_run_governing_version_replacement(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=auth.mandate_authorization_id),
    )

    assert report.mandate_status == "ACTIVE"
    assert report.source_mandate_version_id == source_version.mandate_version_id
    assert report.current_governing_version_id == source_version.mandate_version_id
    assert report.source_allowed_strategy_versions == (LEGACY_IDENTITY,)
    assert report.proposed_replacement_strategy_versions == (CANONICAL_IDENTITY,)
    assert report.replacement_required is True
    assert report.stop_reason is None
    assert len(report.versions_in_order) == 1
    assert len(report.exact_version_authorizations) == 1
    assert db.add_calls == 0
    assert db.commits == 0
    assert db.rollbacks == 0
    assert db.flushes == 0


@pytest.mark.asyncio
async def test_dry_run_stops_when_source_is_not_current_governing_version(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    later_version = _version(mandate_id=mandate.mandate_id, number=2, allowed_strategy_versions=(CANONICAL_IDENTITY,))
    auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([later_version, source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([auth]))

    report = await replacement.dry_run_governing_version_replacement(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=auth.mandate_authorization_id),
    )

    assert report.stop_reason == "unexpected_later_version"
    assert report.replacement_required is False
    assert db.commits == 0
    assert db.add_calls == 0


@pytest.mark.asyncio
async def test_dry_run_stops_when_source_allowlist_is_not_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(CANONICAL_IDENTITY,))
    auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([auth]))

    report = await replacement.dry_run_governing_version_replacement(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=auth.mandate_authorization_id),
    )

    assert report.stop_reason == 'source_allowlist_must_equal_["1.0.0"]'
    assert report.replacement_required is False


@pytest.mark.asyncio
async def test_execution_copies_policy_fields_and_authorizes_exact_new_version(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    source_auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    new_version_id = uuid.uuid4()
    new_auth_id = uuid.uuid4()
    captured: dict[str, object] = {}

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([source_auth]))
    monkeypatch.setattr(lifecycle, "get_mandate", _async_return(mandate))
    _register_row_pair(db, source_version, source_auth)

    async def _create_stub(*, db, request, commit=False):
        _ = db
        _ = commit
        captured["create_request"] = request
        return SimpleNamespace(
            mandate_version_id=new_version_id,
            mandate_id=request.mandate_id,
            version_number=2,
            base_currency=request.base_currency,
            authorized_capital_usd=request.authorized_capital_usd,
            max_order_notional_usd=request.max_order_notional_usd,
            max_open_exposure_usd=request.max_open_exposure_usd,
            max_daily_deployed_usd=request.max_daily_deployed_usd,
            max_daily_realized_loss_usd=request.max_daily_realized_loss_usd,
            max_campaign_drawdown_usd=request.max_campaign_drawdown_usd,
            max_consecutive_losses=request.max_consecutive_losses,
            position_limit=request.position_limit,
            price_evidence_max_age_seconds=request.price_evidence_max_age_seconds,
            max_slippage_bps=request.max_slippage_bps,
            max_fee_bps=request.max_fee_bps,
            allowed_products=list(request.allowed_products),
            allowed_order_sides=list(request.allowed_order_sides),
            allowed_strategy_versions=list(request.allowed_strategy_versions),
            entry_policy=request.entry_policy,
            exit_policy=request.exit_policy,
            cooldown_policy=request.cooldown_policy,
            operating_schedule=request.operating_schedule,
            approval_policy=request.approval_policy,
            reconciliation_policy=request.reconciliation_policy,
            kill_switch_policy=request.kill_switch_policy,
            owner_acknowledgements=request.owner_acknowledgements,
            authorization_evidence_summary=request.authorization_evidence_summary,
            is_authorized=False,
            is_active=False,
            created_at=datetime.now(timezone.utc),
        )

    async def _authorize_stub(*, db, request, commit=False):
        _ = db
        _ = commit
        captured["authorize_request"] = request
        return SimpleNamespace(
            mandate_authorization_id=new_auth_id,
            mandate_id=request.mandate_id,
            mandate_version_id=request.mandate_version_id,
            mandate_version_number=2,
            autonomy_level="LEVEL_2",
            authorization_state="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            authorized_by_actor_id=request.actor,
            audit_correlation_id=request.audit_correlation_id,
            recorded_at=datetime.now(timezone.utc),
            expires_at=request.expires_at,
            revoked_at=None,
        )

    monkeypatch.setattr(replacement, "create_mandate_version", _create_stub)
    monkeypatch.setattr(replacement, "authorize_mandate_version", _authorize_stub)

    report = await replacement.replace_governing_mandate_version(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=source_auth.mandate_authorization_id),
    )

    create_request = captured["create_request"]
    authorize_request = captured["authorize_request"]
    assert create_request.allowed_strategy_versions == (CANONICAL_IDENTITY,)
    assert create_request.authorized_capital_usd == source_version.authorized_capital_usd
    assert create_request.max_order_notional_usd == source_version.max_order_notional_usd
    assert create_request.max_open_exposure_usd == source_version.max_open_exposure_usd
    assert create_request.max_daily_deployed_usd == source_version.max_daily_deployed_usd
    assert create_request.max_daily_realized_loss_usd == source_version.max_daily_realized_loss_usd
    assert create_request.max_campaign_drawdown_usd == source_version.max_campaign_drawdown_usd
    assert create_request.max_consecutive_losses == source_version.max_consecutive_losses
    assert create_request.position_limit == source_version.position_limit
    assert create_request.price_evidence_max_age_seconds == source_version.price_evidence_max_age_seconds
    assert create_request.max_slippage_bps == source_version.max_slippage_bps
    assert create_request.max_fee_bps == source_version.max_fee_bps
    assert create_request.entry_policy == source_version.entry_policy
    assert create_request.exit_policy == source_version.exit_policy
    assert create_request.cooldown_policy == source_version.cooldown_policy
    assert create_request.operating_schedule == source_version.operating_schedule
    assert create_request.reconciliation_policy == source_version.reconciliation_policy
    assert create_request.kill_switch_policy == source_version.kill_switch_policy
    assert authorize_request.mandate_version_id == new_version_id
    assert authorize_request.expires_at == source_auth.expires_at
    assert authorize_request.authorization_evidence["old_version_id"] == str(source_version.mandate_version_id)
    assert authorize_request.authorization_evidence["new_version_id"] == str(new_version_id)
    assert authorize_request.authorization_evidence["source_authorization_id"] == str(source_auth.mandate_authorization_id)
    assert authorize_request.authorization_evidence["canonical_strategy_identity"] == CANONICAL_IDENTITY
    assert authorize_request.authorization_evidence["deployed_git_sha"] == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert report.result.replacement_mandate_version_id == new_version_id
    assert report.result.authorization_id == new_auth_id
    assert report.result.selected_strategy_identity == CANONICAL_IDENTITY
    assert report.result.created_replacement is True


@pytest.mark.asyncio
async def test_duplicate_rerun_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    source_auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    new_version = _version(mandate_id=mandate.mandate_id, number=2, allowed_strategy_versions=(CANONICAL_IDENTITY,))
    new_auth = _authorization(mandate_id=mandate.mandate_id, version_id=new_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=2, expires_at=source_auth.expires_at)
    state = {"first_run": True}

    async def _versions(**_kwargs):
        return [new_version, source_version] if not state["first_run"] else [source_version]

    async def _authorizations(**_kwargs):
        return [source_auth] if state["first_run"] else [source_auth, new_auth]

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _versions)
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _authorizations)
    monkeypatch.setattr(lifecycle, "get_mandate", _async_return(mandate))
    _register_row_pair(db, source_version, source_auth)
    db.register(AutonomousCapitalMandateVersion, new_version.mandate_version_id, new_version)
    db.register(AutonomousCapitalMandateAuthorization, new_auth.mandate_authorization_id, new_auth)

    async def _create_stub(*, db, request, commit=False):
        _ = db
        _ = commit
        state["first_run"] = False
        new_version.allowed_strategy_versions = list(request.allowed_strategy_versions)
        new_version.authorization_evidence_summary = request.authorization_evidence_summary
        return new_version

    async def _authorize_stub(*, db, request, commit=False):
        _ = db
        _ = commit
        return new_auth

    monkeypatch.setattr(replacement, "create_mandate_version", _create_stub)
    monkeypatch.setattr(replacement, "authorize_mandate_version", _authorize_stub)

    first = await replacement.replace_governing_mandate_version(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=source_auth.mandate_authorization_id, idempotency_key="replace-1"),
    )
    second = await replacement.replace_governing_mandate_version(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=source_auth.mandate_authorization_id, idempotency_key="replace-1"),
    )

    assert first.result.created_replacement is True
    assert second.result.created_replacement is False
    assert second.result.replacement_mandate_version_id == new_version.mandate_version_id
    assert second.result.authorization_id == new_auth.mandate_authorization_id


@pytest.mark.asyncio
async def test_partial_failure_rolls_back_replacement_and_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    source_auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([source_auth]))
    monkeypatch.setattr(lifecycle, "get_mandate", _async_return(mandate))
    _register_row_pair(db, source_version, source_auth)

    async def _create_stub(*, db, request, commit=False):
        _ = db
        _ = commit
        return SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            mandate_id=request.mandate_id,
            version_number=2,
            base_currency=request.base_currency,
            authorized_capital_usd=request.authorized_capital_usd,
            max_order_notional_usd=request.max_order_notional_usd,
            max_open_exposure_usd=request.max_open_exposure_usd,
            max_daily_deployed_usd=request.max_daily_deployed_usd,
            max_daily_realized_loss_usd=request.max_daily_realized_loss_usd,
            max_campaign_drawdown_usd=request.max_campaign_drawdown_usd,
            max_consecutive_losses=request.max_consecutive_losses,
            position_limit=request.position_limit,
            price_evidence_max_age_seconds=request.price_evidence_max_age_seconds,
            max_slippage_bps=request.max_slippage_bps,
            max_fee_bps=request.max_fee_bps,
            allowed_products=list(request.allowed_products),
            allowed_order_sides=list(request.allowed_order_sides),
            allowed_strategy_versions=list(request.allowed_strategy_versions),
            entry_policy=request.entry_policy,
            exit_policy=request.exit_policy,
            cooldown_policy=request.cooldown_policy,
            operating_schedule=request.operating_schedule,
            approval_policy=request.approval_policy,
            reconciliation_policy=request.reconciliation_policy,
            kill_switch_policy=request.kill_switch_policy,
            owner_acknowledgements=request.owner_acknowledgements,
            authorization_evidence_summary=request.authorization_evidence_summary,
            is_authorized=False,
            is_active=False,
            created_at=datetime.now(timezone.utc),
        )

    async def _authorize_fail(*, db, request, commit=False):
        _ = db
        _ = request
        _ = commit
        raise RuntimeError("authorize failed")

    monkeypatch.setattr(replacement, "create_mandate_version", _create_stub)
    monkeypatch.setattr(replacement, "authorize_mandate_version", _authorize_fail)

    with pytest.raises(RuntimeError, match="authorize failed"):
        await replacement.replace_governing_mandate_version(
            db=db,
            request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=source_auth.mandate_authorization_id),
        )

    assert db.rollbacks == 1
    assert db.commits == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_audit_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(fail_on_add_number=2)
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    source_auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([source_auth]))
    monkeypatch.setattr(lifecycle, "get_mandate", _async_return(mandate))
    _register_row_pair(db, source_version, source_auth)

    with pytest.raises(RuntimeError, match="audit write failed"):
        await replacement.replace_governing_mandate_version(
            db=db,
            request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=source_auth.mandate_authorization_id),
        )

    assert db.rollbacks == 1
    assert db.commits == 0


def test_malformed_json_fails_before_mutation() -> None:
    with pytest.raises(ValueError, match="must be valid JSON"):
        _parse_json_object("not-json", field_name="authorization_evidence_json")

    with pytest.raises(ValueError, match="must be a non-empty JSON object"):
        _parse_json_object("{}", field_name="authorization_evidence_json")


@pytest.mark.asyncio
async def test_no_provider_preview_order_balance_or_submission_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    source_version = _version(mandate_id=mandate.mandate_id, number=1, allowed_strategy_versions=(LEGACY_IDENTITY,))
    source_auth = _authorization(mandate_id=mandate.mandate_id, version_id=source_version.mandate_version_id, auth_id=uuid.uuid4(), version_number=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    called = {"provider": 0, "preview": 0, "balance": 0, "submission": 0}

    monkeypatch.setattr(replacement, "get_mandate", _async_return(mandate))
    monkeypatch.setattr(replacement, "list_mandate_versions", _async_return([source_version]))
    monkeypatch.setattr(replacement, "list_mandate_authorizations", _async_return([source_auth]))
    monkeypatch.setattr(lifecycle, "get_mandate", _async_return(mandate))
    _register_row_pair(db, source_version, source_auth)
    monkeypatch.setattr("app.services.exchange_connections.providers.registry.get_exchange_provider", lambda *_args, **_kwargs: called.__setitem__("provider", called["provider"] + 1))
    monkeypatch.setattr("app.services.crypto_order_previews.service.create_crypto_order_preview", lambda *_args, **_kwargs: called.__setitem__("preview", called["preview"] + 1))
    monkeypatch.setattr("app.services.exchange_connections.service.refresh_exchange_balances", lambda *_args, **_kwargs: called.__setitem__("balance", called["balance"] + 1))
    monkeypatch.setattr("app.services.live_crypto_orders.service.submit", lambda *_args, **_kwargs: called.__setitem__("submission", called["submission"] + 1))

    async def _create_stub(*, db, request, commit=False):
        _ = db
        _ = commit
        return SimpleNamespace(
            mandate_version_id=uuid.uuid4(),
            mandate_id=request.mandate_id,
            version_number=2,
            base_currency=request.base_currency,
            authorized_capital_usd=request.authorized_capital_usd,
            max_order_notional_usd=request.max_order_notional_usd,
            max_open_exposure_usd=request.max_open_exposure_usd,
            max_daily_deployed_usd=request.max_daily_deployed_usd,
            max_daily_realized_loss_usd=request.max_daily_realized_loss_usd,
            max_campaign_drawdown_usd=request.max_campaign_drawdown_usd,
            max_consecutive_losses=request.max_consecutive_losses,
            position_limit=request.position_limit,
            price_evidence_max_age_seconds=request.price_evidence_max_age_seconds,
            max_slippage_bps=request.max_slippage_bps,
            max_fee_bps=request.max_fee_bps,
            allowed_products=list(request.allowed_products),
            allowed_order_sides=list(request.allowed_order_sides),
            allowed_strategy_versions=list(request.allowed_strategy_versions),
            entry_policy=request.entry_policy,
            exit_policy=request.exit_policy,
            cooldown_policy=request.cooldown_policy,
            operating_schedule=request.operating_schedule,
            approval_policy=request.approval_policy,
            reconciliation_policy=request.reconciliation_policy,
            kill_switch_policy=request.kill_switch_policy,
            owner_acknowledgements=request.owner_acknowledgements,
            authorization_evidence_summary=request.authorization_evidence_summary,
            is_authorized=False,
            is_active=False,
            created_at=datetime.now(timezone.utc),
        )

    async def _authorize_stub(*, db, request, commit=False):
        _ = db
        _ = request
        _ = commit
        return SimpleNamespace(
            mandate_authorization_id=uuid.uuid4(),
            mandate_id=request.mandate_id,
            mandate_version_id=request.mandate_version_id,
            mandate_version_number=2,
            autonomy_level="LEVEL_2",
            authorization_state="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            authorized_by_actor_id=request.actor,
            audit_correlation_id=request.audit_correlation_id,
            recorded_at=datetime.now(timezone.utc),
            expires_at=request.expires_at,
            revoked_at=None,
        )

    monkeypatch.setattr(replacement, "create_mandate_version", _create_stub)
    monkeypatch.setattr(replacement, "authorize_mandate_version", _authorize_stub)

    await replacement.replace_governing_mandate_version(
        db=db,
        request=_replacement_request(mandate_id=mandate.mandate_id, source_version_id=source_version.mandate_version_id, source_authorization_id=source_auth.mandate_authorization_id),
    )

    assert called == {"provider": 0, "preview": 0, "balance": 0, "submission": 0}


def _replacement_request(
    *,
    mandate_id: uuid.UUID,
    source_version_id: uuid.UUID,
    source_authorization_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> MandateVersionReplacementRequest:
    return MandateVersionReplacementRequest(
        mandate_id=mandate_id,
        source_mandate_version_id=source_version_id,
        source_mandate_authorization_id=source_authorization_id,
        replacement_allowed_strategy_versions=(CANONICAL_IDENTITY,),
        actor="operator:owner:primary",
        authorization_method="operator_attestation",
        owner_acknowledgements={
            "owner_actor": "operator:owner:primary",
            "source_mandate_version_id": str(source_version_id),
            "source_mandate_authorization_id": str(source_authorization_id),
            "canonical_strategy_identity": CANONICAL_IDENTITY,
            "replacement_reason": "canonical identity contract replacement",
            "limits_unchanged": True,
            "preview_only_scope": True,
            "no_live_submission": True,
            "deployed_git_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        },
        authorization_evidence={
            "owner_actor": "operator:owner:primary",
            "old_version_id": str(source_version_id),
            "new_version_id": str(uuid.uuid4()),
            "source_authorization_id": str(source_authorization_id),
            "canonical_strategy_identity": CANONICAL_IDENTITY,
            "replacement_reason": "canonical identity contract replacement",
            "limits_unchanged": True,
            "preview_only_scope": True,
            "no_live_submission": True,
            "deployed_git_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        },
        deterministic_explanation={
            "reason": "canonical identity contract replacement",
            "audit_correlation_id": str(uuid.uuid4()),
        },
        deployed_git_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        expires_at=None,
        idempotency_key=idempotency_key or f"replace-governing:{mandate_id}:{source_version_id}:{source_authorization_id}:ma_crossover@1.0.0",
        audit_correlation_id=uuid.uuid4(),
        software_build_version="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )


def _register_row_pair(db: _FakeDb, version: SimpleNamespace, authorization: SimpleNamespace) -> None:
    db.register(AutonomousCapitalMandateVersion, version.mandate_version_id, version)
    db.register(AutonomousCapitalMandateAuthorization, authorization.mandate_authorization_id, authorization)
