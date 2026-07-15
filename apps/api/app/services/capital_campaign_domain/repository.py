from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capital_campaign_definition import CapitalCampaignDefinition


class CapitalCampaignDomainRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, *, campaign_id: UUID | None = None, status: str | None = None, latest_only: bool = True) -> list[CapitalCampaignDefinition]:
        if latest_only:
            latest_version_subquery = (
                select(
                    CapitalCampaignDefinition.campaign_id,
                    func.max(CapitalCampaignDefinition.version).label("max_version"),
                )
                .group_by(CapitalCampaignDefinition.campaign_id)
                .subquery()
            )
            statement = (
                select(CapitalCampaignDefinition)
                .join(
                    latest_version_subquery,
                    and_(
                        CapitalCampaignDefinition.campaign_id == latest_version_subquery.c.campaign_id,
                        CapitalCampaignDefinition.version == latest_version_subquery.c.max_version,
                    ),
                )
            )
        else:
            statement = select(CapitalCampaignDefinition)

        if campaign_id is not None:
            statement = statement.where(CapitalCampaignDefinition.campaign_id == campaign_id)
        if status is not None:
            statement = statement.where(CapitalCampaignDefinition.status == status)

        statement = statement.order_by(CapitalCampaignDefinition.created_at.desc(), CapitalCampaignDefinition.id.desc())
        return (await self.db.execute(statement)).scalars().all()

    async def get(self, *, campaign_id: UUID, version: int | None = None) -> CapitalCampaignDefinition | None:
        statement = select(CapitalCampaignDefinition).where(CapitalCampaignDefinition.campaign_id == campaign_id)
        if version is not None:
            statement = statement.where(CapitalCampaignDefinition.version == version)
        else:
            statement = statement.order_by(CapitalCampaignDefinition.version.desc())
        statement = statement.limit(1)
        return await self.db.scalar(statement)

    async def next_version(self, *, campaign_id: UUID) -> int:
        max_version = await self.db.scalar(
            select(func.max(CapitalCampaignDefinition.version)).where(CapitalCampaignDefinition.campaign_id == campaign_id)
        )
        return int(max_version or 0) + 1

    async def create(self, definition: CapitalCampaignDefinition) -> CapitalCampaignDefinition:
        self.db.add(definition)
        await self.db.flush()
        await self.db.refresh(definition)
        return definition

    async def touch_updated_at(self, definition: CapitalCampaignDefinition) -> None:
        definition.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(definition)
