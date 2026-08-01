# Phase 0 Architectural Inventory

Assessment date: 2026-07-31  
Scope: repository source at the working tree; read-only inspection only  
Governing roadmap: `docs/PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md`  
Governing architectures: `docs/PIPELINE_ARCHITECTURE.md`, `docs/LEARNING_INTELLIGENCE_ARCHITECTURE.md`

## Evidence convention

`VERIFIED` means the statement follows directly from the cited source. `INFERENCE` means it follows from static call-path analysis but was not runtime-observed. `UNVERIFIED` means repository source cannot establish the deployed fact. File citations use repository-relative paths and name the callable; line numbers are included as navigation aids but callable names are controlling because later edits move lines.

Phase 0 did not run a worker, provider client, Controlled Proof, recovery, mutating endpoint, database command, or test. The governing architecture documents were untracked pre-existing user files and were not changed. This is the only file added.

## 1. Executive Finding

**Verdict: READY WITH BOUNDED PREREQUISITES.**

The current BTC/Kraken production route has identifiable entry, strategy/aggregation, Risk, mandate/campaign Governance, package, custody claim, provider, reconciliation, accounting, Controlled Proof, and recovery seams. Phase 1 can safely add non-authoritative contracts and compatibility adapters around them without changing runtime routing.

The bounded prerequisites before any Phase 1 wrapper may become authoritative are:

1. Freeze golden parity fixtures for BUY, HOLD, Risk rejection, Governance rejection, SELL, provider ambiguity/failure, partial/delayed state, reconciliation/accounting, Controlled Proof, and Exit Recovery using the tests in section 13.
2. Preserve transactions exactly. The worker has several deliberately separate commits, while the generic paper `(strategy, asset)` iteration is one transaction (`continuous_pipeline_worker.run_orchestration_cycle`, lines 2409-2922). Controlled Proof creation/claim/linking and recovery also have explicit commits/flushes (`controlled_proof.service`, `exit_recovery`). A wrapper must not move those boundaries.
3. Establish a non-authoritative causation rule for missing Risk and approval lineage. `live.accounting_reconciliation.ensure_execution_source` currently substitutes `uuid.uuid4()` when `live_order.risk_event_id` or provider-response `approval_event_id` is absent (lines 197-259). These are synthetic linkage identifiers, not verified upstream evidence. Phase 1 may represent this truth but must not silently bless it as canonical authority.
4. Resolve deployed configuration ownership before mode-dependent contracts are promoted. Repository source verifies Pydantic `.env` loading and direct `os.getenv` calls, but no systemd unit is checked in; actual `ExecStart`, `EnvironmentFile`, and drop-ins remain unverified (`app/config.py`; `docs/00_OPERATIONS_MAP.md`).
5. Keep Phase 1 BTC/Kraken-only and observe-only. The generic paper loop, instant trades, manual live-order flow, Coinbase adapter, replay/backtest, and research systems must not be routed through a new authority switch.

Production-safety concern: there are multiple provider-capable paths. The commissioned path is governed, but `instant_trades.execute_instant_trade` calls `provider.submit_order` independently, and `exchange_connections.service.reconcile_external_trade` imports external provider reality. They are not evidence that the autonomous path bypasses Governance; they are distinct active/operator paths and must remain explicitly excluded from a Phase 1 authority change.

## 2. Verified Runtime Entry Points

| Kind | Exact file and callable | Verified behavior / boundary |
|---|---|---|
| API service | `apps/api/app/main.py:41`, `create_app()`; module `app` | Builds FastAPI and includes route modules. Docker target is `uvicorn app.main:app` (`apps/api/Dockerfile:15`). |
| Market-data worker | `apps/api/app/services/data/worker_entrypoint.py`, `main()` / `run_forever()` / `run_ingestion_cycle()` | Provider candle acquisition and idempotent candle writing. README invocation: `python -m app.services.data.worker_entrypoint`. |
| Orchestration worker | `apps/api/app/services/orchestration/continuous_pipeline_worker.py:3033`, `main()` → `run_forever()` → `run_orchestration_cycle()` | Primary autonomous conductor and generic paper/research loop. Canonical module invocation is `python -m app.services.orchestration.continuous_pipeline_worker`. |
| Scheduler inside worker | `continuous_pipeline_worker.run_forever()` | Polls at `WorkerConfig.poll_interval_seconds`; opens a session per cycle. This is an async loop, not a separate scheduler service. |
| Reconciliation scheduler | `apps/api/app/services/orchestration/reconciliation_scheduler.py:160`, `poll_unresolved_live_orders()` | Discovers and reconciles unresolved orders; called from orchestration cycle. |
| Claim sweeper | `apps/api/app/services/orchestration/autonomous_execution_claims.py:908`, `sweep_stale_autonomous_execution_claims()` | Releases/repairs stale pre-provider claim state; worker caller. |
| Operator CLI | `apps/api/app/operator_cli/main.py`, `main()` plus dispatch to `operator_cli.service` | Read and governed mutation commands: previews, campaign/mandate lifecycle, package authorization/dry run/activation, Controlled Proof, recovery/status, commissioning. |
| Operator API | `apps/api/app/api/routes/operator_actions.py`, `post_operator_action()` | Creates an operator action and schedules immediate proof dispatch. |
| Controlled Proof API | `apps/api/app/api/routes/controlled_proofs.py`, `post_controlled_proof()`, `post_controlled_proof_recover_stale()`, `post_controlled_proof_cancel()`, `post_controlled_proof_exit_recovery()` | Authenticated operator proof lifecycle. Creation schedules worker dispatch; cancellation fails closed after entry proposal. |
| Controlled Proof service | `apps/api/app/services/controlled_proof/service.py:583/663`, `start_live_controlled_proof()`, `create_controlled_proof()` | Validates exact mandate/campaign/profile scope, capital and active-proof constraints; creates immutable proof evidence. |
| Immediate proof worker path | `continuous_pipeline_worker.schedule_controlled_proof_immediate_dispatch()` → `dispatch_controlled_proof_immediate_attempt()` → `_attempt_operator_controlled_proof_entry()` | In-process background task; asynchronous boundary at `asyncio.create_task`. Normal poll loop remains recovery path if task/process fails. |
| Exit Recovery | `apps/api/app/services/controlled_proof/exit_recovery.py`, `authorize_controlled_proof_exit_recovery()`, `claim_exit_recovery_by_id()`, `refresh_exit_recovery_completion()`, `refresh_exit_recovery_outcomes()`; worker `dispatch_controlled_proof_exit_recovery_attempt()` and its recovery progression branch | Governed SELL recovery with claim, package/order lineage and terminal projection. |
| Reconciliation | `reconciliation_scheduler.poll_unresolved_live_orders()` → `exchange_connections.service.reconcile_imported_live_order()` / `live.accounting_reconciliation.reconcile_live_order_and_fills()` | Provider query/fill collection followed by persistence. |
| Accounting completion | `apps/api/app/services/live/accounting_reconciliation.py:1181`, `record_live_fill_reconciliation()` | Writes fill reconciliation and, where complete, `LiveAccountingRecord` and fee attribution in the caller transaction. |
| Manual live order API | `apps/api/app/api/routes/live_crypto_orders.py`, preview/confirm/submit/cancel route callables → `live_crypto_orders.LiveCryptoOrderService` | Active operator-controlled order lifecycle, separate from commissioned autonomous execution. |
| Instant trade API | `apps/api/app/api/routes/instant_trades.py`, create/confirm callable → `apps/api/app/services/instant_trades.py:execute_instant_trade()` | Separate authenticated operator submission path. |
| Replay API | `apps/api/app/api/routes/arena.py` and decision replay routes → `replay.default_agent.replay_decision_package_v0()` | Reconstructs decisions from persisted packages; does not run the live pipeline. |
| Backtest API | `apps/api/app/api/routes/backtests.py` → `backtesting.persistence.run_backtest_and_persist()` → `backtesting.engine.run_backtest()` | Candle-level simulation and separate persistence. |
| systemd-relevant executable | Orchestration module `main()` and ASGI `app.main:app` | `UNVERIFIED`: no unit file is in the repository, so exact production unit command/environment cannot be asserted. `docs/00_OPERATIONS_MAP.md:101-127` records this gap. |

