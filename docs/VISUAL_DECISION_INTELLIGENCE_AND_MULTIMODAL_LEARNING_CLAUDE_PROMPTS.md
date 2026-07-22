# VISUAL DECISION INTELLIGENCE AND MULTIMODAL LEARNING — CLAUDE IMPLEMENTATION PROMPTS

## OmniTrade Legacy Engine

**Use:** Execute these prompts sequentially with Claude Code.  
**Rule:** Do not skip the audit prompt. Do not combine phases unless the preceding phase is proven complete.  
**Companion architecture:** `docs/VISUAL_DECISION_INTELLIGENCE_AND_MULTIMODAL_LEARNING_ARCHITECTURE.md`

---

# Permanent Instructions for Every Prompt

Apply these constraints throughout:

- Read and obey `PROJECT_CONSTITUTION.md`, `SYSTEM_ARCHITECTURE.md`, `DECISION_INTELLIGENCE_ENGINE.md`, `RISK_ENGINE.md`, `STRATEGY_ENGINE.md`, `AI_LAYER.md`, `UI_SPEC.md`, `API_CONTRACTS.md`, `DATABASE_SCHEMA.md`, `SECURITY_AND_SAFETY.md`, `REPO_STRUCTURE.md`, `00_PROJECT_STATE.md`, and `02_DECISIONS.md` where present.
- Preserve the four permanent foundational engines.
- Treat the Decision Arena Observatory as a cross-engine capability, not a new foundational engine.
- Risk Engine authority is final.
- Campaign authority may not be bypassed.
- Read-only visualization and learning infrastructure must not alter live execution behavior.
- Structured data is canonical; images are deterministic derived artifacts.
- No future leakage.
- No secrets in replay packages, logs, API payloads, manifests, or rendered images.
- No hardcoded Kraken-only assumptions downstream of provider adapters.
- All time ordering must be deterministic.
- Historical artifacts are immutable; corrections create new versions.
- Every model, prompt, feature schema, dataset, render schema, and policy reference must be versioned.
- Do not commit unless explicitly instructed.
- State whether a database migration is required.
- Run targeted tests and the full suite when practical; distinguish new failures from known pre-existing failures.
- Preserve existing behavior unless the phase explicitly authorizes a change.

---

# Prompt 1 — Repository Audit and Architecture Gap Analysis

```text
We are beginning the Decision Arena Observatory initiative described in:

docs/VISUAL_DECISION_INTELLIGENCE_AND_MULTIMODAL_LEARNING_ARCHITECTURE.md

Perform a read-only repository audit. Do not modify code, schemas, configuration, or documentation yet.

Objective:
Determine exactly which parts of the proposed architecture already exist, where the canonical data currently lives, and what the smallest safe implementation sequence should be.

Required audit areas:

1. Market timeline data
   - candles and intervals
   - instrument/asset identity
   - provider/venue identity
   - price normalization
   - data-quality and deduplication behavior

2. Decision timeline data
   - strategy proposals
   - individual strategy votes
   - aggregate decision
   - confidence values
   - Decision Records
   - Decision Packages
   - immutable snapshots
   - reason codes

3. Portfolio and execution timeline
   - account equity snapshots
   - campaign capital state
   - positions
   - orders
   - fills
   - fees
   - slippage
   - reconciliation
   - realized/unrealized P&L

4. Learning and evaluation infrastructure
   - Counterfactual Outcome Ledger
   - strategy outcome scoring
   - research/evolution records
   - Decision Arena
   - model or prompt versioning
   - experiment tracking
   - benchmark calculations

5. Frontend
   - current chart library
   - existing chart components
   - decision explorer/replay pages
   - API client conventions
   - design system patterns

6. Security and governance
   - authorization requirements
   - secret-bearing payloads that must be excluded
   - immutability conventions
   - audit-log conventions

7. Database and API
   - existing tables that can be reused
   - missing entities
   - migration implications
   - current endpoints that already expose relevant data

Produce:

A. A source-of-truth map with concrete files, classes, functions, tables, and endpoints.
B. A proposed canonical timeline event schema.
C. A gap matrix: existing / partial / missing / conflicting.
D. A leakage-risk analysis.
E. A provider-neutrality analysis.
F. A recommended phase sequence and exact first implementation slice.
G. Any architectural conflicts requiring an ADR.
H. No code changes.

Final verdict:
- READY FOR VDI-1 IMPLEMENTATION
- or NOT READY, with exact blockers.
```

---

# Prompt 2 — ADR and Canonical Replay Contract

