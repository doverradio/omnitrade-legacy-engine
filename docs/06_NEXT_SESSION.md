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

Task 10 is complete.

The next session should not redesign commissioned architecture.

The next session should execute the read-only production proving-window evidence collection and prepare an explicit go/no-go recommendation.

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

Investigate the Risk Engine rejection path.

Determine why production BUY proposals are rejected.

Collect:

• Risk rejection reasons
• Risk rule identifiers
• Position sizing calculations
• Minimum order calculations
• Campaign authorization state
• Account balance
• Existing position state
• Cooldown status
• Drawdown status

Determine whether the rejection is expected
or caused by configuration.

Do not loosen any production safety rule until
the exact rejection reason is proven.

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