## 3. Verified Current Pipeline Map

### INPUT

| Component | File/callable; callers → downstream | Effective I/O and effects | Authority / treatment |
|---|---|---|---|
| Binance candles | `data/binance_client.py`, client fetch callable; `worker_entrypoint.run_ingestion_cycle` → `candle_writer.upsert_candles` | Provider JSON → normalized candle values; HTTP side effect; DB `assets`/`candles`. | Source adapter; **WRAP**. |
| Kraken candles | `data/kraken_client.py`, fetch callable; same worker path | Kraken OHLC → candle rows; public HTTP; uniqueness in candle writer/schema. | Source adapter; **WRAP**, BTC first. |
| Candle admission | `data/candle_writer.py`, `upsert_candles()` | Asset + interval + candle list → insert/update result. Mutable upsert, not immutable raw evidence. | Current DB input truth; **PRESERVE**, then wrap. |
| Runtime roster | `orchestration.asset_roster.resolve_autonomous_cycle_products_from_campaign()` | Settings + governing campaign DB state → normalized product list. Hidden DB/config dependency. | Campaign scope; **PRESERVE**. |
| Provider readiness | `exchange_connections.service` and `readiness.py`; provider `fetch_balances/fetch_product/fetch_price_evidence` | Credentials/connection → typed snapshots; private/public HTTP; connection evidence writes by service callers. | Readiness evidence; **WRAP**. |
| Asset readiness | `asset_commissioning.service` and worker roster bridge | Provider/product/candle/governance checks → commissioning run/status. | Governance support; **PRESERVE/WRAP**. |

### PROCESS

| Component | File/callable; callers → downstream | Effective I/O and effects | Authority / treatment |
|---|---|---|---|
| Autonomous cycle | `autonomous_cycle.orchestrator.run_autonomous_preview_cycle()`; worker `_trigger_autonomous_cycles_for_products` → strategy, mandate, Risk, decision ingestion | `AutonomousCycleRequest` → `AutonomousCycleResult`; writes `AutonomousCycleRun`, signals, Risk/mandate/decision evidence; commits owned by caller/service branches. | Production decision preparation; **WRAP**. |
| Strategy roster | `strategy_roster.service.run_strategy_roster_cycle()` and `decision_aggregator.aggregate_strategy_decisions()`; worker/autonomous orchestration → authoritative campaign selection | Candles/strategy configs/outcome scorecards → proposals and aggregate BUY/SELL/HOLD. DB writes roster runs/proposals/aggregate decisions. | Advisory/selection authority before Risk; **WRAP**. |
| Generic strategy | `strategies.base.Strategy.generate_signal()` implementations; worker generic loop | `StrategyContext` with candle sequence/params → action, strength, explanation. Pure except helper wall-clock defaults. | Paper path; **PRESERVE**, not Phase 1 authority target. |
| Economics/ranking | `capital_campaign_orchestration.authoritative.py` selection/composition callables; worker `_attempt_automatic_ready_package_creation` | Candidate evidence → deterministic winner/deferral and package composition. Reads campaign/account/candle/strategy data; writes aggregate and audit/package evidence. | Current deterministic arbitration; **WRAP**. |
| Risk | `risk.risk_engine.RiskEngine.evaluate_order()` and `autonomous_cycle.orchestrator._evaluate_risk()`; Controlled Proof `evaluate_controlled_proof_risk()` | `RiskEvaluationContext`/order request + DB risk state → approve/reject/resize and rule results. `risk_persistence.persist_risk_evaluation()` writes `RiskEvent`/audit. | Mandatory veto/resize/delay authority; **PRESERVE and WRAP ONLY**. |
| Mandate | `mandates.evidence.evaluate_and_record_mandate()` and `eligibility.evaluate_mandate_eligibility()` | Exact mandate/version plus action/scope/evidence → authorization decision; writes evaluation. | Governance authority; **PRESERVE/WRAP**. |
| Campaign/package Governance | `canonical_preview_package.authorize_package()`, `dry_run_package()`, `activate_package()`; `automatic_package_executor.execute_automatic_ready_package_through_activation()` | Package + exact campaign/mandate/version/policy pins → state transition READY→AUTHORIZED→DRY_RUN_PASSED→ACTIVATED. Writes package, activation, audit. | Governance authority; **PRESERVE/WRAP**. |
| Custody claim | `autonomous_execution_claims.claim_activated_package()` → `advance_claimed_execution()` | Activated package/scope → one-shot claim and progress. Writes `AutonomousExecutionClaim`, live order/audit through downstream. | Exclusive execution custody; **PRESERVE/WRAP**. |
| Provider translation/submission | `capital_campaign_domain.commissioned_entry_execution.execute_commissioned_entry()` / exit counterpart → `live_crypto_orders.LiveCryptoOrderService.submit()` → `KrakenSpotClient.submit_order()` | Package/claim/exact scope → `ExchangeOrderSubmissionRequest` → normalized provider result. Private HTTP and order/event writes. | Submission authority after flags/Governance; **PRESERVE/WRAP**. |
| Order supervision | `reconciliation_scheduler.poll_unresolved_live_orders()` and claim advancement/sweep | Unresolved orders → provider status/fills → lifecycle updates. | External truth observation; **PRESERVE**, refactor later. |
| Position lifecycle | `position_lifecycle.service.evaluate_position_lifecycle()` using `source_adapter` | Reconciled position/accounting/provider evidence → HOLD/SELL recommendation. No direct provider call found. | Decision input; SELL must re-enter Risk/Governance; **WRAP**. |
| Reconciliation | `live.accounting_reconciliation.reconcile_live_order_and_fills()`, `record_live_order_reconciliation()`, `record_live_fill_reconciliation()` | Provider order/fill snapshots → terminal/nonterminal reconciliation and accounting. Writes immutable-ish event rows plus mutable order projection. | Final authority on provider reality; **PRESERVE/WRAP**. |

