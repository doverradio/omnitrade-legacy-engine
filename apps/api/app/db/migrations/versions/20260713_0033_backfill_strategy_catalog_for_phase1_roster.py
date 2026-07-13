"""backfill strategy catalog rows for phase1 roster builtins

Revision ID: 20260713_0033
Revises: 20260713_0032
Create Date: 2026-07-13 23:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_0033"
down_revision: str | None = "20260713_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _insert_strategy_if_missing(*, name: str, slug: str, description: str, module_version: str) -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO strategies (name, slug, description, module_version, is_active)
            VALUES (:name, :slug, :description, :module_version, false)
            ON CONFLICT (slug) DO NOTHING
            """
        ).bindparams(
            name=name,
            slug=slug,
            description=description,
            module_version=module_version,
        )
    )


def upgrade() -> None:
    _insert_strategy_if_missing(
        name="Momentum",
        slug="momentum",
        description="Built-in momentum strategy for deterministic roster shadow evaluation",
        module_version="1.0.0",
    )
    _insert_strategy_if_missing(
        name="Breakout",
        slug="breakout",
        description="Built-in breakout strategy for deterministic roster shadow evaluation",
        module_version="1.0.0",
    )
    _insert_strategy_if_missing(
        name="Mean Reversion",
        slug="mean_reversion",
        description="Built-in mean reversion strategy for deterministic roster shadow evaluation",
        module_version="1.0.0",
    )
    _insert_strategy_if_missing(
        name="Bollinger Reversion",
        slug="bollinger_reversion",
        description="Built-in Bollinger band reversion strategy for deterministic roster shadow evaluation",
        module_version="1.0.0",
    )
    _insert_strategy_if_missing(
        name="Donchian Breakout",
        slug="donchian_breakout",
        description="Built-in Donchian channel breakout strategy for deterministic roster shadow evaluation",
        module_version="1.0.0",
    )


def downgrade() -> None:
    # Data backfill migration is intentionally non-destructive on downgrade.
    # Deleting by slug would risk removing legitimately adopted catalog rows.
    return None
