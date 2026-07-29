from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.services.mandates.contracts import (
    MANDATE_PURPOSE_CONTROLLED_PROOF,
    MANDATE_PURPOSE_PRODUCTION,
    MandateDomainModel,
    MandateEligibilityInput,
    MandateVersionModel,
)
from app.services.mandates.eligibility import evaluate_mandate_eligibility

_OWNER = "operator:owner"
_STRATEGY = "ma_crossover@1.0.0"
_PRODUCT = "BTC-USD"


def _mandate(*, purpose: str = MANDATE_PURPOSE_PRODUCTION) -> MandateDomainModel:
    return MandateDomainModel(
        mandate_id=uuid.uuid4(),
        owner_actor_id=_OWNER,
        status="ACTIVE",
        autonomy_level="LEVEL_2",
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=None,
        capital_campaign_id=None,
        expires_at=None,
        revoked_at=None,
        purpose=purpose,
    )


def _version(
    *,
    max_order_notional_usd: Decimal = Decimal("5"),
    max_open_exposure_usd: Decimal = Decimal("5"),
    max_daily_deployed_usd: Decimal = Decimal("5"),
    position_limit: int = 1,
) -> MandateVersionModel:
    return MandateVersionModel(
        mandate_version_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(),
        version_number=1,
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=max_order_notional_usd,
        max_open_exposure_usd=max_open_exposure_usd,
        max_daily_deployed_usd=max_daily_deployed_usd,
        max_daily_realized_loss_usd=Decimal("100"),
        max_campaign_drawdown_usd=Decimal("100"),
        max_consecutive_losses=100,
        position_limit=position_limit,
        price_evidence_max_age_seconds=300,
        max_slippage_bps=Decimal("50"),
        max_fee_bps=Decimal("50"),
        allowed_products=(_PRODUCT,),
        allowed_order_sides=("BUY", "SELL"),
        allowed_strategy_versions=(_STRATEGY,),
        approval_policy="MANDATE_ALLOWED",
        is_authorized=True,
        is_active=True,
    )


def _request(
    *,
    mandate: MandateDomainModel,
    side: str = "BUY",
    proposed_notional_usd: Decimal = Decimal("5"),
    daily_deployed_usd: Decimal = Decimal("0"),
    controlled_proof_open_exposure_usd: Decimal = Decimal("0"),
    expected_mandate_purpose: str = MANDATE_PURPOSE_PRODUCTION,
) -> MandateEligibilityInput:
    return MandateEligibilityInput(
        owner_actor_id=mandate.owner_actor_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
        strategy_version=_STRATEGY,
        product=_PRODUCT,
        side=side,
        proposed_notional_usd=proposed_notional_usd,
        current_open_exposure_usd=Decimal("0"),
        daily_deployed_usd=daily_deployed_usd,
        daily_realized_loss_usd=Decimal("0"),
        campaign_drawdown_usd=Decimal("0"),
        consecutive_losses=0,
        current_position_count=0,
        risk_verdict="ACCEPTED",
        evidence_age_seconds=0,
        kill_switch_engaged=False,
        observed_at=datetime.now(timezone.utc),
        expected_mandate_purpose=expected_mandate_purpose,
        controlled_proof_open_exposure_usd=controlled_proof_open_exposure_usd,
    )


def test_production_mandate_still_uses_daily_deployed_usd_unchanged() -> None:
    """Ordinary production mandate semantics are byte-for-byte unchanged:
    a large daily_deployed_usd still denies, exactly as before this feature."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_PRODUCTION)
    version = _version(max_daily_deployed_usd=Decimal("5"))
    request = _request(
        mandate=mandate,
        daily_deployed_usd=Decimal("5.04"),  # a prior $5 BUY + fee, never decremented
        expected_mandate_purpose=MANDATE_PURPOSE_PRODUCTION,
    )

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "REJECTED"
    assert decision.reason_code == "daily_deployed_exceeds_mandate_limit"


def test_controlled_proof_mandate_ignores_stale_daily_deployed_usd() -> None:
    """The exact reproduced defect: a prior day's cumulative BUY turnover
    must never block a fresh Controlled Proof once exposure has returned to
    zero -- daily_deployed_usd is completely ignored for CONTROLLED_PROOF."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_CONTROLLED_PROOF)
    version = _version(max_open_exposure_usd=Decimal("5"))
    request = _request(
        mandate=mandate,
        daily_deployed_usd=Decimal("5.04"),  # would have denied under the old rule
        controlled_proof_open_exposure_usd=Decimal("0"),  # prior proof fully sold + reconciled
        expected_mandate_purpose=MANDATE_PURPOSE_CONTROLLED_PROOF,
    )

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "AUTHORIZED"


