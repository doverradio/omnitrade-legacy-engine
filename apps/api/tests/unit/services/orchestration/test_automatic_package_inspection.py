from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.orchestration import automatic_package_inspection as inspection


class _Rows:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _Db:
    def __init__(self, *, rows=(), scalars=()):
        self.rows = list(rows)
        self.values = list(scalars)

    async def scalars(self, _statement): return _Rows(self.rows.pop(0))
    async def scalar(self, _statement): return self.values.pop(0)


def _settings(enabled=False, submission=False):
    return SimpleNamespace(
        automatic_mandate_package_activation_enabled=enabled,
        live_crypto_preparation_enabled=True,
        live_crypto_order_submission_enabled=submission,
    )


def _package(state="READY", *, authority=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        package_id=uuid.uuid4(), campaign_id=uuid.uuid4(), campaign_version=2,
        decision_record_id=uuid.uuid4(), package_state=state,
        authorization_source=authority, mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
        created_at=now, generated_at=now, preview_expires_at=now + timedelta(hours=1),
        authorization_expires_at=None, superseded_at=None, approval_event_id=None,
        authority_audit_correlation_id=None, dry_run_live_crypto_order_id=None,
        paper_account_id=None, live_trading_profile_id=None, provider="kraken_spot",
        environment="production", product="BTC-USD", side="BUY", strategy_version="v1",
        risk_approved_amount=5,
        runtime_campaign_id=uuid.uuid4(), strategy_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(), proposed_order_amount=5,
        market_evidence_identity={},
    )


def _mandate(level="LEVEL_2", *, expired=False, revoked=False):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        mandate_id=uuid.uuid4(), status="ACTIVE", autonomy_level=level,
        expires_at=now - timedelta(seconds=1) if expired else now + timedelta(days=1),
        revoked_at=now if revoked else None,
        capital_campaign_id=2, paper_account_id=uuid.uuid4(), live_trading_profile_id=uuid.uuid4(),
        exchange_connection_id=uuid.uuid4(), provider="kraken_spot", exchange_environment="production",
    )


def _authorization(mandate):
    return SimpleNamespace(mandate_version_id=uuid.uuid4(), expires_at=datetime.now(timezone.utc) + timedelta(days=1))


def _version(auth, mandate=None):
    return SimpleNamespace(
        mandate_version_id=auth.mandate_version_id, version_number=1, is_active=True, is_authorized=True,
        mandate_id=None if mandate is None else mandate.mandate_id,
        allowed_products=["BTC-USD"], allowed_order_sides=["BUY"], allowed_strategy_versions=["strategy@v1"],
        authorized_capital_usd=5, max_order_notional_usd=5,
        max_open_exposure_usd=5, max_daily_deployed_usd=5, approval_policy="MANDATE_ALLOWED",
    )


def _identity_rows(package, mandate):
    package.market_evidence_identity = {"exchange_connection_id": str(mandate.exchange_connection_id)}
    return (
        SimpleNamespace(id=mandate.capital_campaign_id, definition_campaign_id=package.campaign_id, definition_version=package.campaign_version),
        SimpleNamespace(id=package.strategy_id, slug="strategy"),
        SimpleNamespace(
            crypto_order_preview_id=package.crypto_order_preview_id, decision_record_id=package.decision_record_id,
            provider=package.provider, environment=package.environment, product_id=package.product,
            side=package.side, strategy_id=package.strategy_id, requested_amount=package.proposed_order_amount,
        ),
        SimpleNamespace(decision_id=package.decision_record_id),
        SimpleNamespace(id=package.live_trading_profile_id, paper_account_id=package.paper_account_id),
        SimpleNamespace(exchange_connection_id=mandate.exchange_connection_id, provider=package.provider, environment=package.environment),
    )