### OUTPUT, RESULTS, FEEDBACK

| Stage/component | File/callable; shape/effects | Authority / treatment |
|---|---|---|
| Decision output | `decisions.ingestion.ingest_decision_records()` → `DecisionRecord`, `DecisionSnapshot`; `decisions.package.DecisionPackageBuilder.build()` returns persisted-evidence contract. | Current episodic output, incomplete whole-cycle closure; **EVOLVE/WRAP**. |
| Execution output | `LiveCryptoOrder`, `LiveExecutionEvent`, `LiveReconciliationEvent`, `LiveAccountingRecord`, `LiveExecutionQualityMetric`; writers in `live_crypto_orders`, `live.execution_orchestration`, `live.accounting_reconciliation`, `live.execution_quality`. | Distributed terminal evidence; **PRESERVE/WRAP**. |
| Controlled Proof output | `controlled_proof.service.get_controlled_proof_view()` / `_derive_fine_grained_status()` | Composes proof, buy/sell package/order/reconciliation/accounting into terminal view. **PRESERVE**. |
| RESULTS | `decisions.counterfactuals`, `decisions.quality`, `strategy_outcomes.service`, `live.execution_quality` | Append derived outcomes/quality; strategy outcomes use current clock and later candles. Partial; **REFACTOR LATER**. |
| FEEDBACK | `decisions.recommendations`, `research_*`, `evolution.*` | Recommendation/candidate/research records. Research is separately feature-flagged and must remain zero authority to production. **PRESERVE; EXCLUDE Phase 1**. |

## 4. End-to-End Call Paths

The following are static source traces (`INFERENCE`) with verified callables. `→` is a synchronous/awaited call; `⇢` is an asynchronous scheduling or later-poll boundary; `[branch]` is conditional.

### BUY (autonomous BTC/Kraken)

`continuous_pipeline_worker.run_forever` → `run_orchestration_cycle` → `data.worker_entrypoint.run_ingestion_cycle` → `candle_writer.upsert_candles` → `_run_autonomous_and_campaign_orchestration_attempt` → `_trigger_autonomous_cycles_for_products` → `autonomous_cycle.orchestrator.run_autonomous_preview_cycle` → `_run_approved_strategy` → `_evaluate_risk`/`RiskEngine.evaluate_order` → `mandates.evidence.evaluate_and_record_mandate` → `decisions.ingestion.ingest_decision_records` → campaign `authoritative` aggregation/ranking → `_attempt_automatic_ready_package_creation` → `canonical_preview_package.create_*` → `automatic_package_executor.execute_automatic_ready_package_through_activation` → authorization → dry-run → activation → `autonomous_execution_claims.claim_activated_package` → `advance_claimed_execution` → `commissioned_entry_execution.execute_commissioned_entry` → `LiveCryptoOrderService.submit` → `KrakenSpotClient.submit_order`.

Branches: insufficient/stale candle, duplicate cycle, HOLD strategy, no Risk approval, mandate mismatch, no winner, existing package/order, unresolved reconciliation, activation feature disabled, submission feature disabled, provider rejection/ambiguity all stop before or at the noted boundary. Package progression and provider submission are separated by claim custody.

### HOLD

Worker → autonomous cycle → approved strategy/aggregate → action `hold` → `autonomous_cycle.orchestrator._finish_hold` → decision intelligence/Decision Record and cycle terminal status. No package, claim, or provider call. In the generic paper loop, `generate_signal` action other than buy/sell persists `Signal`, calls `ingest_decision_records`, and skips `orchestrate_paper_signal_execution` (`continuous_pipeline_worker.run_orchestration_cycle`, generic loop).

### BLOCKED or DEFERRED

Risk block: autonomous cycle → `_evaluate_risk` → `risk_persistence.persist_risk_evaluation` → `_finish_hold`/failed terminal reason; no package. Governance block: candidate/package preparation → `_ensure_campaign_cycle_mandate_evaluation` / `evaluate_and_record_mandate` → automatic executor readiness checks → blocked outcome/audit; no activation or claim. Arbitration deferral: authoritative ranking emits no eligible winner and records deferral/context; no READY package. Provider-disabled block: claimed execution → `mark_submission_safety_disabled` or `mark_pre_provider_blocked`; no provider call.

### SELL

Position evidence → `position_lifecycle.service.evaluate_position_lifecycle` → worker controlled/autonomous SELL proposal → `controlled_proof.service.evaluate_controlled_proof_risk` or normal Risk evaluation → mandate/campaign exact-version checks → SELL package (`link_controlled_proof_sell_package`) → authorize/dry-run/activate → side-neutral `claim_activated_package` → `advance_claimed_execution` → commissioned exit execution → `LiveCryptoOrderService.submit` → `KrakenSpotClient.submit_order` ⇢ reconciliation/accounting. A SELL is not a direct provider action from the lifecycle evaluator.

### Controlled Proof

API `post_controlled_proof_start/create` or operator action → `start_live_controlled_proof`/`create_controlled_proof` → commit proof and audit ⇢ `schedule_controlled_proof_immediate_dispatch` → `dispatch_controlled_proof_immediate_attempt` → worker `_attempt_operator_controlled_proof_entry` → claim proof → resolve exact mandate strategy → candle/entry blockers → `evaluate_controlled_proof_risk` → create/link decision and package → normal package authorization/dry-run/activation → execution claim → commissioned submission. Later worker polls reconciliation; proof view derives completion only from reconciled/accounted legs.

### Exit Recovery

Operator/API authorization → `exit_recovery.authorize_controlled_proof_exit_recovery` → commit ⇢ worker immediate dispatch or next poll → `claim_exit_recovery_by_id` → worker recovery progression branch → validate proof/owned quantity/no conflicting open order → Risk evaluation → build/link SELL decision/package → activate → execution claim/submission ⇢ reconciliation → `refresh_exit_recovery_completion` / `refresh_exit_recovery_outcomes` → terminal proof/recovery verdict. Recovery rows are retained on expiry/block/failure.

