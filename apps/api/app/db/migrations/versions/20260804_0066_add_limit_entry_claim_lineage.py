"""add canonical package/activation/claim/custody lineage columns to autonomous_limit_entry_attempts

Revision ID: 20260804_0066
Revises: 20260803_0065
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_0066"
down_revision: str | None = "20260803_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These four columns give a BUY_LIMIT attempt an authoritative lineage
    # into the SAME canonical package/claim/custody machinery that already
    # governs autonomous market BUYs -- established once the attempt
    # reaches submission (see autonomous_limit_entry_worker.py's
    # _establish_claim_lineage). All nullable: an attempt that never
    # reaches submission (REJECTED at the Risk stage, for example) never
    # acquires any of them, and no existing row is affected.
    op.add_column("autonomous_limit_entry_attempts", sa.Column("paper_account_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("autonomous_limit_entry_attempts", sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("autonomous_limit_entry_attempts", sa.Column("activation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("autonomous_limit_entry_attempts", sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("autonomous_limit_entry_attempts", sa.Column("custody_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_alea_package", "autonomous_limit_entry_attempts", "canonical_preview_packages",
        ["package_id"], ["package_id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_alea_activation", "autonomous_limit_entry_attempts", "canonical_proving_activations",
        ["activation_id"], ["activation_id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_alea_claim", "autonomous_limit_entry_attempts", "autonomous_execution_claims",
        ["claim_id"], ["claim_id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_alea_custody", "autonomous_limit_entry_attempts", "autonomous_position_custodies",
        ["custody_id"], ["custody_id"], ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_alea_claim_id", "autonomous_limit_entry_attempts", ["claim_id"])
    op.create_unique_constraint("uq_alea_package_id", "autonomous_limit_entry_attempts", ["package_id"])


def downgrade() -> None:
    op.drop_constraint("uq_alea_package_id", "autonomous_limit_entry_attempts", type_="unique")
    op.drop_constraint("uq_alea_claim_id", "autonomous_limit_entry_attempts", type_="unique")
    op.drop_constraint("fk_alea_custody", "autonomous_limit_entry_attempts", type_="foreignkey")
    op.drop_constraint("fk_alea_claim", "autonomous_limit_entry_attempts", type_="foreignkey")
    op.drop_constraint("fk_alea_activation", "autonomous_limit_entry_attempts", type_="foreignkey")
    op.drop_constraint("fk_alea_package", "autonomous_limit_entry_attempts", type_="foreignkey")
    op.drop_column("autonomous_limit_entry_attempts", "custody_id")
    op.drop_column("autonomous_limit_entry_attempts", "claim_id")
    op.drop_column("autonomous_limit_entry_attempts", "activation_id")
    op.drop_column("autonomous_limit_entry_attempts", "package_id")
    op.drop_column("autonomous_limit_entry_attempts", "paper_account_id")