def _evaluation(package, mandate, version):
    package.mandate_id = mandate.mandate_id
    package.mandate_version_id = version.mandate_version_id
    package.mandate_evaluation_id = uuid.uuid4()
    return SimpleNamespace(
        evaluation_id=package.mandate_evaluation_id, mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id, decision_id=package.decision_record_id,
        proposed_action=package.side, authorization_result="AUTHORIZED",
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,expected", [(False, "READY_TO_ENABLE"), (True, "ALREADY_ENABLED_AND_HEALTHY")])
async def test_readiness_ready_verdicts(monkeypatch, enabled, expected):
    package, mandate = _package(), _mandate()
    package.paper_account_id = mandate.paper_account_id
    package.live_trading_profile_id = mandate.live_trading_profile_id
    auth = _authorization(mandate)
    version = _version(auth, mandate)
    evaluation = _evaluation(package, mandate, version)
    identity_rows = _identity_rows(package, mandate)
    stale_packages = [_package(), _package()]
    for stale in stale_packages:
        stale.preview_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings(enabled))
    db = _Db(rows=[[package, *stale_packages], [mandate], []], scalars=[auth, version, evaluation, *identity_rows, None])
    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=db, provider="kraken_spot", environment="production", product="BTC-USD",
    )
    assert result["verdict"] == expected
    assert result["read_only"] is True
    assert result["eligible_package_count"] == 1
    assert all(item.package_state == "READY" for item in stale_packages)
    assert all(item["match"] for item in result["mandate"]["identity_comparisons"])
    assert {item["field"] for item in result["mandate"]["identity_comparisons"]} >= {
        "campaign_runtime", "campaign_uuid", "campaign_version", "paper_account_id",
        "live_trading_profile_id", "exchange_connection_id", "provider", "environment",
        "product", "side", "strategy_identity", "max_order_notional",
        "preview_decision_record", "preview_product", "preview_side", "preview_notional",
    }
    assert result["submission_boundary"] == {
        "activation_implies_submission": False,
        "live_submission_flag_enabled": False,
        "submission_callable_reachable": False,
        "provider_submission_callable_reachable": False,
    }


@pytest.mark.asyncio
async def test_readiness_all_stale_ready_history_is_preserved_and_reports_stale_package(monkeypatch):
    stale_packages = [_package(), _package()]
    for stale in stale_packages:
        stale.preview_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    mandate = _mandate()
    auth = _authorization(mandate)
    version = _version(auth, mandate)
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings())

    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=_Db(rows=[stale_packages, [mandate], []], scalars=[auth, version, None]),
        provider="kraken_spot", environment="production", product="BTC-USD",
    )

    assert result["verdict"] == "NOT_READY"
    assert result["eligible_package_count"] == 0
    assert "stale_package" in {item["code"] for item in result["reason_codes"]}
    stale_reason = next(item for item in result["reason_codes"] if item["code"] == "stale_package")
    assert stale_reason["package_id"] == str(stale_packages[0].package_id)
    assert stale_reason["state"] == "READY"
    assert stale_reason["age_seconds"] >= 0
    assert result["mandate"]["matching_evaluation_id"] is None
    assert result["mandate"]["evaluation_readiness"]["status"] == "PREFLIGHT_BLOCKED"
    assert all(item.package_state == "READY" for item in stale_packages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mandates,packages,code",
    [([], [_package()], "missing_active_level2_mandate"),
     ([_mandate(), _mandate()], [_package()], "ambiguous_active_level2_mandates"),
     ([_mandate()], [_package(), _package()], "ambiguous_eligible_packages"),
     ([_mandate()], [], "no_package_available")],
)
async def test_readiness_fails_closed_with_precise_reasons(monkeypatch, mandates, packages, code):
    if len(mandates) == 1:
        for package in packages:
            package.paper_account_id = mandates[0].paper_account_id
            package.live_trading_profile_id = mandates[0].live_trading_profile_id
            _identity_rows(package, mandates[0])
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings())
    scalar_values = []
    if len(mandates) == 1:
        auth = _authorization(mandates[0])
        version = _version(auth, mandates[0])
        scalar_values.extend([auth, version])
        if len(packages) == 1:
            scalar_values.extend([_evaluation(packages[0], mandates[0], version), *_identity_rows(packages[0], mandates[0])])
    scalar_values.append(None)
    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=_Db(rows=[packages, mandates, []], scalars=scalar_values),
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    assert result["verdict"] == "NOT_READY"
    assert code in {item["code"] for item in result["reason_codes"]}


