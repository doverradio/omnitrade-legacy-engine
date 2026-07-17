# OmniTrade Legacy Engine
# NEXT SESSION

Purpose

This document tells the next ChatGPT conversation exactly how to continue the project.

Update it at the end of every major work session.

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

# Next Task

Run the commissioned-campaign production proving window exactly as documented in:

COMMISSIONED_AUTONOMOUS_SEED_CAMPAIGN_ARCHITECTURE.md

Do not activate a campaign during the proving window.

Collect and preserve:

- service health evidence for omnitrade-api.service;
- service health evidence for omnitrade-orchestration.service;
- stable NRestarts and worker PID evidence;
- clean startup-log evidence proving no _STARTED_AT or _RUN_ID failure;
- evidence that no runpy/sys.modules preload warning exists;
- current candle-ingestion evidence;
- commissioned control-plane status evidence;
- Decision Record and Risk Engine evidence visibility;
- reconciliation coherence evidence;
- audit visibility for control-plane mutations.

If the proving window passes, prepare a single explicit operator approval decision for later commissioning.

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

Read:

PROJECT_STATE.md

DECISIONS.md

PROJECT_STATUS.md

PROJECT_CONSTITUTION.md

Then continue implementation from the Next Task section above.

Do not redesign completed work unless a genuine architectural issue is identified.

Do not activate the commissioned campaign without explicit operator approval after the proving window evidence is reviewed.