```text
Using the completed repository audit and:

docs/VISUAL_DECISION_INTELLIGENCE_AND_MULTIMODAL_LEARNING_ARCHITECTURE.md

create the architecture documentation and contracts required before implementation.

Do not implement runtime behavior yet.

Required outputs:

1. An ADR establishing:
   - Decision Arena Observatory as a cross-engine capability
   - structured records as canonical truth
   - deterministic visual renderings as derived artifacts
   - explicit cutoff-time semantics
   - immutable versioned replay packages
   - future labels stored separately from inference-time features
   - visual agents as advisory only

2. A canonical replay-package contract defining:
   - identity
   - instrument and venue
   - cutoff timestamp
   - ordered candle window
   - strategy proposals/votes
   - confidence
   - campaign and portfolio state
   - risk result
   - execution state
   - version references
   - content hash

3. A canonical timeline-event contract with deterministic event ordering.

4. A render-manifest contract identifying:
   - replay package
   - render schema version
   - visible overlays
   - normalization mode
   - viewport
   - data cutoff
   - outcome visibility
   - content hash

5. A dataset-manifest contract.

6. API contract additions, clearly marked planned and not yet implemented.

7. Database schema proposal. Reuse existing entities wherever possible and avoid duplication.

8. Threat model covering leakage, secret exposure, artifact mutation, and unauthorized model promotion.

Run documentation/reference validation if available.
Do not commit.

Final report:
- files created/changed
- decisions made
- unresolved questions
- migration expected later: yes/no
- verdict for implementation.
```

---

# Prompt 3 — VDI-1 Read-Only Observatory Backend

```text
Implement the smallest read-only Observatory backend slice approved by the audit and ADR.

Scope:
A provider-neutral timeline endpoint for one instrument and a bounded time range.

The endpoint must return only existing canonical records and must not alter trading behavior.

Minimum response capabilities:

- ordered candle data
- asset/instrument and venue identity
- portfolio/equity values when available
- BUY/SELL/HOLD aggregate events
- individual strategy votes when available
- confidence values when available
- risk resize/rejection events
- order/fill/reconciliation events when available
- benchmark-ready timestamps
- explicit data completeness flags
- schema version

Requirements:

1. Deterministic ordering for events sharing a timestamp.
2. Explicit UTC timestamps.
3. Bounded query ranges and pagination/point limits.
4. No secrets or exchange credentials.
5. Authorization consistent with existing observability endpoints.
6. No future outcome labels in the live/historical decision payload.
7. Provider-neutral contracts.
8. Efficient queries; avoid N+1 behavior.
9. Tests for empty, partial, and fully populated timelines.
10. Tests proving secret fields are absent.
11. Tests proving ordering is deterministic.
12. Tests proving no write side effects.

Update API contracts and documentation.
Run targeted tests and the full suite.
Do not commit.

Final report:
- exact endpoint
- query plan/data sources
- files changed
- tests
- performance considerations
- migration required yes/no
- verdict.
```

---

# Prompt 4 — VDI-1 Interactive Observatory UI

```text
Implement the first read-only Observatory UI using the backend timeline endpoint.

Scope:

Create an instrument timeline page with:

- candle or price line chart
- time-range controls
- overlay checkboxes for:
  - Asset price
  - OmniTrade portfolio value
  - BUY / SELL / HOLD events
  - AI confidence
  - Strategy votes
  - Risk events
- indexed mode so portfolio and benchmark can start at 100
- event tooltips
- click-to-open decision details using existing decision inspector patterns
- clear loading, empty, partial-data, and error states

Important visual semantics:

- BUY/SELL/HOLD are markers/events, not misleading continuous lines.
- Confidence is on a bounded scale.
- Raw asset price and portfolio dollars must not share an unlabeled axis.
- Every chart indicates the interval, range, venue/provider, and data freshness.
- No claims of profitability without net, reconciled data.

Requirements:

1. Follow existing frontend architecture and design system.
2. Reuse the current charting library where suitable.
3. Accessible keyboard and screen-reader behavior for controls.
4. Responsive desktop and mobile layouts.
5. No live execution controls.
6. Unit/component tests where the repository supports them.
7. API contract types generated or defined using project conventions.
8. No hardcoded production IDs.

Run frontend lint/typecheck/tests and relevant backend tests.
Do not commit.

Final report:
- route and components
- screenshots or textual layout summary
- files changed
- test results
- known limitations
- verdict.
```

---

# Prompt 5 — VDI-2 Immutable Replay Package Builder

```text
Implement immutable, versioned Decision Replay Packages.

A replay package must reconstruct only the information available at a declared cutoff timestamp.

Requirements:

1. Replay-package identity and immutable storage.
2. Explicit:
   - cutoff_time
   - window_start/window_end
   - instrument/venue
   - schema versions
   - strategy/model/prompt/policy versions
   - code commit SHA where available
   - content hash
3. Deterministic ordered candles and events.
4. Strict exclusion of information after cutoff_time.
5. Idempotent creation: same source/version inputs produce the same logical artifact or safely return the existing artifact.
6. Provenance links to Decision Records and existing snapshots.
7. No duplication of existing immutable decision data unless required for durable content-addressed packaging.
8. API to request/read a package.
9. Tests with intentionally planted future records proving they are excluded.
10. Tests for idempotency, content hashing, ordering, and immutability.
11. Backfill plan, but do not run an unbounded production backfill.

If a migration is required, create it using repository conventions and explain rollback/compatibility.

Run targeted and full tests.
Do not commit.

Final report:
- schema and migration
- builder flow
- future-leakage proof
- files changed
- tests
- operational backfill command proposal
- verdict.
```