@pytest.mark.asyncio
async def test_readiness_requires_successful_persisted_evaluation(monkeypatch):
    package, mandate = _package(), _mandate()
    package.paper_account_id = mandate.paper_account_id
    package.live_trading_profile_id = mandate.live_trading_profile_id
    auth = _authorization(mandate)
    version = _version(auth, mandate)
    identity_rows = _identity_rows(package, mandate)
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings())
    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=_Db(rows=[[package], [mandate], []], scalars=[auth, version, *identity_rows, None]),
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    assert result["verdict"] == "NOT_READY"
    assert result["mandate"]["matching_evaluation_id"] is None
    assert result["mandate"]["evaluation_readiness"]["status"] == "PREFLIGHT_BLOCKED"


@pytest.mark.asyncio
async def test_identity_diagnostic_finds_null_boundary_between_campaign_and_autonomous_cycles():
    campaign_cycle_id, autonomous_cycle_id, decision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    campaign_cycle = SimpleNamespace(
        cycle_id=campaign_cycle_id, cycle_kind="campaign", mandate_id=None,
        mandate_version_id=None, mandate_evaluation_id=None, decision_record_id=decision_id,
        preview_id=None, proposed_action="OPEN_POSITION_PROPOSED", capital_campaign_id=uuid.uuid4(),
        capital_campaign_version=2,
        cycle_context={"trigger": "kraken_btc_15m_candle_close", "candle": {"close_time": datetime.now(timezone.utc).isoformat()}},
    )
    roster = SimpleNamespace(roster_run_id=uuid.uuid4(), scheduled_cycle_id=autonomous_cycle_id)
    autonomous_cycle = SimpleNamespace(
        cycle_id=autonomous_cycle_id, cycle_kind="autonomous", mandate_id=uuid.uuid4(),
        mandate_version_id=uuid.uuid4(), mandate_evaluation_id=uuid.uuid4(),
        decision_record_id=uuid.uuid4(), proposed_action="BUY",
    )
    result = await inspection.inspect_mandate_evaluation_identity_propagation(
        db=_Db(scalars=[campaign_cycle, roster, autonomous_cycle]),
        cycle_id=campaign_cycle_id, decision_record_id=decision_id,
    )
    assert result["verdict"] == "INCOMPLETE"
    assert result["autonomous_cycle"]["cycle_id"] == str(autonomous_cycle_id)
    assert "campaign_cycle.mandate_evaluation_id" in result["missing_at"]