### Reconciliation

Worker `run_orchestration_cycle` → `reconciliation_scheduler.poll_unresolved_live_orders` → `discover_reconciliation_candidates` → provider `lookup_order` + `list_fills` (via exchange connection reconciliation service) → `live.accounting_reconciliation.reconcile_live_order_and_fills` → `record_live_order_reconciliation` + for each fill `record_live_fill_reconciliation` → claim release when resolved. Boundary is asynchronous relative to submission and retryable by idempotency keys.

### Accounting completion

Normalized provider fill → `record_live_fill_reconciliation` → insert/replay `LiveReconciliationEvent` → create/replay `LiveAccountingRecord` and fee-attribution row → flush; worker caller commits → controlled-proof/position/profit projections read this reconciled evidence. A predicted price or submitted order alone cannot complete accounting.

## 5. Risk and Governance Authority Map

| Authority | Location and callable | Effect |
|---|---|---|
| Autonomous Risk | `autonomous_cycle.orchestrator._evaluate_risk()` → `risk_engine.RiskEngine.evaluate_order()` | Approve/reject and requested/approved quantity/notional; failures close the cycle. |
| Paper execution Risk | `signals.execution_orchestrator.orchestrate_paper_signal_execution()` → Risk engine | Rejects before paper venue execution and persists reason. |
| Controlled Proof Risk | `controlled_proof.service.evaluate_controlled_proof_risk()` | Reconstructs equity/evidence and applies live Risk before each proof leg. |
| Exit Recovery Risk | `controlled_proof.exit_recovery.advance_exit_recovery()` → proof Risk helper | Required for recovered SELL. |
| Manual/instant Risk | `live_crypto_orders.LiveCryptoOrderService` preparation/submit and `instant_trades.execute_instant_trade()` | Separate approval/Risk gates before provider submit. |
| Risk veto/resize/delay | `risk.risk_engine` rule evaluation and returned approved sizing; `risk_persistence.persist_risk_evaluation()` | `RiskEvent` is durable evidence; callers must use approved values. |
| Kill switches | `risk.risk_monitor` (`get_or_create_kill_switch`, mutations/evaluation) and Risk context | Global/account switches; DB-backed plus `global_kill_switch_default` config. |
| Campaign authority | `canonical_campaign_binding`, `capital_campaign_orchestration.authoritative`, package authority audit | Exact campaign/version/runtime binding and status. |
| Mandate authority | `mandates.lifecycle.get_governing_authorized_mandate_version`, `evidence.evaluate_and_record_mandate` | Exact active version, authorization expiry and allowed scope/strategy/action. |
| Asset readiness | `asset_commissioning.service`; `orchestration.asset_roster`; venue commissioning bridge | Only commissioned/enabled product scope becomes eligible. |
| Execution custody | `autonomous_execution_claims.claim_activated_package` | One-shot, side-neutral exclusive claim; uniqueness is database enforced by migrations 0047/0048/0053/0054. |
| Provider authorization | `commissioned_entry_execution` plus `LiveCryptoOrderService.submit`; flags in `config.Settings` | Requires activated package/claim and `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED`. |
| Feature flags | `Settings.live_crypto_order_submission_enabled`, `automatic_mandate_package_activation_enabled`, preparation/dry-run/reconciliation/venue flags; worker `WorkerConfig` direct env | Gates mutation/submission; mixed configuration sources are a migration risk. |

Potential bypass inventory (do not change in Phase 0):

- `instant_trades.execute_instant_trade()` directly reaches `provider.submit_order` after its own operator/Risk/preview gates. It bypasses autonomous package/claim Governance by design; classify ACTIVE operator path.
- `live_crypto_orders.LiveCryptoOrderService.submit()` is callable from manual API and commissioned execution. Its safety depends on the caller-specific approval contract; Phase 1 must not expose it as a generic canonical consumer.
- Provider adapter methods are public Python methods. Static source cannot prove there is no dynamic caller; repository search found production submission call sites listed in section 6.
- Coinbase `cancel_orders()` is implemented, but the base protocol has no cancel method and no production Kraken cancel implementation/caller was found. API “cancel live order” primarily mutates local lifecycle; do not describe it as exchange cancellation without provider evidence.

## 6. Provider Submission Inventory

| Capability | Call sites / implementation | Identity and retry behavior |
|---|---|---|
| Submit commissioned order | `commissioned_entry_execution` → `live_crypto_orders.LiveCryptoOrderService.submit()` → `KrakenSpotClient.submit_order()` | Package ID, claim ID, live order ID and stable internal client order ID. Kraken `_kraken_client_order_id()` maps to provider `cl_ord_id`; private request nonce is monotonic under an async lock. Claim/package uniqueness and order idempotency prevent ordinary duplicate retries. Ambiguous responses remain reconcilable. |
| Submit instant order | `instant_trades.execute_instant_trade()` (around lines 540) → provider `submit_order()` | Client request/confirmation/order IDs; distinct timeout and follow-up reconciliation. ACTIVE operator path. |
| Manual live submission | `LiveCryptoOrderService.submit()` (service around lines 2900+) | Generates client order ID only if absent; writes execution/order evidence. Callable from API and commissioned path. |
| Provider implementations | `KrakenSpotClient.submit_order()` (992), `CoinbaseAdvancedClient.submit_order()` (569) | Normalize `ExchangeOrderSubmissionRequest` to provider fields and normalize response/rejection. BTC/Kraken is Phase 1 target. |
| Cancel provider order | `CoinbaseAdvancedClient.cancel_orders()` (795) | Batch cancel API exists. No Kraken cancel provider callable or production call site found (`UNVERIFIED` cancellation capability). Local `LiveCryptoOrderService.cancel` must not be equated to provider cancel. |
| Query order | Reconciliation service → `KrakenSpotClient.lookup_order()` (1359), Coinbase equivalent (650) | Provider order ID and/or client order ID; scheduled repeated polls. |
| Retrieve fills | Reconciliation service → `KrakenSpotClient.list_fills()` (1497), Coinbase equivalent (700) | Provider order/client ID filters; fill IDs feed reconciliation idempotency. |
| Retrieve balances | autonomous `_reconcile_state`, exchange readiness/service → provider `fetch_balances()` | Snapshot only; provider state and retrieval time are hidden external inputs. |
| Retrieve positions | No spot-provider `fetch_positions` protocol method found. Positions are derived from fills/accounting/balances; paper/Alpaca has separate concepts. | Gap is explicit, not a missing call site. |
| Translate packages | `commissioned_entry_execution` constructs live order/request; `KrakenSpotClient.submit_order` performs Kraken pair/volume/order mapping | Stable seam: activated package → existing live-order service request; do not put Kraken fields in canonical contract. |

