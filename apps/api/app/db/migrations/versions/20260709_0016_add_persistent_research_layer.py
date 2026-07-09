"""add persistent research layer tables

Revision ID: 20260709_0016
Revises: 20260708_0015
Create Date: 2026-07-09 12:10:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260709_0016"
down_revision: str | None = "20260708_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_laboratory_runs",
        sa.Column("run_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participating_agents", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("generated_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("evaluated_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_research_laboratory_runs_created_at", "research_laboratory_runs", ["created_at"])
    op.create_index("ix_research_laboratory_runs_status", "research_laboratory_runs", ["status"])

    op.create_table(
        "research_campaigns",
        sa.Column("campaign_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participating_agents", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("campaign_id"),
    )
    op.create_index("ix_research_campaigns_created_at", "research_campaigns", ["created_at"])
    op.create_index("ix_research_campaigns_status", "research_campaigns", ["status"])

    op.create_table(
        "research_candidates",
        sa.Column("candidate_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("laboratory_run_id", sa.UUID(), nullable=True),
        sa.Column("campaign_id", sa.UUID(), nullable=True),
        sa.Column("parent_candidate_id", sa.UUID(), nullable=True),
        sa.Column("originating_agent", sa.Text(), nullable=False),
        sa.Column("strategy_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameter_set", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("mutation_reason", sa.Text(), nullable=True),
        sa.Column("parameter_diff", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["research_campaigns.campaign_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["laboratory_run_id"], ["research_laboratory_runs.run_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["research_candidates.candidate_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("candidate_id"),
    )
    op.create_index("ix_research_candidates_created_at", "research_candidates", ["created_at"])
    op.create_index("ix_research_candidates_generated_at", "research_candidates", ["generated_at"])
    op.create_index("ix_research_candidates_laboratory_run_id", "research_candidates", ["laboratory_run_id"])
    op.create_index("ix_research_candidates_campaign_id", "research_candidates", ["campaign_id"])
    op.create_index("ix_research_candidates_parent_candidate_id", "research_candidates", ["parent_candidate_id"])
    op.create_index("ix_research_candidates_originating_agent", "research_candidates", ["originating_agent"])
    op.create_index("ix_research_candidates_generation", "research_candidates", ["generation"])

    op.create_table(
        "research_candidate_lineage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("parent_candidate_id", sa.UUID(), nullable=False),
        sa.Column("mutation_reason", sa.Text(), nullable=True),
        sa.Column("parameter_diff", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_candidates.candidate_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["research_candidates.candidate_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_research_candidate_lineage_candidate_id"),
    )
    op.create_index("ix_research_candidate_lineage_parent_candidate_id", "research_candidate_lineage", ["parent_candidate_id"])
    op.create_index("ix_research_candidate_lineage_created_at", "research_candidate_lineage", ["created_at"])

    op.create_table(
        "research_candidate_evaluations",
        sa.Column("evaluation_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("laboratory_run_id", sa.UUID(), nullable=True),
        sa.Column("replay_status", sa.Text(), nullable=False),
        sa.Column("decision_quality_score", sa.Integer(), nullable=False),
        sa.Column("ai_coach_summary", sa.Text(), nullable=False),
        sa.Column("decision_intelligence_summary", sa.Text(), nullable=False),
        sa.Column("tournament_rank", sa.Integer(), nullable=True),
        sa.Column("promotion_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_candidates.candidate_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["laboratory_run_id"], ["research_laboratory_runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("evaluation_id"),
    )
    op.create_index("ix_research_candidate_evaluations_candidate_id", "research_candidate_evaluations", ["candidate_id"])
    op.create_index("ix_research_candidate_evaluations_created_at", "research_candidate_evaluations", ["created_at"])
    op.create_index("ix_research_candidate_evaluations_laboratory_run_id", "research_candidate_evaluations", ["laboratory_run_id"])
    op.create_index("ix_research_candidate_evaluations_quality", "research_candidate_evaluations", ["decision_quality_score"])
    op.create_index("ix_research_candidate_evaluations_rank", "research_candidate_evaluations", ["tournament_rank"])

    op.create_table(
        "research_memory_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column("laboratory_run_id", sa.UUID(), nullable=True),
        sa.Column("candidate_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["research_candidates.candidate_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["laboratory_run_id"], ["research_laboratory_runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_memory_entries_created_at", "research_memory_entries", ["created_at"])
    op.create_index("ix_research_memory_entries_entry_type", "research_memory_entries", ["entry_type"])
    op.create_index("ix_research_memory_entries_laboratory_run_id", "research_memory_entries", ["laboratory_run_id"])
    op.create_index("ix_research_memory_entries_candidate_id", "research_memory_entries", ["candidate_id"])

    op.create_table(
        "research_agent_activity",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("laboratory_run_id", sa.UUID(), nullable=True),
        sa.Column("campaign_id", sa.UUID(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("activity_type", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["research_campaigns.campaign_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["laboratory_run_id"], ["research_laboratory_runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_agent_activity_created_at", "research_agent_activity", ["created_at"])
    op.create_index("ix_research_agent_activity_agent_name", "research_agent_activity", ["agent_name"])
    op.create_index("ix_research_agent_activity_activity_type", "research_agent_activity", ["activity_type"])
    op.create_index("ix_research_agent_activity_campaign_id", "research_agent_activity", ["campaign_id"])
    op.create_index("ix_research_agent_activity_laboratory_run_id", "research_agent_activity", ["laboratory_run_id"])

    op.create_table(
        "research_campaign_statistics",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("laboratory_runs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("candidates_generated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("candidates_evaluated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("best_candidate_id", sa.UUID(), nullable=True),
        sa.Column("best_quality_score", sa.Integer(), nullable=True),
        sa.Column("current_champion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["best_candidate_id"], ["research_candidates.candidate_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["campaign_id"], ["research_campaigns.campaign_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("campaign_id"),
        sa.UniqueConstraint("campaign_id", name="uq_research_campaign_statistics_campaign_id"),
    )
    op.create_index("ix_research_campaign_statistics_updated_at", "research_campaign_statistics", ["updated_at"])
    op.create_index("ix_research_campaign_statistics_best_quality_score", "research_campaign_statistics", ["best_quality_score"])


def downgrade() -> None:
    op.drop_index("ix_research_campaign_statistics_best_quality_score", table_name="research_campaign_statistics")
    op.drop_index("ix_research_campaign_statistics_updated_at", table_name="research_campaign_statistics")
    op.drop_table("research_campaign_statistics")

    op.drop_index("ix_research_agent_activity_laboratory_run_id", table_name="research_agent_activity")
    op.drop_index("ix_research_agent_activity_campaign_id", table_name="research_agent_activity")
    op.drop_index("ix_research_agent_activity_activity_type", table_name="research_agent_activity")
    op.drop_index("ix_research_agent_activity_agent_name", table_name="research_agent_activity")
    op.drop_index("ix_research_agent_activity_created_at", table_name="research_agent_activity")
    op.drop_table("research_agent_activity")

    op.drop_index("ix_research_memory_entries_candidate_id", table_name="research_memory_entries")
    op.drop_index("ix_research_memory_entries_laboratory_run_id", table_name="research_memory_entries")
    op.drop_index("ix_research_memory_entries_entry_type", table_name="research_memory_entries")
    op.drop_index("ix_research_memory_entries_created_at", table_name="research_memory_entries")
    op.drop_table("research_memory_entries")

    op.drop_index("ix_research_candidate_evaluations_rank", table_name="research_candidate_evaluations")
    op.drop_index("ix_research_candidate_evaluations_quality", table_name="research_candidate_evaluations")
    op.drop_index("ix_research_candidate_evaluations_laboratory_run_id", table_name="research_candidate_evaluations")
    op.drop_index("ix_research_candidate_evaluations_created_at", table_name="research_candidate_evaluations")
    op.drop_index("ix_research_candidate_evaluations_candidate_id", table_name="research_candidate_evaluations")
    op.drop_table("research_candidate_evaluations")

    op.drop_index("ix_research_candidate_lineage_created_at", table_name="research_candidate_lineage")
    op.drop_index("ix_research_candidate_lineage_parent_candidate_id", table_name="research_candidate_lineage")
    op.drop_table("research_candidate_lineage")

    op.drop_index("ix_research_candidates_generation", table_name="research_candidates")
    op.drop_index("ix_research_candidates_originating_agent", table_name="research_candidates")
    op.drop_index("ix_research_candidates_parent_candidate_id", table_name="research_candidates")
    op.drop_index("ix_research_candidates_campaign_id", table_name="research_candidates")
    op.drop_index("ix_research_candidates_laboratory_run_id", table_name="research_candidates")
    op.drop_index("ix_research_candidates_generated_at", table_name="research_candidates")
    op.drop_index("ix_research_candidates_created_at", table_name="research_candidates")
    op.drop_table("research_candidates")

    op.drop_index("ix_research_campaigns_status", table_name="research_campaigns")
    op.drop_index("ix_research_campaigns_created_at", table_name="research_campaigns")
    op.drop_table("research_campaigns")

    op.drop_index("ix_research_laboratory_runs_status", table_name="research_laboratory_runs")
    op.drop_index("ix_research_laboratory_runs_created_at", table_name="research_laboratory_runs")
    op.drop_table("research_laboratory_runs")
