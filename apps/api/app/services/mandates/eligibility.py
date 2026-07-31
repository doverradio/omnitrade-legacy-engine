from __future__ import annotations

from decimal import Decimal

from app.services.mandates.contracts import (
    AUTONOMY_LEVEL_2,
    MANDATE_AUTHORIZATION_ALLOWED,
    MANDATE_AUTHORIZATION_REJECTED,
    MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
    MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
    MANDATE_PURPOSE_CONTROLLED_PROOF,
    MandateAuthorizationDecision,
    MandateDomainModel,
    MandateEligibilityInput,
    MandateVersionModel,
)


def evaluate_mandate_eligibility(
    *,
    mandate: MandateDomainModel,
    version: MandateVersionModel,
    request: MandateEligibilityInput,
) -> MandateAuthorizationDecision:
    # Exposure and deployed-capital limits constrain capital-increasing
    # entries. A spot SELL closes owned inventory; treating its notional as
    # another deployment is a historical BUY-only shortcut that prevents the
    # same mandate machinery from authorizing an exit.
    capital_increase_usd = request.proposed_notional_usd if request.side == "BUY" else 0
    # CONTROLLED_PROOF's cumulative UTC-daily BUY turnover must never block a
    # fresh proof once a prior proof has fully sold and reconciled back to
    # zero exposure -- but ordinary_deployed_usd never decrements intraday
    # (see build_risk_accounting_snapshot), so reusing it here would repeat
    # the exact defect this purpose exists to fix. CONTROLLED_PROOF instead
    # re-checks current *open* exposure (which does return to zero once a
    # position is fully closed) against the same $5 max_open_exposure_usd
    # cap the ordinary exposure_limit check below already enforces --
    # belt-and-suspenders against the identical limit, not a separate or
    # looser one. PRODUCTION's daily_deployment_limit is completely
    # unchanged.
    daily_deployment_check = (
        ("controlled_proof_open_exposure_limit",
         (
             request.controlled_proof_open_exposure_usd + capital_increase_usd
             if request.side == "BUY"
             else Decimal("0")
         ) <= version.max_open_exposure_usd,
         "controlled_proof_open_exposure_exceeds_mandate_limit")
        if mandate.purpose == MANDATE_PURPOSE_CONTROLLED_PROOF
        else ("daily_deployment_limit",
              request.daily_deployed_usd + capital_increase_usd <= version.max_daily_deployed_usd,
              "daily_deployed_exceeds_mandate_limit")
    )
    continuing_exit = request.continuing_exit_authority and request.side == "SELL"
    checks: list[tuple[str, bool, str]] = [
        ("owner_match", mandate.owner_actor_id == request.owner_actor_id, "owner_mismatch"),
        (
            "mandate_purpose_match",
            mandate.purpose == request.expected_mandate_purpose,
            "mandate_purpose_mismatch",
        ),
        ("mandate_status", mandate.status in {"ACTIVE", "EXIT_ONLY"} or (continuing_exit and mandate.status == "EXPIRED"), "mandate_not_active"),
        (
            "mandate_not_revoked",
            mandate.revoked_at is None and mandate.status not in {"REVOKED", "KILLED"},
            "mandate_revoked_or_killed",
        ),
        (
            "mandate_not_expired",
            continuing_exit or mandate.expires_at is None or mandate.expires_at > request.observed_at,
            "mandate_expired",
        ),
        ("provider_match", mandate.provider == request.provider, "provider_mismatch"),
        (
            "environment_match",
            mandate.exchange_environment == request.exchange_environment,
            "environment_mismatch",
        ),
        (
            "connection_match",
            mandate.exchange_connection_id == request.exchange_connection_id,
            "exchange_connection_mismatch",
        ),
        (
            "profile_match",
            mandate.live_trading_profile_id == request.live_trading_profile_id,
            "live_trading_profile_mismatch",
        ),
        (
            "campaign_match",
            mandate.capital_campaign_id is None or mandate.capital_campaign_id == request.capital_campaign_id,
            "capital_campaign_mismatch",
        ),
        (
            "paper_account_match",
            mandate.paper_account_id is None or mandate.paper_account_id == request.paper_account_id,
            "paper_account_mismatch",
        ),
        ("version_authorized", version.is_authorized, "mandate_version_not_authorized"),
        ("version_active", version.is_active or (continuing_exit and mandate.status == "EXPIRED"), "mandate_version_not_active"),
        (
            "product_allowed",
            request.product in version.allowed_products,
            "product_not_allowed_by_mandate",
        ),
        (
            "side_allowed",
            request.side in version.allowed_order_sides,
            "side_not_allowed_by_mandate",
        ),
        (
            "strategy_allowed",
            request.strategy_version in version.allowed_strategy_versions,
            "strategy_not_allowed_by_mandate",
        ),
        (
            "notional_limit",
            request.proposed_notional_usd <= version.max_order_notional_usd,
            "order_notional_exceeds_mandate_limit",
        ),
        (
            "exposure_limit",
            request.current_open_exposure_usd + capital_increase_usd <= version.max_open_exposure_usd,
            "open_exposure_exceeds_mandate_limit",
        ),
        daily_deployment_check,
        (
            "daily_loss_limit",
            request.daily_realized_loss_usd <= version.max_daily_realized_loss_usd,
            "daily_realized_loss_exceeds_mandate_limit",
        ),
        (
            "drawdown_limit",
            request.campaign_drawdown_usd <= version.max_campaign_drawdown_usd,
            "campaign_drawdown_exceeds_mandate_limit",
        ),
        (
            "consecutive_loss_limit",
            request.consecutive_losses <= version.max_consecutive_losses,
            "consecutive_losses_exceed_mandate_limit",
        ),
        (
            "position_limit",
            request.current_position_count <= version.position_limit,
            "position_limit_exceeded",
        ),
        (
            "risk_engine_allows_action",
            request.risk_verdict in {"ACCEPTED", "RESIZED"},
            "risk_engine_rejected_action",
        ),
        (
            "evidence_fresh",
            request.evidence_age_seconds < version.price_evidence_max_age_seconds,
            "execution_evidence_stale",
        ),
        (
            "kill_switch_clear",
            not request.kill_switch_engaged,
            "kill_switch_engaged",
        ),
    ]

    passed_checks = tuple(name for name, passed, _failure_reason in checks if passed)
    failed_pairs = [(name, failure_reason) for name, passed, failure_reason in checks if not passed]
    failures = [failure_reason for _name, failure_reason in failed_pairs]
    failed_checks = tuple(name for name, _failure_reason in failed_pairs)

    if failures:
        explanation = tuple([*(f"CHECK_PASSED:{code}" for code in passed_checks), *(f"CHECK_FAILED:{code}" for code in failures)])
        return MandateAuthorizationDecision(
            result=MANDATE_AUTHORIZATION_REJECTED,
            approval_result=MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
            reason_code=failures[0],
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            deterministic_explanation=explanation,
        )

    if mandate.autonomy_level != AUTONOMY_LEVEL_2:
        return MandateAuthorizationDecision(
            result=MANDATE_AUTHORIZATION_REJECTED,
            approval_result=MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
            reason_code="autonomy_level_does_not_allow_autonomous_execution",
            passed_checks=passed_checks,
            failed_checks=("autonomy_level_supports_autonomous_execution",),
            deterministic_explanation=(
                *(f"CHECK_PASSED:{code}" for code in passed_checks),
                "CHECK_FAILED:autonomy_level_does_not_allow_autonomous_execution",
            ),
        )

    return MandateAuthorizationDecision(
        result=MANDATE_AUTHORIZATION_ALLOWED,
        approval_result=MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
        reason_code="authorized_under_active_mandate",
        passed_checks=(*passed_checks, "autonomy_level_supports_autonomous_execution"),
        failed_checks=(),
        deterministic_explanation=(
            *(f"CHECK_PASSED:{code}" for code in passed_checks),
            "CHECK_PASSED:autonomy_level_supports_autonomous_execution",
            "CHECK_PASSED:authorized_under_active_mandate",
        ),
    )