Important identifiers: package `package_id`; claim `claim_id`; internal `client_order_id`; Kraken shortened/mapped `cl_ord_id`; provider transaction/order ID; live order UUID; proof ID for controlled mode. Retry is primarily application polling plus idempotent replay, not blind resubmission. HTTP client backoff uses injected random function (`data.http_client`) for data requests; private submission ambiguity is reconciled rather than assumed failed.

## 7. Persistence and Transaction Inventory

| Material data | Model/table | Writing callable and transaction | Idempotency/mutability/lineage |
|---|---|---|---|
| Market data | `Asset`, `Candle` | `candle_writer.upsert_candles`; ingestion caller commits | Natural provider/asset/interval/open-time uniqueness; candle is mutable upsert. Asset ID links. |
| Strategy signal/evidence | `Signal`, `StrategyRosterRun`, proposal/outcome, `StrategyAggregateDecision` | worker generic loop; `strategy_roster.service`; authoritative aggregation; caller/session commits | Signal duplicate check on strategy+parameter+asset+signal time; outcome indexes. Mutable status, evidence rows mainly append. |
| Autonomous cycle | `AutonomousCycleRun` | `run_autonomous_preview_cycle`; branch flush/commit/caller commit | `build_cycle_idempotency_key`; mutable state machine. Links cycle, campaign, mandate, decision, risk. |
| Decision memory | `DecisionRecord`, `DecisionSnapshot` | `decisions.ingestion.ingest_decision_records`; worker transaction | Signal-source uniqueness; record/snapshot intended append-only. IDs link signals/risk/approval/package context. |
| Economics | Package/aggregate decision JSON and campaign definition accounting fields | authoritative package composition; commissioned execution | Economic idempotency key and intent hash in `commissioned_entry_execution`; some JSON projections mutable. |
| Risk | `RiskEvent`, `RiskKillSwitch`, `RiskRuleConfig`, `RiskEquityBaseline` | `risk_persistence.persist_risk_evaluation`; `risk_monitor`; `equity_evidence`; caller commit | Evaluation/audit identifiers; events append, switches/config/baseline current projections mutable. |
| Governance/campaign | `CapitalCampaign`, definition/version/profit policy/cycle | campaign repositories/services/control plane | Version pins and idempotency/audit hashes; definitions/version evidence append-ish, runtime/control state mutable. |
| Mandate | mandate, version, authorization, evaluation | `mandates.lifecycle`, `mandates.evidence` | Version hash, auth/evaluation idempotency; version intended immutable, governing pointers/status mutable. |
| Package | `CanonicalPreviewPackage`, `CanonicalProvingActivation` | `canonical_preview_package` create/authorize/dry-run/activate | Package/correlation/idempotency keys; state machine mutable; policy/market/decision IDs connect lineage. |
| Claim | `AutonomousExecutionClaim` | `claim_activated_package`, mark/release/sweep | DB uniqueness and one-shot flags; mutable custody state; package/proof/order IDs. |
| Provider order | `LiveCryptoOrder`, `LiveExecutionEvent` | `LiveCryptoOrderService` and live execution orchestration | client order ID/idempotency; order mutable projection, execution events append. |
| Fills/reconciliation | `LiveReconciliationEvent` | `record_live_order_reconciliation`, `record_live_fill_reconciliation` | deterministic reconciliation idempotency key; append/replay, newer terminal truth protected; links order/provider/fill/campaign. |
| Accounting | `LiveAccountingRecord` plus fee attribution | `record_live_fill_reconciliation` | fill/order idempotency; append accounting evidence. Reconciled truth is authority. |
| Controlled Proof | `ControlledProofRun` | create/start/claim/link/block/cancel/view repair in `controlled_proof.service` | request idempotency and active-scope concurrency; mutable lifecycle, retained terminal row; links mandate/campaign/packages/orders. |
| Exit Recovery | `ControlledProofExitRecovery` | authorize/claim/advance/project in `exit_recovery` | scope/idempotency and claim state; mutable retained lifecycle; proof/sell package/claim/order links. |
| Replay/backtest | `Backtest`, `BacktestTrade`; replay mostly reads Decision packages | `backtesting.persistence.run_backtest_and_persist` owns commits | Backtest ID; mutable run then terminal, trades append. Simulation isolation applies only to historical_simulation facilities, not all replay reads. |
| Model outputs | `ModelOutput` | signal/decision intelligence ingestion paths | Model name/version plus signal links; version semantics not registry-grade. |
| Audit | `AuditLog`, `LiveAuditEvidenceRecord` | widespread `db.add`; live audit service | Mostly append-only, correlation/entity IDs; transaction follows owning business operation. |
| Research/feedback | research candidate/campaign/memory/evaluation models; decision recommendations/quality/counterfactuals | `research_persistence.repository`, decisions services | IDs/versions vary; isolated from live authority by feature/path tests, but shares application DB unless simulation config is used. |

Transaction rule: a `flush()` allocates/checks identity but is not durability. The controlling commit is frequently in the worker or API dependency. Phase 1 adapters must be pure and must neither commit nor open a second session inside these boundaries.

## 8. Identifier and Causation Map

| Identifier | Current origin and propagation | Finding |
|---|---|---|
| Worker run ID | `continuous_pipeline_worker.run_forever`: `uuid.uuid4().hex` | Process-start operational ID, not decision cycle ID; regenerated. |
| Cycle ID | `AutonomousCycleRun.cycle_id`; cycle request/idempotency key | Stable within autonomous cycle; strongest initial causation root for BTC/Kraken. |
| Signal/decision ID | `Signal.id`; `DecisionRecord.decision_id` | Both exist and are linked by source refs; avoid overloading. |
| Decision Record ID | `DecisionRecord.decision_id` | Stable UUID; package builder uses it. |
| Asset/instrument | `Asset.id`; symbols/product strings (`BTC`, `XBT`, `BTC-USD`, Kraken pairs) | Stable DB asset ID exists, but product/symbol normalization is repeated and overloaded. Migration risk. |
| Campaign ID/version | definition/runtime UUID plus integer version | Explicit and widely pinned; preserve exact pair. |
| Mandate ID/version | mandate UUID, version UUID, version number | Three related values; never collapse version UUID and number. |
| Proof ID | `ControlledProofRun.proof_id` | Stable end-to-end operating-context ID. |
| Package ID | `CanonicalPreviewPackage.package_id` | Stable provider-intent root; preserve. |
| Execution claim ID | `AutonomousExecutionClaim.claim_id` | Stable custody identifier; side-neutral migration. |
| Live order ID | `LiveCryptoOrder.live_crypto_order_id` | Internal order aggregate ID. |
| Provider order ID | normalized provider result/order | External stable ID when acknowledged; may be absent under ambiguity. |
| Client order ID | live order/request; Kraken maps through `_kraken_client_order_id` | Stable idempotency bridge, subject to provider length/format mapping. |
| Position ID | No single canonical spot position row/ID | Position is derived from accounting/fills/campaign ownership. Missing canonical identity. |
| Reconciliation ID | `LiveReconciliationEvent.live_reconciliation_event_id` plus idempotency key | Event ID stable; multiple events per order are expected. |
| Accounting/result ID | `LiveAccountingRecord` ID; proof terminal fields | Distributed, no one cycle result ID. Phase 1 should reference, not fabricate. |
| Replay ID | Backtest UUID or replay result/package-derived identifiers | Multiple meanings; default replay uses decision package ID and current replay timestamp. Overloaded. |
| Model/output ID | `ModelOutput.id`, model name/version | Present but not immutable registry identity. |
| Audit correlation ID | UUIDs across preview/order/decision | Useful but sometimes independently regenerated and not universal. |

