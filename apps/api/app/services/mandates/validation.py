from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.mandates.contracts import (
    AUTONOMY_LEVEL_2,
    AUTONOMY_LEVELS,
    MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED,
    MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED,
    MandateVersionModel,
)
from app.services.strategies.identity import is_strategy_identity
from app.services.mandates.state import is_valid_state, validate_transition


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str | None


def validate_autonomy_level(level: str) -> ValidationResult:
    if level not in AUTONOMY_LEVELS:
        return ValidationResult(valid=False, reason="invalid_autonomy_level")
    return ValidationResult(valid=True, reason=None)


def validate_mandate_state_transition(*, from_status: str, to_status: str) -> ValidationResult:
    if not is_valid_state(from_status) or not is_valid_state(to_status):
        return ValidationResult(valid=False, reason="invalid_mandate_state")
    if not validate_transition(from_status=from_status, to_status=to_status):
        return ValidationResult(valid=False, reason="invalid_mandate_state_transition")
    return ValidationResult(valid=True, reason=None)


def validate_mandate_version(version: MandateVersionModel) -> ValidationResult:
    if version.version_number < 1:
        return ValidationResult(valid=False, reason="invalid_version_number")
    if version.authorized_capital_usd <= Decimal("0"):
        return ValidationResult(valid=False, reason="invalid_authorized_capital")
    if version.max_order_notional_usd <= Decimal("0"):
        return ValidationResult(valid=False, reason="invalid_max_order_notional")
    if version.max_order_notional_usd > version.authorized_capital_usd:
        return ValidationResult(valid=False, reason="max_order_exceeds_authorized_capital")
    if version.max_open_exposure_usd > version.authorized_capital_usd:
        return ValidationResult(valid=False, reason="max_exposure_exceeds_authorized_capital")
    if version.max_daily_deployed_usd > version.authorized_capital_usd:
        return ValidationResult(valid=False, reason="max_daily_deployed_exceeds_authorized_capital")
    if version.position_limit < 0:
        return ValidationResult(valid=False, reason="invalid_position_limit")
    if version.price_evidence_max_age_seconds <= 0:
        return ValidationResult(valid=False, reason="invalid_price_evidence_age")
    if not version.allowed_products:
        return ValidationResult(valid=False, reason="empty_allowed_products")
    if not version.allowed_order_sides:
        return ValidationResult(valid=False, reason="empty_allowed_order_sides")
    if not version.allowed_strategy_versions:
        return ValidationResult(valid=False, reason="empty_allowed_strategy_versions")
    if any(not is_strategy_identity(item) for item in version.allowed_strategy_versions):
        return ValidationResult(valid=False, reason="invalid_allowed_strategy_identity")
    if version.approval_policy not in {
        MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED,
        MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED,
    }:
        return ValidationResult(valid=False, reason="invalid_approval_policy")
    if version.approval_policy == MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED and version.is_active and not version.is_authorized:
        return ValidationResult(valid=False, reason="active_mandate_policy_requires_authorized_version")
    return ValidationResult(valid=True, reason=None)


def validate_version_immutability(*, existing: MandateVersionModel, proposed: MandateVersionModel) -> ValidationResult:
    if not existing.is_authorized:
        return ValidationResult(valid=True, reason=None)
    if existing != proposed:
        return ValidationResult(valid=False, reason="authorized_version_immutable")
    return ValidationResult(valid=True, reason=None)


def mandate_supports_autonomous_actions(level: str) -> bool:
    return level == AUTONOMY_LEVEL_2
