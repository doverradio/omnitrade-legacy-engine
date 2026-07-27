"""replace autonomous execution claim campaign-version uniqueness with an
active-scope partial index

Revision ID: 20260727_0053
Revises: 20260726_0052

The original uq_autonomous_execution_claim_campaign_version (migration
20260724_0048) is a plain, table-wide UNIQUE(campaign_id, campaign_version)
-- it permits at most one AutonomousExecutionClaim row EVER for a given
campaign version, regardless of that row's claim_status. Since every
Controlled Proof shares one pinned campaign_id/campaign_version, this made
it impossible for any second Controlled Proof to ever claim again once the
first claim existed, even after that claim reached a fully resolved,
provider-never-called state (SAFETY_DISABLED / FAILED_PRE_PROVIDER) --
confirmed production root cause of autonomous_execution_claim_skipped
reason=claim_concurrency_conflict.

Replaces it with a partial unique index scoped to claim_status values that
represent a genuinely unresolved provider-submission outcome (see
_CLAIM_SCOPE_NONTERMINAL_STATES in
app/services/orchestration/autonomous_execution_claims.py, which this
index's WHERE clause must stay in sync with): at most one such claim may
exist per (campaign_id, campaign_version) at a time -- the real invariant
this table exists to protect (no two simultaneous, still-live claims
racing toward the same provider submission). A claim whose provider
outcome is already fully resolved (or definitively never attempted) no
longer occupies the scope, so a later, legitimate sequential Controlled
Proof can claim again. No row is deleted, updated, or rewritten; all
historical claims and their audit trail remain exactly as they are.

BLOCKED is deliberately not in the nonterminal set: its name describes a
permanent, non-recoverable pre-provider stop (the same shape as
FAILED_PRE_PROVIDER/SAFETY_DISABLED), not an in-progress state, so a claim
that ever reaches it must not reserve the campaign scope forever either.
RECOVERY_REQUIRED (name implies active, unresolved recovery) stays
nonterminal.

Preflight (run before deploying, to see whether any currently-active claim
would violate the new partial index -- expected either empty, or exactly
one row per (campaign_id, campaign_version): the OLD constraint already
limited each campaign version to at most one row of any status, so more
than one row per campaign version here would indicate the OLD constraint
was itself somehow bypassed and needs investigation before proceeding.
Exactly one row is a legitimate, currently-active claim -- it will
correctly continue to block a same-scope claim from being created until it
resolves; that is not a reason to stop the deployment):

    SELECT claim_id, package_id, campaign_id, campaign_version, claim_status,
           claim_owner, claimed_at
    FROM autonomous_execution_claims
    WHERE claim_status IN (
        'CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING',
        'RECONCILIATION_REQUIRED','RECOVERY_REQUIRED'
    )
    ORDER BY campaign_id, campaign_version, claimed_at;
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0053"
down_revision: str | None = "20260726_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NONTERMINAL_STATES_SQL = (
    "claim_status IN ('CLAIMED','EXECUTION_STARTED','SUBMISSION_PENDING','RECONCILIATION_REQUIRED',"
    "'RECOVERY_REQUIRED')"
)


def upgrade() -> None:
    op.drop_constraint(
        "uq_autonomous_execution_claim_campaign_version",
        "autonomous_execution_claims",
        type_="unique",
    )
    op.create_index(
        "uq_aec_active_campaign_scope",
        "autonomous_execution_claims",
        ["campaign_id", "campaign_version"],
        unique=True,
        postgresql_where=sa.text(_NONTERMINAL_STATES_SQL),
    )


def downgrade() -> None:
    op.drop_index("uq_aec_active_campaign_scope", table_name="autonomous_execution_claims")
    op.create_unique_constraint(
        "uq_autonomous_execution_claim_campaign_version",
        "autonomous_execution_claims",
        ["campaign_id", "campaign_version"],
    )
