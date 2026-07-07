from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.paper_account import PaperAccount


DEFAULT_OWNER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_STARTING_BALANCE = Decimal("25")
SUPPORTED_ASSET_CLASSES = ("crypto", "stock")


@dataclass(slots=True)
class SeedSummary:
    created_asset_classes: list[str] = field(default_factory=list)


async def _active_account_exists(db_session: AsyncSession, *, asset_class: str) -> bool:
    account_id = await db_session.scalar(
        select(PaperAccount.id)
        .where(PaperAccount.asset_class == asset_class)
        .where(PaperAccount.is_active.is_(True))
        .limit(1)
    )
    return account_id is not None


async def seed_paper_accounts(db_session: AsyncSession) -> SeedSummary:
    summary = SeedSummary()

    for asset_class in SUPPORTED_ASSET_CLASSES:
        if await _active_account_exists(db_session, asset_class=asset_class):
            continue

        db_session.add(
            PaperAccount(
                owner_user_id=DEFAULT_OWNER_USER_ID,
                name=asset_class,
                asset_class=asset_class,
                starting_balance=DEFAULT_STARTING_BALANCE,
                current_cash_balance=DEFAULT_STARTING_BALANCE,
                is_active=True,
            )
        )
        summary.created_asset_classes.append(asset_class)

    await db_session.commit()
    return summary


def print_summary(summary: SeedSummary) -> None:
    if not summary.created_asset_classes:
        print("Paper accounts already exist.")
        return

    for asset_class in summary.created_asset_classes:
        print(f"Created paper account: {asset_class}")


async def _async_main() -> int:
    setup_logging()

    async with AsyncSessionLocal() as db_session:
        summary = await seed_paper_accounts(db_session)

    print_summary(summary)
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