Missing/unstable points: no universal pipeline-run ID; no canonical position ID; product identity repeats provider-specific normalization; reconciliation may synthesize missing Risk/approval IDs; mandate version UUID vs number can be confused; replay ID is not common across backtest/replay/research; worker run ID is operational only.

## 9. Clock, Environment, and Global-State Inventory

| Source | Examples | Classification |
|---|---|---|
| Direct wall clock | autonomous orchestrator, worker, Controlled Proof/recovery, commissioned execution, live reconciliation, provider clients, strategy roster | Deterministic only at instant observed; mostly **currently hidden**, **replay risk**, **migration risk**. Preserve behavior; inject only in later compatible seams. |
| Injectable clocks | `data.worker_entrypoint.run_ingestion_cycle(now_fn=...)`; `data.http_client` retry dependencies; several `now`/`as_of` optional args | **Injectable**, lower replay risk. Prefer these patterns. |
| DB server/default time | ORM defaults/migrations and ordering by created/updated timestamps | Database-derived implicit state; replay/migration risk unless captured. |
| Pydantic environment | `app/config.py:Settings`; `get_settings()` | Process env plus configured `.env` fallback and defaults; hidden at call sites, migration risk. Do not print secrets. |
| Direct environment | worker `WorkerConfig.from_env`: poll/candle/lookback/quantity; provider mock flags; `DEPLOYED_GIT_SHA`; OpenAI key | Service-specific loading; currently hidden and can diverge from Settings. High migration/mode risk. |
| Module mutable state | `operations_status._RUN_ID/_STARTED_AT`; worker immediate-task sets/locks; commissioned-entry lock map; provider `_last_successful_call_at` and nonce lock/counter | Process-local implicit state; non-replayable and restart-sensitive. Locks provide safety only within a process; DB constraints remain cross-process authority. |
| UUID/random | numerous `uuid.uuid4`; Coinbase JWT nonce; HTTP retry jitter | Non-deterministic. Some are identity-only; synthesized causation is a replay risk. |
| Provider state | balances, product status, price, lookup/fills, nonce, order ambiguity | External and time-varying; must be captured for replay. |
| Feature flags | submission, package activation, dry run, reconciliation, venue commissioning, research; DB kill switches | Config/DB implicit state. Must be version-pinned in future context; preserve existing gate locations. |
| Defaults | DB URL, allowed BTC product, order caps/ages, worker intervals/quantity, strategy aggregator config | Deterministic only if exact deployed configuration is known. Present-time defaults are historical replay risk. |

## 10. Live-versus-Replay Comparison

| Question | Verified finding |
|---|---|
| Shared business logic | Strategy implementations and some decision-package readers are reusable. Backtest fill/metric logic is separate. Default replay reads a `DecisionPackageContract` assembled from persisted live evidence. |
| Duplicated logic | `backtesting.engine` runs strategy/fill/accounting simulation distinct from autonomous cycle, live Risk/Governance, provider lifecycle, and production accounting. `replay.default_agent._reconstructed_action` reconstructs rather than invoking live decision stages. |
| Replay approximations | `simulate_buy_fill`/`simulate_sell_fill` are candle/slippage/fee approximations; no live order book, provider ambiguity, claims, reconciliation scheduler, or actual accounting lifecycle. |
| Live-only decisions | readiness, feature flags, campaign/mandate exact version, kill switches, execution claim, provider submission, order supervision, reconciliation/accounting, Controlled Proof/recovery. |
| Separate schemas/databases | `OT_SIMULATION_DATABASE_URL`, `historical_simulation.SimulationBase`, `IsolationGuard` exist. Ordinary replay endpoints are read clients of the application DB; backtest persistence uses the supplied session. Structural isolation is therefore partial, not universal. |
| Production-state access | Replay/default agent deliberately reads Decision Records/packages from the current DB. No provider mutation call was found in replay modules. Research/backtest callers must be assessed by session configuration, not module name alone. |
| Future information | Counterfactual/outcome services query later candles by horizon; that is valid for RESULTS but not decision reconstruction. Default replay stamps `datetime.now`; backtests use present strategy/config unless explicitly pinned. Current-time configuration and missing `available_at` semantics make canonical historical replay not ready. |

Conclusion: live and replay do **not** yet execute the same post-normalization business pipeline. This is a verified Phase 1+ migration objective, not a defect to fix in Phase 0.

## 11. Implicit Contract Inventory

