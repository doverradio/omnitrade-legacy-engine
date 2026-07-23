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
        authorization_source=authority, mandate_id=None, mandate_evaluation_id=None,
        created_at=now, generated_at=now, preview_expires_at=now + timedelta(hours=1),
        authorization_expires_at=None, superseded_at=None, approval_event_id=None,
        authority_audit_correlation_id=None, dry_run_live_crypto_order_id=None,
        paper_account_id=None, live_trading_profile_id=None, provider="kraken_spot",
        environment="production", product="BTC-USD", side="BUY", strategy_version="v1",
        risk_approved_amount=5,
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


def _version(auth):
    return SimpleNamespace(
        mandate_version_id=auth.mandate_version_id, version_number=1, is_active=True, is_authorized=True,
        allowed_products=["BTC-USD"], allowed_order_sides=["BUY"], allowed_strategy_versions=["v1"],
        authorized_capital_usd=5, max_order_notional_usd=5,
    )


def _evaluation(mandate):
    return SimpleNamespace(
        evaluation_id=uuid.uuid4(), authorization_result="AUTHORIZED",
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,expected", [(False, "READY_TO_ENABLE"), (True, "ALREADY_ENABLED_AND_HEALTHY")])
async def test_readiness_ready_verdicts(monkeypatch, enabled, expected):
    package, mandate = _package(), _mandate()
    package.paper_account_id = mandate.paper_account_id
    package.live_trading_profile_id = mandate.live_trading_profile_id
    auth = _authorization(mandate)
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings(enabled))
    db = _Db(rows=[[package], [mandate], []], scalars=[auth, _version(auth), _evaluation(mandate), None])
    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=db, provider="kraken_spot", environment="production", product="BTC-USD",
    )
    assert result["verdict"] == expected
    assert result["read_only"] is True
    assert result["submission_boundary"] == {
        "activation_implies_submission": False,
        "live_submission_flag_enabled": False,
        "submission_callable_reachable": False,
        "provider_submission_callable_reachable": False,
    }


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
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings())
    scalar_values = []
    if len(mandates) == 1:
        auth = _authorization(mandates[0])
        scalar_values.extend([auth, _version(auth)])
        if len(packages) == 1:
            scalar_values.append(_evaluation(mandates[0]))
    scalar_values.append(None)
    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=_Db(rows=[packages, mandates, []], scalars=scalar_values),
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    assert result["verdict"] == "NOT_READY"
    assert code in {item["code"] for item in result["reason_codes"]}


@pytest.mark.asyncio
async def test_readiness_rejects_missing_mandate_evaluation(monkeypatch):
    package, mandate = _package(), _mandate()
    package.paper_account_id = mandate.paper_account_id
    package.live_trading_profile_id = mandate.live_trading_profile_id
    auth = _authorization(mandate)
    monkeypatch.setattr(inspection, "get_settings", lambda: _settings())
    result = await inspection.inspect_automatic_mandate_activation_readiness(
        db=_Db(rows=[[package], [mandate], []], scalars=[auth, _version(auth), None, None]),
        provider="kraken_spot", environment="production", product="BTC-USD",
    )
    assert "matching_mandate_evaluation_missing" in {item["code"] for item in result["reason_codes"]}


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
    result = await inspection.inspect_automatic_mandate_activation_proof(
        db=_Db(scalars=[package, evaluation, dry, activation, 0, 0]), package_id=package.package_id,
    )
    assert result["verdict"] == "PROVEN"
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
    result = await inspection.inspect_automatic_mandate_activation_proof(db=_Db(scalars=values), package_id=package.package_id)
    assert reason in result["reason_codes"]
    assert result["verdict"] != "PROVEN"