@pytest.mark.asyncio
async def test_proof_complete_mandate_evidence_is_proven():
    package = _package("ACTIVATED", authority="MANDATE")
    package.mandate_id = uuid.uuid4()
    package.mandate_evaluation_id = uuid.uuid4()
    package.dry_run_live_crypto_order_id = uuid.uuid4()
    package.authority_audit_correlation_id = uuid.uuid4()
    evaluation = SimpleNamespace(
        evaluation_id=package.mandate_evaluation_id,
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE", authorization_result="AUTHORIZED",
    )
    dry = SimpleNamespace(
        live_crypto_order_id=package.dry_run_live_crypto_order_id, status="DRY_RUN_READY",
        safe_provider_response={"dry_run": True, "submission_skipped": True, "authority_audit_correlation_id": str(package.authority_audit_correlation_id)},
        provider_order_id=None, submitted_at=None,
        decision_record_id=package.decision_record_id, provider=package.provider,
        environment=package.environment, product_id=package.product, side=package.side,
        exchange_connection_id=uuid.uuid4(),
    )
    activation = SimpleNamespace(
        activation_id=uuid.uuid4(), authority_source="MANDATE", approval_event_id=None,
        authority_audit_correlation_id=package.authority_audit_correlation_id,
        campaign_id=package.campaign_id, campaign_version=package.campaign_version,
        paper_account_id=package.paper_account_id, live_trading_profile_id=package.live_trading_profile_id,
        provider=package.provider, environment=package.environment, product=package.product,
        dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
        mandate_evaluation_id=package.mandate_evaluation_id, exchange_connection_id=dry.exchange_connection_id,
    )
    authorized_at = datetime.now(timezone.utc)
    dry_run_at = authorized_at + timedelta(seconds=1)
    activated_at = dry_run_at + timedelta(seconds=1)
    package_audits = [
        SimpleNamespace(id=1, action="canonical_preview_package_authorized_mandate", created_at=authorized_at),
        SimpleNamespace(id=2, action="canonical_preview_package_dry_run_recorded", created_at=dry_run_at),
    ]
    activation_audits = [
        SimpleNamespace(id=3, action="canonical_proving_activation_created", created_at=activated_at),
    ]
    result = await inspection.inspect_automatic_mandate_activation_proof(
        db=_Db(rows=[package_audits, activation_audits], scalars=[package, evaluation, dry, activation, 0, 0]),
        package_id=package.package_id,
    )
    assert result["verdict"] == "PROVEN"
    assert [item["state"] for item in result["transitions"]] == [
        "READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED",
    ]
    assert result["campaign_runtime_id"] == str(package.runtime_campaign_id)
    assert result["paper_account_id"] == str(package.paper_account_id)
    assert result["live_submission_called"] is False
    assert result["provider_submission_called"] is False
    assert result["provider_order_id"] is None
    assert result["submitted_at"] is None
    assert result["live_submission_record_exists"] is False
    assert result["provider_order_id"] is None
    assert result["reconciliation_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,reason",
    [("human", "human_authority_contamination"), ("dry_missing", "dry_run_evidence_missing"),
     ("submitted", "live_submission_evidence_present"), ("correlation", "audit_correlation_mismatch"),
     ("reconciliation", "reconciliation_evidence_present"), ("position", "position_evidence_present")],
)
async def test_proof_rejects_conflicting_or_incomplete_evidence(mutation, reason):
    package = _package("ACTIVATED", authority="MANDATE")
    package.mandate_id = uuid.uuid4(); package.mandate_evaluation_id = uuid.uuid4()
    package.dry_run_live_crypto_order_id = uuid.uuid4(); package.authority_audit_correlation_id = uuid.uuid4()
    evaluation = SimpleNamespace(evaluation_id=package.mandate_evaluation_id, approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE", authorization_result="AUTHORIZED")
    connection_id = uuid.uuid4()
    dry = SimpleNamespace(live_crypto_order_id=package.dry_run_live_crypto_order_id, status="DRY_RUN_READY", safe_provider_response={"dry_run": True, "submission_skipped": True, "authority_audit_correlation_id": str(package.authority_audit_correlation_id)}, provider_order_id=None, submitted_at=None, decision_record_id=package.decision_record_id, provider=package.provider, environment=package.environment, product_id=package.product, side=package.side, exchange_connection_id=connection_id)
    activation = SimpleNamespace(activation_id=uuid.uuid4(), authority_source="MANDATE", approval_event_id=None, authority_audit_correlation_id=package.authority_audit_correlation_id, campaign_id=package.campaign_id, campaign_version=package.campaign_version, paper_account_id=package.paper_account_id, live_trading_profile_id=package.live_trading_profile_id, provider=package.provider, environment=package.environment, product=package.product, dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id, mandate_evaluation_id=package.mandate_evaluation_id, exchange_connection_id=connection_id)
    if mutation == "human": package.approval_event_id = uuid.uuid4()
    if mutation == "dry_missing": dry = None
    if mutation == "submitted": dry.provider_order_id = "unexpected"
    if mutation == "correlation": activation.authority_audit_correlation_id = uuid.uuid4()
    values = [package, evaluation]
    if package.dry_run_live_crypto_order_id is not None: values.append(dry)
    values.extend([activation, 1 if mutation == "reconciliation" else 0])
    if dry is not None: values.append(1 if mutation == "position" else 0)
    result = await inspection.inspect_automatic_mandate_activation_proof(
        db=_Db(rows=[[], []], scalars=values), package_id=package.package_id,
    )
    assert reason in result["reason_codes"]
    assert result["verdict"] != "PROVEN"