| Boundary | Producer → consumer | Effective payload / hidden dependency / failure / version | Likely insertion point |
|---|---|---|---|
| Provider candle → DB | Kraken/Binance client → `upsert_candles` | Dict/typed candle values; provider symbol/time assumptions; exceptions/retry result; unversioned | After provider fetch, before writer. |
| DB candles → strategy | worker/autonomous loader → `Strategy.generate_signal` | ORM rows converted to `StrategyContext`; latest ordering, interval/settings; exception or HOLD; strategy/parameter versions partially explicit | `_to_strategy_context` / autonomous `_run_approved_strategy`. |
| Strategy → arbitration | roster proposals → aggregate/authoritative ranking | ORM/TypedDict/JSON scores, explanations, eligibility; DB current scorecards/config; blocked/deferral reason; aggregator config version exists | Immediately before authoritative rank. |
| Decision → Risk | autonomous/proof request → Risk engine | Context objects/decimals plus DB snapshots and switches; reject/resize/reason; rule versions/config not one manifest | Caller wrapper around existing Risk call. |
| Risk → Governance | Risk event/approved sizing → mandate evaluation/package | ORM IDs plus dict payload; exact campaign/mandate state; blocked reason; no common schema version | Adapter that references RiskEvent, not recalculates. |
| Governance → package | mandate/campaign/package services | ORM package plus JSON evidence and exact version pins; expiry/current clock; state/reason; package schema implicit | Package creation boundary. |
| Activated package → claim | package executor → `claim_activated_package` | Package UUID and resolved scope; DB locking/uniqueness; blocked outcome; migrations encode contract | Thin input/result envelope around claim. |
| Claim/package → live order | `advance_claimed_execution` → commissioned execution → LiveOrder service | ORM claim/package/proof and request dataclasses; feature flags/credentials/current provider; exceptions/provider rejection/ambiguity | Best provider seam: just before existing `LiveCryptoOrderService.submit`. |
| Provider request → Kraken | live order service → `KrakenSpotClient.submit_order` | `ExchangeOrderSubmissionRequest`; credentials/environment/pair metadata/nonce; typed result or rejection/ambiguous exception; protocol unversioned | Provider adapter envelope, no business changes. |
| Provider state → reconciliation | lookup/list fills → accounting reconciliation | typed provider order/fills and safe payloads; current provider; statuses/unknown/conflict; idempotency key | Normalize immediately after adapter response. |
| Reconciliation → accounting | reconciliation writer → accounting record | ORM event/fill/order/campaign; transaction and prior rows hidden; replay/idempotent return; schema implicit | Reference-only accounting result wrapper after flush. |
| Live evidence → proof result | proof view loaders → `_derive_fine_grained_status` | Multiple ORM rows/dicts; latest-event ordering; waiting/manual-review/terminal states; unversioned | Read-only output assembly seam. |
| Decision package → replay | `DecisionPackageBuilder` → `DefaultReplayAgent` | Pydantic/dataclass contract from DB; current replay time; errors as not-found/diagnostics; `v0` callable version | Preserve as legacy adapter, not canonical live stage. |

## 12. Canonical-Contract Insertion Points

Smallest safe BTC/Kraken Phase 1 seams, in priority order:

1. **Candle observation adapter** — producer `data.kraken_client` fetch callable; consumer `data.candle_writer.upsert_candles`. Stable because it is provider acquisition → current persistence. Adapter must reproduce current asset/symbol/interval/decimal/time values. Preserve retry/upsert semantics. Parity: captured Kraken fixture maps byte-for-semantic-field identically and writer inputs compare equal.
2. **Strategy evaluation envelope** — producer autonomous `_run_approved_strategy`; consumer `_evaluate_risk`/arbitration. Stable because action/strength/explanation already exist. Adapter must retain existing dict/ORM inputs and exact action/reason/ordering. Parity: BUY/HOLD/SELL golden contexts yield identical action, confidence and explanation.
3. **Risk decision reference** — producer `_evaluate_risk` plus `persist_risk_evaluation`; consumer mandate/package preparation. Stable because durable `RiskEvent` is already the veto boundary. Adapter references exact event and approved sizing; it must not run Risk twice. Parity: approval, rejection, resize/delay and first-failing-rule fixtures.
4. **Governance authorization reference** — producer `evaluate_and_record_mandate` plus package authorization; consumer package activation/claim. Stable exact campaign/mandate version pins. Compatibility adapter exposes existing package fields. Parity: exact-version approval, expired/mismatched mandate, uncommissioned asset, paused campaign.
5. **Execution-intent adapter** — producer activated package + claim in `advance_claimed_execution`; consumer existing commissioned execution/`LiveCryptoOrderService.submit`. Stable because custody is acquired and values are fixed. Preserve client order ID, package/claim/proof IDs, decimals, gates, exceptions and transaction boundaries. Parity: submission-disabled, provider reject, ambiguous response, idempotent replay.
6. **Provider request/result envelope** — producer `LiveCryptoOrderService.submit`; consumer `KrakenSpotClient.submit_order` and returned normalized result. Preserve Kraken mapping/nonce/client-ID logic exactly. Parity: existing Kraken provider parsing/conformance tests plus request fixture.
7. **Reconciliation/accounting result references** — producer `record_live_order_reconciliation`/`record_live_fill_reconciliation`; consumers claim release, proof result, position/profit projections. Stable because these rows are authority over external truth. Adapter only records IDs/statuses after existing flush. Parity: open→partial→filled, delayed fill, duplicate fill, terminal-regression protection, fee/accounting totals.

Do not create a universal cycle object. A minimal common envelope may carry schema version, evidence ID/hash, occurred/observed time and causation references; business payloads remain stage-specific. No seam above changes authority in Phase 1.

## 13. Baseline Fixture Plan

| Scenario | Reuse now | Missing golden assertion to add in Phase 1 |
|---|---|---|
| BUY | `tests/unit/services/autonomous_cycle/test_orchestrator.py`; `tests/integration/test_continuous_pipeline_worker.py`; campaign authoritative/package tests | Freeze BTC/Kraken candle, exact action/sizing/reasons, campaign+mandate pins and package fields. |
| HOLD | autonomous orchestrator HOLD tests; `tests/unit/operator_cli/test_hold_decision_diagnostic.py`; worker non-actionable tests | Freeze terminal cycle/Decision Record and prove zero package/provider call. |
| Risk rejection | `tests/unit/services/risk/*`; autonomous cycle tests; controlled-proof Risk tests | One fixture per veto and resize/delay, exact reason/approved amount, no Governance/submission. |
| Governance rejection | mandate eligibility/lifecycle tests; canonical package readiness/authority tests; asset commissioning tests | Exact expired/wrong version, campaign pause, uncommissioned product outputs. |
| SELL | position lifecycle tests; controlled-proof worker/claim tests; commissioned exit tests | Complete side-neutral claim→Kraken request fixture and re-entry through Risk/Governance. |
| Provider failure | `tests/services/exchange_connections/test_kraken_provider_parsing.py`; provider conformance; live order tests | Separate rejection, timeout-before-send, ambiguity-after-send; assert no blind duplicate. |
| Partial/delayed state | reconciliation/accounting and worker reconciliation tests | Freeze open→partial→filled event order and claim release timing. |
| Reconciliation | `tests/integration/test_continuous_pipeline_worker.py`; live reconciliation unit/integration tests | Provider lookup/fills fixture with exact idempotency key/status mapping. |
| Accounting | live accounting/reconciliation tests; capital ledger tests | Exact quantity, gross/net, fee attribution, ownership and duplicate-fill behavior. |
| Controlled Proof | `tests/integration/test_controlled_proofs_route.py`, PostgreSQL lifecycle/concurrency tests, controlled-proof service/worker tests | One full proof with buy+sell+accounting terminal lineage; no real provider. |
| Exit Recovery | `tests/unit/services/controlled_proof/test_exit_recovery.py` and migration/worker tests (verify exact filenames via `rg exit_recovery tests`) | Authorized→claimed→SELL→reconciled→projected fixture plus expiry/block/crash restart. |

