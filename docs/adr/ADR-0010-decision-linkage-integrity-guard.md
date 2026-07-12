# ADR-0010: Decision Linkage Integrity Guard

## Status
Accepted

## Context
Production evidence confirmed a persisted preview decision with status `RISK_REJECTED` where `decision_record_id`, `risk_event_id`, and `preview_id` were all `NULL`. Audit events proved the workflow happened, but did not provide enough forensic detail to prove why linkage persistence was skipped at runtime.

This creates a reliability gap: Decision Intelligence invariants can be violated silently unless the system emits explicit integrity diagnostics at write time and provides a read-only verifier for ongoing checks.

## Decision
Adopt a non-blocking Decision Linkage Integrity Guard for preview decision persistence.

The guard:
- Runs during preview persistence paths (`RISK_REJECTED`, `PREVIEW_FAILED`, `PREVIEW_READY`).
- Verifies linkage invariants across `CryptoOrderPreview`, `DecisionRecord`, and `RiskEvent` references.
- Emits structured diagnostic audit events (`decision_linkage_integrity_violation`, `decision_linkage_integrity_guard_error`) under `entity_type=decision_linkage_integrity` when invariants fail or guard execution errors.
- Does not change risk, provider, or execution control flow in this phase.

A read-only verifier is added as `python -m scripts.verify_decision_linkage_integrity`:
- Scans persisted preview decisions.
- Separates future violations from historical exemptions using a documented feature cutoff.
- Returns non-zero only for future violations, not historical exemptions.

Inspector integration surfaces integrity violations explicitly as warnings and includes guard events in audit timeline context.

## Alternatives Considered
1. Fail-closed write path on any integrity mismatch.
- Rejected for this phase because it would change production execution behavior and may block operator workflows before enough operational evidence is collected.

2. Backfill historical missing linkage rows.
- Rejected by policy: historical mutation/backfill is disallowed in this phase.

3. Post-hoc batch checks only (no write-time diagnostics).
- Rejected because silent violations would still occur between checks.

## Consequences
Positive:
- Future linkage violations are no longer silent.
- Operators and auditors can see explicit integrity warnings in Inspector.
- Reliability checks are repeatable via a read-only script suitable for production auditing.

Trade-offs:
- Guard is diagnostic, not fail-closed; invalid rows can still persist in exceptional paths, but are now explicitly detectable.
- Additional audit-log volume from integrity events.

Follow-up:
- After sufficient production evidence, evaluate moving from non-blocking diagnostics to selective fail-closed enforcement for high-confidence invariants.
