from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from app.services.mandates.contracts import (
    AUTONOMY_LEVEL_1,
    AUTONOMY_LEVEL_2,
    MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED,
    MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
    MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
    MandateDomainModel,
    MandateEligibilityInput,
    MandateVersionModel,
)
from app.services.mandates.eligibility import evaluate_mandate_eligibility
from app.services.mandates.validation import (
    mandate_supports_autonomous_actions,
    validate_mandate_state_transition,
    validate_mandate_version,
    validate_version_immutability,
)


_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _mandate(*, status: str = "ACTIVE", autonomy_level: str = AUTONOMY_LEVEL_2) -> MandateDomainModel:
    return MandateDomainModel(
        mandate_id=uuid.uuid4(),
        owner_actor_id="owner:primary",
        status=status,
        autonomy_level=autonomy_level,
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=101,
        expires_at=_NOW + timedelta(days=10),
        revoked_at=None,
    )


def _version(*, is_authorized: bool = True, is_active: bool = True) -> MandateVersionModel:
    return MandateVersionModel(
        mandate_version_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(),
        version_number=1,
        base_currency="USD",
        authorized_capital_usd=Decimal("25.00"),
        max_order_notional_usd=Decimal("5.00"),
        max_open_exposure_usd=Decimal("10.00"),
        max_daily_deployed_usd=Decimal("10.00"),
        max_daily_realized_loss_usd=Decimal("3.00"),
        max_campaign_drawdown_usd=Decimal("5.00"),
        max_consecutive_losses=2,
        position_limit=1,
        price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("25"),
        max_fee_bps=Decimal("50"),
        allowed_products=("BTC-USD",),
        allowed_order_sides=("BUY", "SELL", "HOLD"),
        allowed_strategy_versions=("strategy.v1",),
        approval_policy=MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED,
        is_authorized=is_authorized,
        is_active=is_active,
    )


def _request(mandate: MandateDomainModel) -> MandateEligibilityInput:
    return MandateEligibilityInput(
        owner_actor_id=mandate.owner_actor_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
        strategy_version="strategy.v1",
        product="BTC-USD",
        side="BUY",
        proposed_notional_usd=Decimal("5.00"),
        current_open_exposure_usd=Decimal("0"),
        daily_deployed_usd=Decimal("0"),
        daily_realized_loss_usd=Decimal("0"),
        campaign_drawdown_usd=Decimal("0"),
        consecutive_losses=0,
        current_position_count=0,
        risk_verdict="ALLOW",
        evidence_age_seconds=10,
        kill_switch_engaged=False,
        observed_at=_NOW,
    )


def test_valid_mandate_authorizes_action() -> None:
    mandate = _mandate()
    version = _version()
    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=_request(mandate))

    assert decision.result == "AUTHORIZED"
    assert decision.approval_result == MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE


def test_invalid_mandate_version_rejected_by_validation_service() -> None:
    invalid = _version()
    invalid = MandateVersionModel(**{**invalid.__dict__, "max_order_notional_usd": Decimal("0")})

    result = validate_mandate_version(invalid)
    assert result.valid is False
    assert result.reason == "invalid_max_order_notional"


def test_expired_mandate_is_rejected() -> None:
    mandate = _mandate()
    mandate = MandateDomainModel(**{**mandate.__dict__, "expires_at": _NOW - timedelta(seconds=1)})
    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=_request(mandate))

    assert decision.result == "REJECTED"
    assert decision.reason_code == "mandate_expired"


def test_revoked_mandate_is_rejected() -> None:
    mandate = _mandate(status="REVOKED")
    mandate = MandateDomainModel(**{**mandate.__dict__, "revoked_at": _NOW})
    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=_request(mandate))

    assert decision.result == "REJECTED"
    assert decision.reason_code == "mandate_not_active"


def test_paused_mandate_is_rejected() -> None:
    mandate = _mandate(status="PAUSED")
    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=_request(mandate))

    assert decision.result == "REJECTED"
    assert decision.reason_code == "mandate_not_active"


def test_kill_switch_rejects_even_when_other_checks_pass() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "kill_switch_engaged": True})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "kill_switch_engaged"


def test_capital_limit_rejects_when_order_notional_exceeds_limit() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "proposed_notional_usd": Decimal("5.01")})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "order_notional_exceeds_mandate_limit"


def test_exposure_limit_rejects_when_total_exposure_would_exceed_limit() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "current_open_exposure_usd": Decimal("6")})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "open_exposure_exceeds_mandate_limit"


def test_product_restriction_rejects_unlisted_product() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "product": "ETH-USD"})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "product_not_allowed_by_mandate"


def test_provider_mismatch_rejects() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "provider": "coinbase_advanced"})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "provider_mismatch"


def test_strategy_mismatch_rejects() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "strategy_version": "strategy.v2"})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "strategy_not_allowed_by_mandate"


def test_stale_evidence_rejects() -> None:
    mandate = _mandate()
    request = _request(mandate)
    request = MandateEligibilityInput(**{**request.__dict__, "evidence_age_seconds": 31})

    decision = evaluate_mandate_eligibility(mandate=mandate, version=_version(), request=request)
    assert decision.result == "REJECTED"
    assert decision.reason_code == "execution_evidence_stale"


def test_authorization_result_is_deterministic_for_same_input() -> None:
    mandate = _mandate()
    version = _version()
    request = _request(mandate)

    first = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)
    second = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert first == second


def test_authorized_version_is_immutable() -> None:
    current = _version(is_authorized=True)
    proposed = MandateVersionModel(**{**current.__dict__, "max_fee_bps": Decimal("10")})

    result = validate_version_immutability(existing=current, proposed=proposed)
    assert result.valid is False
    assert result.reason == "authorized_version_immutable"


def test_autonomy_level_behavior_requires_level_2_for_exemption() -> None:
    mandate_level_1 = _mandate(autonomy_level=AUTONOMY_LEVEL_1)
    decision_level_1 = evaluate_mandate_eligibility(
        mandate=mandate_level_1,
        version=_version(),
        request=_request(mandate_level_1),
    )
    assert decision_level_1.result == "REJECTED"
    assert decision_level_1.approval_result == MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN

    mandate_level_2 = _mandate(autonomy_level=AUTONOMY_LEVEL_2)
    decision_level_2 = evaluate_mandate_eligibility(
        mandate=mandate_level_2,
        version=_version(),
        request=_request(mandate_level_2),
    )
    assert decision_level_2.result == "AUTHORIZED"
    assert mandate_supports_autonomous_actions(AUTONOMY_LEVEL_2) is True


def test_state_transition_rules_are_enforced() -> None:
    invalid = validate_mandate_state_transition(from_status="DRAFT", to_status="ACTIVE")
    valid = validate_mandate_state_transition(from_status="AUTHORIZED", to_status="ACTIVE")

    assert invalid.valid is False
    assert invalid.reason == "invalid_mandate_state_transition"
    assert valid.valid is True