All Phase 1 tests must use fake providers and isolated test DBs. Do not execute tests marked/inferred as provider integration until their fixtures prove no external endpoint is reachable.

## 14. Duplicate and Bypass Path Register

| Path | Classification | Finding |
|---|---|---|
| Autonomous BTC/Kraken campaign path | ACTIVE | Governing production target. |
| Generic per-strategy paper loop in worker | ACTIVE | Separate signal→paper execution→Decision Record implementation. |
| Strategy roster/aggregate decision path | ACTIVE | Parallel strategy evaluations feeding authoritative campaign selection, not duplicate provider submission by itself. |
| Manual live crypto order API/service | CONDITIONALLY ACTIVE | Operator gated; can submit through live service. |
| Instant trade service | CONDITIONALLY ACTIVE | Direct provider call after its own operator/Risk gates; bypasses autonomous package/claim by design. |
| Controlled Proof | CONDITIONALLY ACTIVE | Governed operating context sharing package/claim/provider path. |
| Exit Recovery | CONDITIONALLY ACTIVE | Governed exceptional SELL path; shares safety stages. |
| Imported external order | CONDITIONALLY ACTIVE | Queries provider and imports reality; does not submit. Essential reconciliation bypass of original intent, with provenance. |
| Coinbase provider | CONDITIONALLY ACTIVE | Registered provider and submit/cancel/query/fill implementation; outside BTC/Kraken Phase 1. |
| Backtest engine | ACTIVE research/API | Duplicated strategy/fill/accounting approximation. |
| Default replay agent | ACTIVE read/replay | Reconstructs action from package rather than same live pipeline. |
| Paper internal simulator / Alpaca paper | ACTIVE or conditionally configured | Separate execution/accounting paths. |
| Legacy campaign transition/operator commands | LEGACY, CONDITIONALLY EXECUTABLE | Explicit inspect/transition/rollback functions; retain until verified retired. |
| Scripts for live dry run, environment initialization, Kraken auth/balance | CONDITIONALLY ACTIVE operator tools | Potential external side effects; not executed. |
| Test fake providers/direct calls | TEST ONLY | Provider conformance and lifecycle tests. |
| Coinbase cancel with no route caller | APPARENTLY DEAD — VERIFY BEFORE REMOVAL | Implemented capability, no production caller found. |

No source is deleted. “Duplicate” means independently implemented business semantics, not necessarily erroneous behavior.

## 15. Phase 1 Readiness Package

### Exact recommended file scope

Add only:

- `apps/api/app/services/pipeline_contracts/__init__.py`
- `apps/api/app/services/pipeline_contracts/envelope.py`
- `apps/api/app/services/pipeline_contracts/context.py`
- `apps/api/app/services/pipeline_contracts/identifiers.py`
- `apps/api/app/services/pipeline_contracts/btc_kraken.py` (stage-specific v1 payloads, not a universal object)
- `apps/api/app/services/pipeline_contracts/adapters.py`
- `apps/api/tests/unit/services/pipeline_contracts/test_envelope.py`
- `apps/api/tests/unit/services/pipeline_contracts/test_btc_kraken_adapters.py`
- `apps/api/tests/fixtures/pipeline_contracts/` golden JSON fixtures

Modify only after pure adapters pass, and only to populate observe-only contracts without routing authority:

- `apps/api/app/services/data/worker_entrypoint.py` or the narrow Kraken fetch→writer caller
- `apps/api/app/services/autonomous_cycle/orchestrator.py`
- `apps/api/app/services/orchestration/continuous_pipeline_worker.py`
- `apps/api/app/services/orchestration/autonomous_execution_claims.py`
- `apps/api/app/services/capital_campaign_domain/commissioned_entry_execution.py`
- `apps/api/app/services/live_crypto_orders.py`
- `apps/api/app/services/live/accounting_reconciliation.py`
- corresponding existing unit/integration tests only

No migration or production table is required for the smallest Phase 1. Serialization fixtures can prove contracts before observe-only persistence is designed.

### Smallest scope and prerequisites

Implement deterministic envelope/context/identity/decimal/time serialization plus adapters for: persisted Kraken candle → current autonomous strategy input; existing RiskEvent → Governance input reference; activated package+claim → existing execution request; provider result → reconciliation input reference. Populate in memory/test logs only, default-disabled if any runtime hook is added.

First resolve fixture approval, transaction ownership, synthetic lineage representation, and deployed config-source inventory. Do not resolve them by refactoring production code.

### Tests that must remain green

Autonomous orchestrator and reconciliation gate; continuous worker integration; risk engine/persistence/monitor; mandate eligibility/lifecycle/evidence; canonical package authority/activation; autonomous execution claims including controlled-proof scope; commissioned entry/exit; Kraken provider parsing/conformance; live order submission; reconciliation/accounting; Controlled Proof concurrency/lifecycle; Exit Recovery; research/live isolation; historical simulation isolation.

### Explicit exclusions

No migrations, raw evidence store, authoritative canonical routing, Risk math changes, Governance changes, order lifecycle consolidation, replay convergence, model/dataset registry, learning model, Coinbase generalization, microservice/queue, frontend/API, systemd/config change, provider cancellation work, or cleanup of legacy/duplicate paths.

### Rollback boundary

Delete/disable unused contract modules and observe-only calls. Existing ORM records, routing, provider requests, commits, feature flags, and authority remain untouched. No data rollback is needed because Phase 1 should not persist canonical evidence yet.

### Recommended commit sequence

1. Add approved golden fixtures copied from current test builders and document provenance.
2. Add pure envelope/context/serialization and tests.
3. Add BTC/Kraken stage-specific contracts and compatibility adapters with fixture parity.
4. Add read-only Risk/Governance and package/claim reference adapters; prove no recalculation.
5. Add provider request/result and reconciliation/accounting reference adapters; prove exact decimals, IDs and errors.
6. Add optional observe-only instrumentation one seam at a time, each default-disabled and with first-divergence reporting.
7. Produce a parity report; do not promote authority in Phase 1.

## Final Phase 0 Recommendation

Proceed to Phase 1 only after the bounded prerequisites in section 1 are accepted. The most important insertion points are the durable RiskEvent→Governance reference, activated package+claim→existing live-order submission request, and normalized provider result→existing reconciliation/accounting writers. The most important safety concern is not a newly discovered defect but an architectural fact: multiple submission-capable operator/autonomous paths coexist, while reconciliation can synthesize absent lineage IDs. The exact next action is to approve and freeze the section 13 BTC/Kraken golden fixtures, then implement commit 1 of the Phase 1 sequence without changing runtime authority.
