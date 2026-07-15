from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.services.position_lifecycle.contracts import PositionLifecyclePolicy


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


# Canonical static policy registry for PFP-1.2.
# Deterministic precedence: instrument_scope exact match > asset_class+venue > asset_class+venue-neutral.
_POLICIES: tuple[PositionLifecyclePolicy, ...] = (
    PositionLifecyclePolicy(
        policy_id="pl-policy-crypto-venue-neutral-v1",
        policy_version="1.0.0",
        asset_class="crypto",
        venue_scope="venue-neutral",
        instrument_scope=None,
        evaluation_cadence="5m",
        effective_at=_utc("2026-07-14T00:00:00Z"),
        expires_at=None,
        minimum_net_profit_to_exit=Decimal("2.00"),
        estimated_exit_fee_rate=Decimal("0.004"),
        estimated_slippage_rate=Decimal("0.001"),
        stale_price_threshold_minutes=15,
        minimum_position_size=Decimal("0.00001"),
        stop_loss_percent=Decimal("0.02"),
        stop_loss_price=None,
        max_hold_minutes=24 * 60,
        dust_threshold=Decimal("5.00"),
    ),
    PositionLifecyclePolicy(
        policy_id="pl-policy-stock-venue-neutral-v1",
        policy_version="1.0.0",
        asset_class="stock",
        venue_scope="venue-neutral",
        instrument_scope=None,
        evaluation_cadence="15m",
        effective_at=_utc("2026-07-14T00:00:00Z"),
        expires_at=None,
        minimum_net_profit_to_exit=Decimal("1.00"),
        estimated_exit_fee_rate=Decimal("0.001"),
        estimated_slippage_rate=Decimal("0.0005"),
        stale_price_threshold_minutes=30,
        minimum_position_size=Decimal("0.0001"),
        stop_loss_percent=Decimal("0.03"),
        stop_loss_price=None,
        max_hold_minutes=5 * 24 * 60,
        dust_threshold=Decimal("10.00"),
    ),
)


def resolve_lifecycle_policy(*, asset_class: str, symbol: str, venue: str, now: datetime) -> PositionLifecyclePolicy | None:
    normalized_asset_class = asset_class.strip().lower()
    normalized_symbol = symbol.strip().upper()
    normalized_venue = venue.strip().lower()

    candidates = [
        policy
        for policy in _POLICIES
        if policy.asset_class == normalized_asset_class
        and policy.effective_at <= now
        and (policy.expires_at is None or now < policy.expires_at)
    ]

    if not candidates:
        return None

    instrument_specific = [
        policy for policy in candidates if policy.instrument_scope is not None and policy.instrument_scope == normalized_symbol
    ]
    if instrument_specific:
        instrument_specific.sort(key=lambda policy: (policy.effective_at, policy.policy_id), reverse=True)
        return instrument_specific[0]

    venue_specific = [
        policy
        for policy in candidates
        if policy.instrument_scope is None and policy.venue_scope == normalized_venue
    ]
    if venue_specific:
        venue_specific.sort(key=lambda policy: (policy.effective_at, policy.policy_id), reverse=True)
        return venue_specific[0]

    venue_neutral = [
        policy
        for policy in candidates
        if policy.instrument_scope is None and policy.venue_scope == "venue-neutral"
    ]
    if venue_neutral:
        venue_neutral.sort(key=lambda policy: (policy.effective_at, policy.policy_id), reverse=True)
        return venue_neutral[0]

    return None


__all__ = ["resolve_lifecycle_policy"]
