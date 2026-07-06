"""add decision ingestion metadata

Revision ID: 20260706_0008
Revises: 20260706_0007
Create Date: 2026-07-06 05:00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260706_0008"
down_revision: str | None = "20260706_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_records",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "decision_records",
        sa.Column(
            "source_lineage",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "decision_records",
        sa.Column(
            "field_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.execute("UPDATE decision_records SET idempotency_key = decision_id::text WHERE idempotency_key IS NULL")
    op.alter_column("decision_records", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_decision_records_idempotency_key",
        "decision_records",
        ["idempotency_key"],
    )

    op.alter_column("decision_records", "source_lineage", server_default=None)
    op.alter_column("decision_records", "field_provenance", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_decision_records_idempotency_key", "decision_records", type_="unique")
    op.drop_column("decision_records", "field_provenance")
    op.drop_column("decision_records", "source_lineage")
    op.drop_column("decision_records", "idempotency_key")