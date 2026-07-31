# OmniTrade Legacy Engine
# NEXT SESSION

Purpose

This document tells the next ChatGPT conversation exactly how to continue the project.

Update it at the end of every major work session.

---

GOLDEN RULES

1. Read the governing documents first.

2. Believe runtime evidence over assumptions.

3. Preserve the Constitution.

4. Preserve Risk Engine authority.

5. Small deterministic changes.

6. Validate before commit.

7. Commit → Push → VPS Pull whenever implementation is complete.

8. Local commands first.

9. VPS commands second.

10. Every command block ends with one blank line.

---

# Current Status

The architecture documents are considered stable.

The Constitution is stable.

The Vision is stable.

The Roadmap is stable.

Avoid redesigning completed architecture unless a genuine defect is discovered.

---

# Current Focus

Controlled Proof #3 (`ef0ca4df-e520-4764-b6a5-71bcf165f43a`) remains
`BLOCKED` with verified reconciled BUY exposure and no SELL lineage. Its single
Exit Recovery POST returned HTTP `400` with `Controlled Proof is not eligible
for exit recovery` and `details.status=BLOCKED`; no recovery was created and
the POST must not be repeated before the reviewed correction is deployed.
The local correction conditionally admits `BLOCKED` without changing proof
status or restoring BUY authority, and requires exact agreement between
canonical proof-owned quantity, scoped position quantity, and profile custody.
Next action is review, deploy, then read-only validation; authorization remains
a separate operator checkpoint.

That authorization produced recovery `bf2c040d-4ee6-4091-adeb-9dbb633d2b65`,
which was claimed and passed SELL eligibility plus Risk ALLOW but blocked on
the expired original CONTROLLED_PROOF mandate before creating a SELL package.
It is terminal and must not be resumed or replayed. The reviewed correction
must be deployed first; after read-only validation, exactly one replacement
recovery with a new persisted key may be considered. Continuing exit authority
is typed, recovery-bound, SELL-only, and does not restore entry authority.
Proof #3 remains outside the clean five-proof streak (still 0 of 5).
Preserve the historical recovery's pre-fix `completed_at=null` as immutable
legacy evidence. It remains unclaimable and does not block a different-key
replacement because only AUTHORIZED/IN_PROGRESS rows are unique per proof.

The first replacement (`31a927a6-f7ea-4ea2-9966-426bfe659b64`) is also
terminal BLOCKED and must not be replayed. Continuing recovery context reached
mandate evaluation, but `controlled_proof_open_exposure_limit` still treated
already-owned fill-value exposure as an entry violation during SELL. The local
correction treats SELL as zero incremental exposure while preserving the full
BUY calculation. A third recovery is prohibited until review and deployment.

The external reconciliation investigation is complete.

The PACKAGE_ONLY SELL progression defect has been repaired,
deployed, and production validated.

The Controlled Proof authority-propagation defect
(`GLOBAL_CONFIGURED_SCOPE controlled_proof_id=None`) is
production-confirmed fixed: a fresh Exit Recovery dispatch reached
`CONTROLLED_PROOF_DERIVED_SCOPE` with the real `controlled_proof_id`.

That evidence surfaced a second, deeper defect immediately downstream:
activation then failed closed with
`controlled_proof_activation_override_blocked reason=controlled_proof_not_active`,
and the claimed recovery stayed `IN_PROGRESS` indefinitely with no
recorded reason. Root cause and fix are implemented, unit-tested, and
documented in `docs/00_OPERATIONS_MAP.md`'s Controlled Proof Exit
Recovery section — in short: the eligibility check wrongly required a
SELL package's creation-time recovery-id stamp to match the currently
claimed recovery, which is never true for Exit Recovery's documented
"resume a pre-existing SELL package" case; and a claimed recovery that
fails to activate now always gets an explicit `BLOCKED`/retryable
reason instead of sitting silently `IN_PROGRESS`. **Not yet deployed or
production-validated.**

The next session should: (1) deploy this correction, (2) production-
validate it with a **fresh** Exit Recovery authorization (the two ids
already used for diagnosis are now terminal/stale for retry purposes),
then (3) continue following the autonomous lifecycle from automatic
package activation through execution, reconciliation, accounting
completion, and First Autonomous Profit.

---

# Operator Preferences (Permanent)

These preferences are considered part of the OmniTrade operator workflow.

Every future ChatGPT session should follow them automatically.

## Command Formatting

Whenever providing development commands:

1. Always provide LOCAL commands first.

2. Always provide VPS commands second.

3. Combine related commands into a single copy block whenever practical.

4. Prefer fail-fast command chains using:

&&

Use ";" only when later commands should continue even if an earlier command fails.

5. Every command block MUST end with one completely blank line.

This allows the operator to paste once and immediately execute.

Never omit the trailing blank line.

Example:

```bash
git add .
git commit -m "Commit message"
git push

```

(the blank line after the last command is intentional)

## Git Workflow

Whenever recommending a commit, always include BOTH:

LOCAL

```bash
git add .
git commit -m "Descriptive message"
git push

```

VPS

```bash
cd ~/omnitrade-legacy-engine && \
git pull

```

Do not wait for the operator to ask for these.

Assume they are wanted every time a commit is recommended.

After every implementation recommendation:

1. Validate

2. Commit

3. Push

4. Pull on VPS

Do not stop after the code change.

Assume deployment is part of the task unless explicitly stated otherwise.

## Editing

Always use:

vi

Never recommend nano.

## Migrations

Never recommend:

alembic upgrade head

unless the implementation actually introduced a new Alembic migration.