---

# Prompt 6 — VDI-2 Candle-by-Candle Replay UI

```text
Implement a candle-by-candle replay experience backed by immutable replay packages.

Features:

- play/pause
- step backward/forward
- speed controls
- jump to decision event
- chart reveals data only through the current replay cursor
- synchronized strategy votes, confidence, campaign state, position state, and risk state
- decision inspector at each event
- visible cutoff/leakage boundary indicator
- version manifest panel
- optional outcome mode that is disabled by default and clearly marked hindsight/retrospective

Safety requirement:
The default replay view must never render future candles or outcome labels before the replay cursor.

Add tests proving cursor-based truncation and default outcome hiding.
Do not add model inference yet.
Run relevant tests and do not commit.

Return files, tests, route, limitations, and verdict.
```

---

# Prompt 7 — VDI-3 Outcome Label and Benchmark Engine

```text
Implement versioned retrospective labels and benchmark calculations without exposing them to inference-time replay payloads.

Minimum labels:

- net return after fees at 1, 2, 4, 8, and 16 candle horizons
- maximum favorable excursion
- maximum adverse excursion
- realized outcome when a position closes
- benchmark buy-and-hold return
- OmniTrade alpha versus benchmark
- decision-quality fields already supported by the platform

Requirements:

1. `available_at` timestamp for every label.
2. Labels stored separately from inference features.
3. Fees, fills, and reconciliation truth used where applicable.
4. Clear distinction between executed outcome and counterfactual outcome.
5. Idempotent label generation.
6. Correct handling of missing future candles and incomplete positions.
7. Tests preventing labels from appearing in decision-time endpoints/packages.
8. Tests for exact horizon calculations.
9. Versioned label schema.
10. Provider-neutral calculations.

Add benchmark and alpha overlays to retrospective UI mode only.
Run targeted/full tests.
Do not commit.

Final report must include leakage proof and migration status.
```

---

# Prompt 8 — VDI-4 Dataset Registry and Experiment Ledger

```text
Implement a reproducible Dataset Registry and Learning Experiment Ledger.

Dataset manifests must include:

- instruments/venues
- time range
- replay package selection criteria
- feature schema version
- label schema version
- train/validation/test boundaries
- embargo/purge settings
- content hash
- known data-quality issues

Experiment records must include:

- experiment identity/version
- dataset manifest
- model/agent identity and version
- prompt template version if applicable
- hyperparameters/configuration
- random seed
- code commit SHA
- metrics by split
- artifact references
- status and governance verdict

Requirements:

- immutable completed manifests/experiments
- no random row splitting for time series
- walk-forward split support
- sealed final holdout support
- authorization for creating experiments
- no production strategy mutation
- tests for reproducibility and immutability

Implement APIs/CLI or service interfaces consistent with repository patterns.
Do not train a production model yet.
Run tests and do not commit.
```

---

# Prompt 9 — VDI-5 Interpretable Supervised Baseline

```text
Build the first advisory-only supervised baseline using the Dataset Registry.

Purpose:
Test whether the versioned replay features contain out-of-sample predictive information.

Start with interpretable baselines, not a complex deep model.

Candidate tasks:

- probability that a proposed BUY produces positive net return after a selected horizon
- probability that the aggregate decision beats buy-and-hold
- prediction of decision-quality category

Requirements:

1. Time-ordered walk-forward validation.
2. Sealed holdout untouched until the candidate is frozen.
3. Fees included in outcome labels.
4. Calibration metrics.
5. Feature importance or explainability.
6. Baselines against naive policies.
7. Metrics by market regime and confidence bucket.
8. Multiple-comparison caution.
9. Artifact/model versioning.
10. Advisory only; no execution-path integration.

Produce an experiment report stating whether meaningful predictive value exists. A negative result is valid and must be preserved.
Do not commit or promote anything.
```

---

# Prompt 10 — VDI-6 Unsupervised and Self-Supervised Pattern Discovery

