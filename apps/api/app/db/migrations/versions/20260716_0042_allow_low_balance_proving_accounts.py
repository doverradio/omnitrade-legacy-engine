"""allow low balance proving accounts

Revision ID: 20260716_0042
Revises: 20260715_0041
Create Date: 2026-07-16 00:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260716_0042"
down_revision: str | None = "20260715_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.drop_constraint("ck_paper_accounts_starting_balance_min", "paper_accounts", type_="check")
	op.create_check_constraint(
		"ck_paper_accounts_starting_balance_min",
		"paper_accounts",
		"starting_balance >= 0",
	)


def downgrade() -> None:
	bind = op.get_bind()
	low_balance_count = int(
		bind.execute(
			sa.text("SELECT COUNT(*) FROM paper_accounts WHERE starting_balance < 25")
		).scalar_one()
	)
	if low_balance_count > 0:
		raise RuntimeError(
			"Cannot downgrade 20260716_0042: paper_accounts contains rows with starting_balance < 25"
		)

	op.drop_constraint("ck_paper_accounts_starting_balance_min", "paper_accounts", type_="check")
	op.create_check_constraint(
		"ck_paper_accounts_starting_balance_min",
		"paper_accounts",
		"starting_balance >= 25",
	)
