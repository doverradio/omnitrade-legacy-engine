from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.schemas.capital_campaign_domain import CommissionedEntryExecutionRequest, CommissionedReadinessRequest
from app.services.capital_campaign_domain import activated_commissioned_entry as subject


def _case():
    now = datetime.now(timezone.utc)
    campaign_id, package_id, account_id, profile_id = uuid4(), uuid4(), uuid4(), uuid4()
    mandate_id, version_id, evaluation_id, preview_id = uuid4(), uuid4(), uuid4(), uuid4()
    package = SimpleNamespace(
        package_id=package_id, package_state="ACTIVATED", campaign_id=campaign_id, campaign_version=1,
        paper_account_id=account_id, live_trading_profile_id=profile_id, provider="kraken_spot",
        environment="production", product="BTC-USD", risk_event_id=uuid4(), crypto_order_preview_id=preview_id,
        mandate_id=mandate_id, mandate_version_id=version_id, mandate_evaluation_id=evaluation_id,
        authorization_source="MANDATE", approval_event_id=None,
    )
    activation = SimpleNamespace(
        package_id=package_id, activation_state="ACTIVE", activated_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5), campaign_id=campaign_id, campaign_version=1,
        live_trading_profile_id=profile_id, provider="kraken_spot", environment="production",
        product="BTC-USD", mandate_evaluation_id=evaluation_id,
        authority_source="MANDATE", approval_event_id=None,
    )
    preview = SimpleNamespace(
        crypto_order_preview_id=preview_id, provider="kraken_spot", environment="production", product_id="BTC-USD"
    )
    evaluation = SimpleNamespace(mandate_id=mandate_id, mandate_version_id=version_id)
    readiness = CommissionedReadinessRequest.model_construct(
        campaign_id=campaign_id, version=1, provider="kraken_spot", environment="production",
        instrument="BTC-USD", account_id=account_id, live_trading_profile_id=profile_id,
        mandate_id=mandate_id, mandate_version_id=version_id,
    )
    request = CommissionedEntryExecutionRequest.model_construct(
        campaign_id=campaign_id, version=1, paper_account_id=account_id,
        risk_signal_id=preview_id, readiness_request=readiness,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, activation, preview, evaluation]))
    return now, package_id, request, db, package, activation


@pytest.mark.asyncio
async def test_exact_package_delegates_without_opening_session(monkeypatch: pytest.MonkeyPatch) -> None:
    now, package_id, request, db, _package, _activation = _case()
    expected = object()
    execute = AsyncMock(return_value=expected)
    monkeypatch.setattr(subject, "execute_commissioned_entry", execute)

    result = await subject.execute_activated_commissioned_entry(db=db, package_id=package_id, request=request, now=now)

    assert result is expected
    execute.assert_awaited_once_with(db=db, request=request)
    assert db.scalar.await_args_list[0].args[0].whereclause.right.value == package_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        (lambda p, a, r, now: setattr(p, "package_state", "READY"), "package_not_activated"),
        (lambda p, a, r, now: setattr(a, "expires_at", now), "activation_not_effective"),
        (lambda p, a, r, now: setattr(a, "package_id", uuid4()), "activation_package_mismatch"),
        (lambda p, a, r, now: setattr(p, "campaign_id", uuid4()), "campaign_identity_mismatch"),
        (lambda p, a, r, now: setattr(p, "campaign_version", 2), "campaign_version_mismatch"),
        (lambda p, a, r, now: setattr(p, "mandate_id", uuid4()), "mandate_identity_mismatch"),
        (lambda p, a, r, now: setattr(p, "mandate_version_id", uuid4()), "mandate_version_mismatch"),
        (lambda p, a, r, now: setattr(p, "provider", "coinbase_advanced"), "execution_scope_mismatch"),
    ],
)
async def test_exact_package_identity_failures(mutation, blocker: str) -> None:
    now, package_id, request, db, package, activation = _case()
    mutation(package, activation, request, now)
    with pytest.raises(InvalidRequestError) as exc:
        await subject.execute_activated_commissioned_entry(db=db, package_id=package_id, request=request, now=now)
    assert exc.value.details["blocker"] == blocker


@pytest.mark.asyncio
async def test_missing_activation_fails_closed() -> None:
    now, package_id, request, db, package, _activation = _case()
    db.scalar = AsyncMock(side_effect=[package, None])
    with pytest.raises(InvalidRequestError) as exc:
        await subject.execute_activated_commissioned_entry(db=db, package_id=package_id, request=request, now=now)
    assert exc.value.details["blocker"] == "activation_missing"