```text
Implement a research-only pattern-discovery experiment over replay windows.

Goals:

- identify stable market/decision clusters
- detect vote-conflict patterns
- detect anomalous confidence/price divergence
- learn reusable state embeddings if justified

Requirements:

1. Use training windows only when fitting representations/clusters.
2. Evaluate cluster stability out of sample.
3. Do not label clusters as profitable until retrospective testing proves it.
4. Compare discovered groups against existing regime definitions.
5. Produce human-readable representative examples and replay links.
6. Record all artifacts and versions in the Experiment Ledger.
7. No execution authority.
8. Preserve negative/unstable results.

Return discovered hypotheses, stability metrics, and recommended next experiments.
Do not change strategies or commit.
```

---

# Prompt 11 — VDI-7 Deterministic Visual Render Service

```text
Implement deterministic visual replay rendering as a supplementary artifact.

Requirements:

- render from an immutable replay package
- explicit render schema version
- fixed viewport/resolution options
- deterministic overlay set
- cutoff timestamp enforced
- future candles hidden
- outcome overlays off by default
- render manifest with content hash
- no secrets
- storage using existing artifact conventions
- idempotent render requests

Initial overlays:

- asset price/candles
- indexed portfolio value
- BUY/SELL/HOLD markers
- confidence
- compact strategy-vote track

Add tests for deterministic manifests, cutoff enforcement, secret exclusion, and idempotency.
Do not connect a vision model yet.
Run tests and do not commit.
```

---

# Prompt 12 — VDI-7 Multimodal Visual Analyst Prototype

```text
Create an advisory-only Multimodal Visual Analyst prototype.

Inputs:

- deterministic visual replay frame
- render manifest
- selected structured summary from the same replay package

The analyst may produce only structured hypotheses, such as:

- suspected late entry
- suspected premature exit
- confidence/price divergence
- vote-conflict pattern
- visual anomaly

It must not produce or submit an order.

Requirements:

1. Version the model and prompt template.
2. Require citations to timestamps/events in the replay.
3. Validate visual claims against structured data.
4. Mark unsupported claims as rejected.
5. Compare analyst consistency across repeated deterministic runs where possible.
6. Store analysis as an experiment artifact, not a canonical Decision Record.
7. Prevent future/outcome data from entering decision-time analysis.
8. Build a quantitative evaluation set for visual hypotheses.

Return whether the visual analyst adds measurable information beyond structured baselines.
A finding of no incremental value is acceptable.
Do not integrate into live decisions and do not commit.
```

---

# Prompt 13 — VDI-8 Champion/Challenger Decision Arena

```text
Implement the evidence and governance layer for champion/challenger evaluation.

A challenger may be:

- a strategy version
- aggregation policy
- confidence calibration model
- feature schema
- bounded allocation policy

Requirements:

1. Immutable challenger identity/version.
2. Parent/incumbent reference.
3. Explicit hypothesis.
4. Dataset and experiment provenance.
5. Walk-forward replay metrics.
6. Sealed-holdout metrics.
7. Risk-adjusted metrics and drawdown.
8. Fees/slippage.
9. Statistical uncertainty.
10. Paper/shadow proving status.
11. Human/governance verdict.
12. Promotion and rollback records.
13. A challenger cannot approve itself.
14. No live integration in this phase.

Build the evidence UI comparing incumbent and challenger with representative replay links and quantitative metrics.
Run tests and do not commit.
```

---

# Prompt 14 — Final Readiness Audit Before Any Live Influence

```text
Perform a comprehensive readiness audit of the completed Decision Arena Observatory and learning infrastructure.

Do not implement live influence.

Prove or reject each claim:

- replay packages are immutable and deterministic
- no future leakage is possible through structured or visual inputs
- outcome labels are separated
- versions are complete
- experiments are reproducible
- visual agents cannot submit orders
- challengers cannot self-promote
- Risk Engine and campaign authority remain final
- rollback is possible
- secrets are excluded
- provider neutrality is preserved
- negative experiments are retained
- paper/shadow evidence is sufficient for a bounded next phase

Run security, migration, API, frontend, and regression tests.
Produce a formal go/no-go report.

Allowed verdicts:

- READY FOR BOUNDED ADVISORY SHADOW INTEGRATION
- NOT READY

No code changes unless needed solely to fix a proven safety defect, and any such change must be separately reported. Do not commit.
```

---

# Recommended Execution Order

1. Prompt 1 — Audit
2. Prompt 2 — ADR/contracts
3. Prompt 3 — Backend timeline
4. Prompt 4 — Observatory UI
5. Prompt 5 — Replay packages
6. Prompt 6 — Replay UI
7. Prompt 7 — Labels/benchmarks
8. Prompt 8 — Dataset/experiment registry
9. Prompt 9 — Supervised baseline
10. Prompt 10 — Pattern discovery
11. Prompt 11 — Visual rendering
12. Prompt 12 — Multimodal analyst
13. Prompt 13 — Champion/challenger
14. Prompt 14 — Readiness audit

Do not begin with multimodal screenshots. The reliable order is:

**canonical structured truth → replay → labels → datasets → baselines → visual specialist → governed challenger evaluation.**
