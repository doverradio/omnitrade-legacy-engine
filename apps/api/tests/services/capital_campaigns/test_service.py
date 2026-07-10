from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest

from app.core.errors import InvalidRequestError
from app.core.errors import NotFoundError
from app.models.capital_campaign import CapitalCampaign
from app.schemas.capital_campaigns import CapitalCampaignCreateRequest, CapitalCampaignUpdateRequest
from app.services.capital_campaigns import service


class _DummyDb:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class _FakeRepository:
    def __init__(self) -> None:
        self.created: CapitalCampaign | None = None
        self.updated: dict[str, object] | None = None
        self.campaign: CapitalCampaign | None = None

    async def list(self, *, status: str | None = None, owner: str | None = None):
        _ = (status, owner)
        return [self.campaign] if self.campaign is not None else []

    async def get_by_uuid(self, campaign_uuid: uuid.UUID):
        _ = campaign_uuid
        return self.campaign

    async def create(self, campaign: CapitalCampaign):
        campaign.id = 1
        campaign.uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        campaign.created_at = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
        campaign.updated_at = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
        self.created = campaign
        self.campaign = campaign
        return campaign

    async def update(self, campaign: CapitalCampaign, *, changed_fields: dict[str, object]):
        self.updated = dict(changed_fields)
        for key, value in changed_fields.items():
            setattr(campaign, key, value)
        campaign.updated_at = datetime(2026, 7, 10, 15, 10, tzinfo=timezone.utc)
        self.campaign = campaign
        return campaign

    async def delete(self, campaign: CapitalCampaign):
        _ = campaign
        self.campaign = None

    async def paper_account_exists(self, account_id: uuid.UUID) -> bool:
        _ = account_id
        return True

    async def validation_run_exists(self, validation_run_id: uuid.UUID) -> bool:
        _ = validation_run_id
        return True

    async def strategy_exists(self, strategy_id: uuid.UUID) -> bool:
        _ = strategy_id
        return True


@pytest.mark.asyncio
async def test_create_campaign_sets_default_equity_and_roi(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    db = _DummyDb()
    response = await service.create_capital_campaign(
        db=db,
        request=CapitalCampaignCreateRequest(
            owner="owner-1",
            name="Campaign A",
            campaign_type="paper_validation",
            starting_capital=Decimal("25"),
        ),
    )

    assert db.committed is True
    assert response.current_equity == Decimal("25")
    assert response.roi == Decimal("0")


@pytest.mark.asyncio
async def test_update_campaign_recomputes_roi(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    fake_repo.campaign = CapitalCampaign(
        id=1,
        uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        owner="owner-1",
        name="Campaign A",
        description=None,
        status="RUNNING",
        campaign_type="paper_validation",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    db = _DummyDb()
    response = await service.update_capital_campaign(
        db=db,
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(current_equity=Decimal("30")),
    )

    assert db.committed is True
    assert response.current_equity == Decimal("30")
    assert response.roi == Decimal("20")


@pytest.mark.asyncio
async def test_create_campaign_validates_missing_relationship(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()

    async def _missing(_account_id: uuid.UUID) -> bool:
        return False

    fake_repo.paper_account_exists = _missing  # type: ignore[method-assign]
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    with pytest.raises(InvalidRequestError):
        await service.create_capital_campaign(
            db=_DummyDb(),
            request=CapitalCampaignCreateRequest(
                owner="owner-1",
                name="Campaign A",
                campaign_type="paper_validation",
                paper_account_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                starting_capital=Decimal("25"),
            ),
        )


@pytest.mark.asyncio
async def test_create_campaign_rejects_negative_current_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    with pytest.raises(InvalidRequestError, match="current_equity"):
        await service.create_capital_campaign(
            db=_DummyDb(),
            request=CapitalCampaignCreateRequest(
                owner="owner-1",
                name="Campaign A",
                campaign_type="paper_validation",
                starting_capital=Decimal("25"),
                current_equity=Decimal("-1"),
            ),
        )


@pytest.mark.asyncio
async def test_update_campaign_rejects_owner_change(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    fake_repo.campaign = CapitalCampaign(
        id=1,
        uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        owner="owner-1",
        name="Campaign A",
        description=None,
        status="RUNNING",
        campaign_type="paper_validation",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    with pytest.raises(InvalidRequestError, match="owner is immutable"):
        await service.update_capital_campaign(
            db=_DummyDb(),
            campaign_uuid=fake_repo.campaign.uuid,
            request=CapitalCampaignUpdateRequest(owner="owner-2"),
        )


@pytest.mark.asyncio
async def test_status_transition_rules_allow_expected_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    fake_repo.campaign = CapitalCampaign(
        id=1,
        uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        owner="owner-1",
        name="Campaign A",
        description=None,
        status="DRAFT",
        campaign_type="paper_validation",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="READY"),
    )
    assert fake_repo.campaign.status == "READY"

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="RUNNING"),
    )
    assert fake_repo.campaign.status == "RUNNING"

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="PAUSED"),
    )
    assert fake_repo.campaign.status == "PAUSED"

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="RUNNING"),
    )
    assert fake_repo.campaign.status == "RUNNING"

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="TARGET_REACHED"),
    )
    assert fake_repo.campaign.status == "TARGET_REACHED"

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="COMPLETED"),
    )
    assert fake_repo.campaign.status == "COMPLETED"

    await service.update_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
        request=CapitalCampaignUpdateRequest(status="ARCHIVED"),
    )
    assert fake_repo.campaign.status == "ARCHIVED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("ARCHIVED", "RUNNING"),
        ("COMPLETED", "DRAFT"),
        ("TARGET_REACHED", "READY"),
    ],
)
async def test_status_transition_rules_block_invalid_paths(
    monkeypatch: pytest.MonkeyPatch,
    from_status: str,
    to_status: str,
) -> None:
    fake_repo = _FakeRepository()
    fake_repo.campaign = CapitalCampaign(
        id=1,
        uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        owner="owner-1",
        name="Campaign A",
        description=None,
        status=from_status,
        campaign_type="paper_validation",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    with pytest.raises(InvalidRequestError, match="Invalid status transition"):
        await service.update_capital_campaign(
            db=_DummyDb(),
            campaign_uuid=fake_repo.campaign.uuid,
            request=CapitalCampaignUpdateRequest(status=to_status),
        )


@pytest.mark.asyncio
async def test_delete_campaign_archives_non_destructively(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    fake_repo.campaign = CapitalCampaign(
        id=1,
        uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        owner="owner-1",
        name="Campaign A",
        description=None,
        status="PAUSED",
        campaign_type="paper_validation",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    await service.delete_capital_campaign(
        db=_DummyDb(),
        campaign_uuid=fake_repo.campaign.uuid,
    )

    assert fake_repo.campaign is not None
    assert fake_repo.campaign.status == "ARCHIVED"


@pytest.mark.asyncio
async def test_get_missing_campaign_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = _FakeRepository()
    monkeypatch.setattr(service, "CapitalCampaignRepository", lambda _db: fake_repo)

    with pytest.raises(NotFoundError):
        await service.get_capital_campaign(
            db=_DummyDb(),
            campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        )
