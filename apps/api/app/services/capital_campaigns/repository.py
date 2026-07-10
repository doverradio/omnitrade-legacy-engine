from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capital_campaign import CapitalCampaign
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.models.validation_run import ValidationRun


class CapitalCampaignRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, *, status: str | None = None, owner: str | None = None) -> list[CapitalCampaign]:
        statement = select(CapitalCampaign).order_by(CapitalCampaign.created_at.desc(), CapitalCampaign.id.desc())
        if status:
            statement = statement.where(CapitalCampaign.status == status)
        if owner:
            statement = statement.where(CapitalCampaign.owner == owner)
        return (await self.db.execute(statement)).scalars().all()

    async def get_by_uuid(self, campaign_uuid: uuid.UUID) -> CapitalCampaign | None:
        return await self.db.scalar(
            select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_uuid).limit(1)
        )

    async def create(self, campaign: CapitalCampaign) -> CapitalCampaign:
        self.db.add(campaign)
        await self.db.flush()
        await self.db.refresh(campaign)
        return campaign

    async def update(self, campaign: CapitalCampaign, *, changed_fields: dict[str, object]) -> CapitalCampaign:
        for key, value in changed_fields.items():
            setattr(campaign, key, value)
        campaign.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(campaign)
        return campaign

    async def delete(self, campaign: CapitalCampaign) -> None:
        await self.db.execute(delete(CapitalCampaign).where(CapitalCampaign.id == campaign.id))

    async def paper_account_exists(self, account_id: uuid.UUID) -> bool:
        exists = await self.db.scalar(select(PaperAccount.id).where(PaperAccount.id == account_id).limit(1))
        return exists is not None

    async def validation_run_exists(self, validation_run_id: uuid.UUID) -> bool:
        exists = await self.db.scalar(
            select(ValidationRun.validation_run_id).where(ValidationRun.validation_run_id == validation_run_id).limit(1)
        )
        return exists is not None

    async def strategy_exists(self, strategy_id: uuid.UUID) -> bool:
        exists = await self.db.scalar(select(Strategy.id).where(Strategy.id == strategy_id).limit(1))
        return exists is not None
