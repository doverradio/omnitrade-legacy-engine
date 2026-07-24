# Autonomous Operations Supervisor

The supervisor is a read-only projection over canonical trading records. It does not run orchestration, persist a parallel lifecycle, change configuration, or call a provider. The existing orchestration worker continues to persist each cycle; the operator commands resolve current state whenever requested, so supervisor failure cannot roll back or poison a trading transaction.

## Evidence and stages

| Stage | Canonical evidence | Normal successor | Default stall threshold |
|---|---|---|---|
| `NO_CAMPAIGN` | no matching `AutonomousCycleRun` | `STRATEGIES_COMPLETED` | none |
| `STRATEGIES_COMPLETED` | terminal campaign `AutonomousCycleRun` | `EVIDENCE_PERSISTED` | none; HOLD is healthy waiting |
| `EVIDENCE_PERSISTED` | cycle `decision_record_id` | `MANDATE_EVALUATED` | none |
| `MANDATE_EVALUATED` | cycle `mandate_evaluation_id` | `PACKAGE_READY` | none |
| `PACKAGE_READY` | latest scoped `CanonicalPreviewPackage` | `PACKAGE_AUTHORIZED` | 10 minutes |
| `PACKAGE_AUTHORIZED` | package state `AUTHORIZED` or `DRY_RUN_PASSED` | `PACKAGE_ACTIVATED` | 5 minutes |
| `PACKAGE_ACTIVATED` | `CanonicalProvingActivation` | `BUY_SUBMITTED` | 10 minutes |
| `BUY_SUBMITTED` | submitted BUY `LiveCryptoOrder` | `POSITION_OPEN` | 15 minutes |
| `POSITION_OPEN` | positive net quantity in canonical `Trade` records | `SELL_SUBMITTED` | 24 hours |
| `SELL_SUBMITTED` | submitted SELL `LiveCryptoOrder` | `RECONCILIATION_PENDING` | 15 minutes |
| `RECONCILIATION_PENDING` | non-terminal `LiveReconciliationEvent` | `RECONCILED` | 15 minutes |
| `RECONCILED` | filled BUY and SELL reconciliation | `NET_PROFIT_CONFIRMED` | none |
| `NET_PROFIT_CONFIRMED` | autonomous BUY/SELL provenance, filled reconciliation, and campaign realized profit above zero | complete | none |

Normal HOLD cycles never enter stall evaluation. `SAFETY_DISABLED` names the effective activation or live-submission flag only when the lifecycle has reached the boundary controlled by that flag. Other canonical failures are `BLOCKED`; advancing states are `PIPELINE_PROGRESSING`.

Expired nonterminal package rows remain immutable historical evidence. Activation readiness reports them as `stale_package` when no fresh package exists because there is nothing safe to activate. The supervisor does not turn that inventory-only condition into an operational blocker during a healthy HOLD. A fresh package is selected only when its preview window is unexpired and its campaign/version matches the latest campaign cycle.

## Operator commands

```bash
./operator autonomous-profit-status --provider kraken_spot --environment production --product BTC-USD

./operator autonomous-profit-report --provider kraken_spot --environment production --product BTC-USD --since 12h

./operator stale-package-inspect --provider kraken_spot --environment production --product BTC-USD --json

```

Add `--json` for stable machine-readable output. Both commands are read-only and may be run repeatedly.