If no migration exists, explicitly state:

"No Alembic upgrade is required."


## Production Alembic Procedure

Production does not use apps/api/.venv.

Canonical production runtime:

- Python: /home/eric/miniconda3/envs/omnitrade311/bin/python3.11
- API working directory: /home/eric/omnitrade-legacy-engine/apps/api
- Environment file: /home/eric/omnitrade-legacy-engine/apps/api/.env

Read-only migration check:

cd /home/eric/omnitrade-legacy-engine/apps/api
PYTHON=/home/eric/miniconda3/envs/omnitrade311/bin/python3.11
set -a
source .env
set +a
PYTHONPATH=. "$PYTHON" -m alembic -c alembic.ini current

Apply migrations:

cd /home/eric/omnitrade-legacy-engine/apps/api
PYTHON=/home/eric/miniconda3/envs/omnitrade311/bin/python3.11
set -a
source .env
set +a
PYTHONPATH=. "$PYTHON" -m alembic -c alembic.ini upgrade head

Always verify the resulting revision after migration.
Do not assume a .venv path on production.

## Engineering Style

Prefer:

• deterministic implementations

• small bounded implementation tasks

• evidence before assumptions

• runtime proof before new features

Avoid giant speculative prompts.


## Evidence Hierarchy

When runtime evidence conflicts with assumptions,
always trust runtime evidence.

Never attempt to "fix" behavior until the actual
runtime cause has been identified.

Prefer investigation over implementation.


## Reasoning

Never omit the reasoning.

Whenever recommending a change, briefly explain:

• why this is the next step

• what evidence supports it

• what success will look like

before providing commands.


## Production

Never recommend bypassing:

• Risk Engine

• audit logging

• campaign identity

• safety gates

• explainability

• replay evidence

Production evidence is more valuable than speculative architecture.
---

# Next Task

The external reconciliation investigation is complete.

The PACKAGE_ONLY retry defect has been repaired, tested, deployed, and
verified in production.

Commit deployed:

3da57cd

Production runtime now proves:

• Exit Recovery authorization succeeds.

• Exit Recovery is claimed by the orchestration worker.

• The worker rediscovers the READY SELL package.

• Ordinary supervision retries SELL package progression.

• SELL package progression reaches automatic activation.

The current production blocker is:

automatic_activation_mandate_scope_mismatch

Runtime logs further show:

automatic_activation_scope_resolved

authority_mode=GLOBAL_CONFIGURED_SCOPE

controlled_proof_id=None

during Controlled Proof Exit Recovery.

Mission

Determine exactly why the Controlled Proof authority context is lost
before automatic package activation.

Implement the smallest production-safe correction that preserves:

• Constitution

• Risk Engine authority

• mandate governance

• immutable audit evidence

• idempotency

• fail-closed behavior

Do not bypass governance.

Do not weaken mandate authority.

Do not fabricate runtime evidence.

Prefer repository evidence and production runtime evidence over
assumptions.

Only implement the smallest targeted correction once the exact authority
propagation defect has been conclusively identified.

---

# Current Risks

1. Non-commissioned arena Risk Gate fake-session fixture drift.
2. Non-commissioned signal-orchestrator fake-result fixture drift.
3. Non-commissioned validation-run status fixture drift.
4. Research and analytics test-state contamination.
5. Paper realized-PnL expectation drift.
6. Async cancellation and event-loop teardown warning noise.
7. FastAPI startup-event deprecation warnings.

---

# Do NOT Revisit

Do not redesign existing architecture merely for elegance.

Do not rename major components without strong justification.

Do not create duplicate documentation.

Do not bypass the Risk Engine.

Do not weaken auditability.

Do not remove explainability.

---

# Copilot Workflow

Always provide:

• Local commands first.

• VPS commands second.

Commands should be in a single copy block whenever practical.

Use vi instead of nano.

Prefer deterministic implementations.

Validate before committing.

Never skip tests.

Never recommend risky production shortcuts.

---

# Session Completion Checklist

Before ending a work session:

□ Update PROJECT_STATE.md if milestone changed.

□ Append major architectural decisions to DECISIONS.md.

□ Update this file with the next immediate task.

□ Confirm project remains aligned with the Constitution.

□ If Task 10 changed, keep the handoff package aligned with deployed service names and the worker entrypoint.

---

# First Prompt For Next Chat

Read in this order:

1. PROJECT_STATE.md
2. DECISIONS.md
3. NEXT_SESSION.md
4. PROJECT_STATUS.md
5. PROJECT_CONSTITUTION.md

Treat these documents as authoritative unless runtime evidence clearly supersedes them.

Then continue implementation from the Next Task section above.

Do not redesign completed work unless a genuine architectural issue is identified.

Do not activate the commissioned campaign without explicit operator approval after the proving window evidence is reviewed.

Important:

The external reconciliation investigation is complete.

The PACKAGE_ONLY SELL progression retry was deployed in commit 3da57cd.

The Controlled Proof authority-propagation correction is already
production-confirmed. The recovery-aware activation-eligibility and
truthful recovery-terminalization corrections are now implemented and
tested but have not yet been committed, deployed, or production-validated.

Begin by reviewing and committing the existing working-tree changes.
Then deploy them and authorize exactly one fresh Exit Recovery for the
existing proof. Validate automatic activation, execution claim, provider
submission, reconciliation, accounting, and the terminal Controlled Proof
and Exit Recovery outcomes.

Do not re-investigate external reconciliation or PACKAGE_ONLY retry
unless new runtime evidence directly contradicts the established findings.