def test_controlled_proof_open_position_still_blocks_a_new_buy() -> None:
    """An open (unresolved) Controlled Proof position -- current_controlled_
    proof_open_exposure_usd + proposed_buy_notional_usd > max_open_exposure_usd
    -- still blocks a new BUY, even though daily_deployed_usd is ignored."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_CONTROLLED_PROOF)
    version = _version(max_open_exposure_usd=Decimal("5"))
    request = _request(
        mandate=mandate,
        controlled_proof_open_exposure_usd=Decimal("5.04"),  # prior proof's BUY still open
        expected_mandate_purpose=MANDATE_PURPOSE_CONTROLLED_PROOF,
    )

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "REJECTED"
    assert decision.reason_code == "controlled_proof_open_exposure_exceeds_mandate_limit"


def test_controlled_proof_still_enforces_five_dollar_order_notional_cap() -> None:
    mandate = _mandate(purpose=MANDATE_PURPOSE_CONTROLLED_PROOF)
    version = _version(max_order_notional_usd=Decimal("5"))
    request = _request(
        mandate=mandate,
        proposed_notional_usd=Decimal("6"),
        expected_mandate_purpose=MANDATE_PURPOSE_CONTROLLED_PROOF,
    )

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "REJECTED"
    assert decision.reason_code == "order_notional_exceeds_mandate_limit"


def test_controlled_proof_still_enforces_five_dollar_open_exposure_cap_via_current_open_exposure() -> None:
    """The ordinary exposure_limit check (current_open_exposure_usd) is
    unaffected and still applies for CONTROLLED_PROOF too."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_CONTROLLED_PROOF)
    version = _version(max_open_exposure_usd=Decimal("5"))
    request = MandateEligibilityInput(
        owner_actor_id=mandate.owner_actor_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
        strategy_version=_STRATEGY,
        product=_PRODUCT,
        side="BUY",
        proposed_notional_usd=Decimal("5"),
        current_open_exposure_usd=Decimal("1"),  # + 5 proposed > 5 max
        daily_deployed_usd=Decimal("0"),
        daily_realized_loss_usd=Decimal("0"),
        campaign_drawdown_usd=Decimal("0"),
        consecutive_losses=0,
        current_position_count=0,
        risk_verdict="ACCEPTED",
        evidence_age_seconds=0,
        kill_switch_engaged=False,
        observed_at=datetime.now(timezone.utc),
        expected_mandate_purpose=MANDATE_PURPOSE_CONTROLLED_PROOF,
        controlled_proof_open_exposure_usd=Decimal("0"),
    )

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "REJECTED"
    assert decision.reason_code == "open_exposure_exceeds_mandate_limit"


def test_controlled_proof_purpose_mismatch_rejects_ordinary_mandate() -> None:
    """A Controlled Proof evaluation must never be satisfiable by the
    ordinary PRODUCTION mandate."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_PRODUCTION)
    version = _version()
    request = _request(mandate=mandate, expected_mandate_purpose=MANDATE_PURPOSE_CONTROLLED_PROOF)

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "REJECTED"
    assert decision.reason_code == "mandate_purpose_mismatch"


def test_ordinary_purpose_mismatch_rejects_controlled_proof_mandate() -> None:
    """Ordinary autonomous trading must never be authorized under the
    dedicated Controlled Proof mandate."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_CONTROLLED_PROOF)
    version = _version()
    request = _request(mandate=mandate, expected_mandate_purpose=MANDATE_PURPOSE_PRODUCTION)

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "REJECTED"
    assert decision.reason_code == "mandate_purpose_mismatch"


def test_default_expected_purpose_preserves_every_existing_caller_unchanged() -> None:
    """MandateEligibilityInput.expected_mandate_purpose defaults to
    PRODUCTION, so every pre-existing caller that never mentions purpose at
    all keeps its prior, unchanged behavior against a PRODUCTION mandate."""
    mandate = _mandate(purpose=MANDATE_PURPOSE_PRODUCTION)
    version = _version()
    request = MandateEligibilityInput(
        owner_actor_id=mandate.owner_actor_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
        strategy_version=_STRATEGY,
        product=_PRODUCT,
        side="BUY",
        proposed_notional_usd=Decimal("5"),
        current_open_exposure_usd=Decimal("0"),
        daily_deployed_usd=Decimal("0"),
        daily_realized_loss_usd=Decimal("0"),
        campaign_drawdown_usd=Decimal("0"),
        consecutive_losses=0,
        current_position_count=0,
        risk_verdict="ACCEPTED",
        evidence_age_seconds=0,
        kill_switch_engaged=False,
        observed_at=datetime.now(timezone.utc),
        # expected_mandate_purpose and controlled_proof_open_exposure_usd
        # deliberately omitted -- proving the defaults alone are enough.
    )

    decision = evaluate_mandate_eligibility(mandate=mandate, version=version, request=request)

    assert decision.result == "AUTHORIZED"
