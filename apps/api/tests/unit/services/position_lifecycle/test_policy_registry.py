from __future__ import annotations

from datetime import datetime, timezone

from app.services.position_lifecycle.policy_registry import resolve_lifecycle_policy


def test_resolves_crypto_policy_deterministically() -> None:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    policy = resolve_lifecycle_policy(asset_class="crypto", symbol="BTC-USD", venue="venue-neutral", now=now)
    assert policy is not None
    assert policy.policy_id == "pl-policy-crypto-venue-neutral-v1"
    assert policy.policy_version == "1.0.0"


def test_resolves_stock_policy_deterministically() -> None:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    policy = resolve_lifecycle_policy(asset_class="stock", symbol="AAPL-USD", venue="venue-neutral", now=now)
    assert policy is not None
    assert policy.policy_id == "pl-policy-stock-venue-neutral-v1"


def test_fail_closed_when_no_eligible_policy_exists() -> None:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    policy = resolve_lifecycle_policy(asset_class="options", symbol="SPY-OPT", venue="venue-neutral", now=now)
    assert policy is None
