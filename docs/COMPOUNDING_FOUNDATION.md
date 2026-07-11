# Compounding Foundation v1 (Phase 2)

## Objective

Compounding Foundation v1 establishes recommendation-grade compounding logic for capital campaigns.

The objective is to provide durable, auditable compounding suggestions without executing capital movement.

## Scope

Included:

- campaign-level policy-driven compounding calculations
- cycle-by-cycle recommendation history
- operator approval/rejection workflow
- mission-control and ledger visibility for recommendation evidence

Excluded:

- automatic compounding execution
- automatic withdrawals
- live broker order placement
- wallet/balance transfer orchestration

## Recommendation Lifecycle

1. Operator configures a profit policy.
2. System evaluates a profit cycle against durable campaign accounting data.
3. System computes recommendation outputs:
   - `compound_amount`
   - `withdrawal_amount`
   - `reserve_amount`
4. Recommendation is persisted with evidence and status.
5. Operator can approve or reject the cycle.

Approval is governance state only in v1 and does not trigger execution.

## Capital Protection Constraints

Compounding recommendations are bounded by policy constraints, including:

- minimum realized profit gates
- minimum cash reserve retention
- fee/tax reserve percentages
- protected principal floors
- maximum campaign capital ceilings

These constraints prevent over-allocation and preserve recommendation discipline.

## Mission Control and Ledger Integration

Mission Control includes campaign-profit cards for recommendation status awareness:

- campaigns near target
- campaigns at target
- eligible compounding amount
- recommended withdrawal amount
- awaiting-review amount
- active compounding policies

Capital Ledger includes recommendation-only pool rows:

- `compounding_recommendation`
- `withdrawal_recommendation`
- `profit_reserve`
- `policy_review`

These rows are evidence-only and do not change managed-capital totals.

## Safety and Governance

Hard safety requirement:

- `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`

Compounding Foundation v1 is designed for paper-safe accounting recommendations and operator review, not autonomous execution.

## Future Evolution (Non-v1)

Potential future phases may add settlement-aware execution adapters and transfer controls, but only with explicit governance, risk gating, and additional safeguards.
