# RESEARCH_EVOLUTION_AND_DECISION_ARENA_ACTIVATION_CLAUDE_PROMPTS.md

# Research Evolution & Decision Arena Activation
## Claude Implementation Prompt Pack

Version: 1.0

Purpose:

This document contains the implementation sequence for activating OmniTrade's existing Research Evolution architecture.

These prompts intentionally **build upon the existing repository**.

They are **not** intended to redesign or replace previously implemented systems.

The objective is to activate the complete autonomous research pipeline while preserving existing architecture, governance, and production stability.

---

# General Instructions (Include With Every Prompt)

Before making any code changes:

1. Read the existing implementation.
2. Compare the repository against:
   - RESEARCH_EVOLUTION_AND_DECISION_ARENA_ACTIVATION.md
   - AI_RESEARCH_AND_EVOLUTION_ENGINE.md
   - RESEARCH_AGENT_FRAMEWORK.md
   - RESEARCH_LABORATORY.md
   - RESEARCH_EVOLUTION_OPERATIONS.md
   - STRATEGY_ENGINE.md
3. Reuse existing code wherever possible.
4. Do not duplicate existing implementations.
5. Preserve backward compatibility.
6. Preserve deterministic behavior.
7. Maintain replay reproducibility.
8. Risk Engine remains final authority.
9. Human approval remains mandatory for production promotion.

Every response must include:

- Architecture findings
- Files modified
- Files created
- Database migrations (if any)
- Tests executed
- Test results
- Remaining work
- Deployment instructions
- Rollback considerations

Do not commit changes unless explicitly instructed.

---

# Prompt 1
## Repository Reconciliation Audit

Review the repository against the new activation specification.

Determine:

- Which research components already exist.
- Which architecture documents remain accurate.
- Which documents are partially outdated.
- Which systems are already implemented but disconnected.
- Which activation gaps remain.

Do not write production code.

Produce a detailed implementation plan that references existing repository components instead of proposing duplicate implementations.

STOP after the audit.

---

# Prompt 2
## Activate Candidate Lifecycle

Review the current candidate lifecycle.

Implement or complete any missing lifecycle states:

- PROPOSED
- VALIDATED
- REPLAY_QUALIFIED
- BACKTEST_QUALIFIED
- ARENA_ACTIVE
- PROMOTION_CANDIDATE
- PRODUCTION_APPROVED
- ACTIVE
- UNDER_REVIEW
- RETIRED

Ensure:

- immutable transitions
- complete audit history
- replay compatibility
- backward compatibility

Do not modify production execution.

STOP after implementation.

---

# Prompt 3
## Activate Decision Arena

Review the existing Decision Arena implementation.

Complete any missing orchestration so that qualified strategies compete under identical conditions.

Verify:

- synchronized timestamps
- identical market data
- identical fees
- identical slippage assumptions
- identical risk constraints

Arena execution must remain paper-only.

STOP after implementation.

---

# Prompt 4
## Tournament Engine Activation

Review the tournament implementation.

Complete any missing functionality for:

- persistent rankings
- cumulative scoring
- evidence accumulation
- historical leaderboards
- no forced champion selection

If evidence thresholds are not met:

No champion should be declared.

STOP after implementation.

---

# Prompt 5
## Research Memory Integration

Review Research Memory.

Ensure it permanently stores:

- hypotheses
- candidate strategies
- descendants
- tournament outcomes
- critiques
- failures
- promotion recommendations
- production outcomes

Research Memory must be append-only.

Nothing may overwrite historical evidence.

STOP after implementation.

---

# Prompt 6
## Strategy Evolution

Review descendant generation.

Complete bounded evolution for:

- parameter mutation
- parameter lineage
- deterministic descendants
- generation tracking
- parent relationships

Do not introduce unrestricted self-modifying code.

Every descendant must remain deterministic and reproducible.

STOP after implementation.

---

# Prompt 7
## Capital Allocation Integration

Review the Capital Allocation Engine.

Integrate tournament evidence so allocation recommendations can consume:

- tournament rankings
- drawdown
- stability
- consistency
- replay quality
- decision quality

This prompt must only produce recommendations.

Do not authorize production capital automatically.

STOP after implementation.

---

# Prompt 8
## Continuous Research Operation

Review the orchestration worker.

Complete activation of continuous research cycles.

Requirements:

- research continues independently of production
- production trading never pauses research
- bounded execution
- configurable intervals
- deterministic scheduling
- fault isolation

Production failures must not corrupt research.

Research failures must not interrupt production.

STOP after implementation.

---

# Prompt 9
## Research Dashboard

Review the existing frontend.

Create or extend pages allowing operators to observe:

- Research Laboratory
- Candidate Strategies
- Candidate Lineage
- Tournament Rankings
- Research Memory
- Promotion Recommendations
- Active Champion
- Historical Champions

This dashboard must remain read-only.

No production actions may originate from this interface.

STOP after implementation.

---

# Prompt 10
## End-to-End Commissioning

Perform a complete commissioning review.

Verify the following pipeline:

Research Agent

↓

Candidate Strategy

↓

Validation

↓

Replay

↓

Backtesting

↓

Decision Arena

↓

Tournament

↓

Research Memory

↓

Promotion Recommendation

↓

Capital Allocation Recommendation

↓

Human Approval

↓

Production

Document:

- every successful stage
- every missing stage
- every remaining gap
- production readiness
- recommended next milestones

Do not introduce additional architecture.

The objective is to certify the activation of the existing Research Evolution system rather than redesign it.

STOP after the commissioning report.