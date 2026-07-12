# OmniTrade Decision Inspector & Decision Explorer Specification

## Status

**Proposed implementation specification**

This document defines the operator-facing Decision Inspector and Decision Explorer for OmniTrade Legacy Engine.

It is an implementation-ready specification for a read-only Decision Intelligence interface that allows humans and future AI research workflows to understand, search, compare, and audit governed investment decisions.

This feature is a surface of the existing **Decision Intelligence Engine**. It is not a fifth core engine, not an execution subsystem, and not a substitute for the Risk Engine, Preview workflow, Approval workflow, Audit Log, Counterfactual Outcome Ledger, or Decision Quality Engine.

---

## 1. Purpose

OmniTrade now persists the core artifacts needed to reconstruct a governed investment decision:

- Decision Record
- Decision Snapshot
- Execution Price Evidence
- Risk Event
- Crypto Order Preview
- Audit events
- Optional downstream approval, rehearsal, submission, execution, and outcome records

The missing capability is a single operator interface that assembles these artifacts into a coherent explanation.

The Decision Inspector and Decision Explorer exist to answer:

> What did OmniTrade decide, why did it decide that, what evidence did it use, what rules were evaluated, what happened afterward, and can a human or future AI researcher reconstruct the decision without reading source code or raw logs?

The primary goal is not visual polish. The primary goal is **complete, trustworthy decision reconstruction**.

---

## 2. Product Boundary

### 2.1 Decision Explorer

The **Decision Explorer** is the searchable, filterable index of Decision Records.

It answers:

- What decisions exist?
- Which decisions were approved, rejected, resized, held, expired, rehearsed, submitted, filled, or cancelled?
- Which providers, assets, strategies, campaigns, rules, regimes, and confidence levels are associated with them?
- Which decisions need human review?
- Which decisions share the same rejection cause or decision pattern?

### 2.2 Decision Inspector

The **Decision Inspector** is the detail view for one Decision Record.

It answers:

- What exactly happened?
- What market and portfolio state existed at decision time?
- Which provider-native price evidence was used?
- Which risk rules passed or failed?
- What was the first failing rule?
- What preview was created?
- Was approval created?
- Was rehearsal run?
- Was an order submitted or filled?
- What audit events prove the sequence?
- What counterfactual and decision-quality data became available later?

### 2.3 Explicit Non-Goals

The first implementation must not:

- create, edit, approve, rehearse, submit, cancel, or retry an order;
- alter a Decision Record, Decision Snapshot, Risk Event, Preview, Approval, or Audit Log;
- change risk thresholds;
- change provider configuration;
- change strategy configuration;
- trigger AI recommendations automatically;
- become a general trading terminal;
- become a duplicate Risk Monitor;
- expose secrets, decrypted credentials, signatures, private keys, or raw authorization headers;
- depend on raw terminal logs for core explanation;
- reconstruct missing facts by guessing.

The initial product is strictly **read-only**.

---

## 3. Governing Principles

### 3.1 Explainability First

Every displayed conclusion must be traceable to persisted evidence.

The interface must distinguish clearly between:

- persisted fact;
- derived interpretation;
- unavailable information;
- later hindsight analysis.

### 3.2 Fail Visible

Missing or broken linkage must be shown explicitly.

Examples:

- `Decision Snapshot unavailable`
- `Risk Event linkage missing`
- `Price Evidence metadata incomplete`
- `No approval exists`
- `No rehearsal was run`
- `Outcome not yet known`

The UI must never replace missing evidence with plausible defaults.

### 3.3 Immutable History

The Inspector reads historical decision state. It does not rewrite it.

Later annotations, reflections, counterfactual outcomes, and quality scores must be presented as later layers rather than silently merged into the original decision-time reasoning.

### 3.4 Operator Safety

The Explorer and Inspector must not expose an action that could move capital.

All live controls remain on their existing governed surfaces.

### 3.5 Decision Quality Over Outcome

Profit and loss are important but must not dominate the page.

A decision can be:

- well reasoned and unprofitable;
- poorly reasoned and profitable;
- risk-disciplined but opportunity-costly;
- lucky;
- unlucky;
- incomplete due to unavailable evidence.

The interface must keep decision quality distinct from financial outcome.

---

## 4. User Roles

### 4.1 Primary Operator

Needs to understand current and historical decisions, especially before approval or after rejection.

### 4.2 Auditor / Reviewer

Needs immutable chronology, provenance, rule evaluation, and actor attribution.

### 4.3 Strategy Researcher

Needs to filter and compare decisions by strategy, regime, confidence, rejection cause, and outcome.

### 4.4 Future AI Researcher

Needs structured, queryable decision packages rather than screenshots or terminal output.

### 4.5 Family / Non-Technical Reviewer

Needs a plain-language explanation that does not require knowledge of implementation details.

---

## 5. Information Architecture

Add the following routes:

```text
/decisions
/decisions/[decisionId]
/decisions/compare
```

Future routes, not required for initial delivery:

```text
/decisions/timeline
/decisions/analytics
/decisions/quality
/decisions/counterfactuals
```

Sidebar label:

```text
Decision Intelligence
```

Primary navigation children:

- Explorer
- Compare
- Analytics — future

---

## 6. Decision Explorer Page

### 6.1 Page Header

Display:

- Title: `Decision Explorer`
- Subtitle: `Search and inspect every governed market decision.`
- Environment badge
- Data freshness timestamp
- Total matching decisions
- Read-only badge

### 6.2 Summary Strip

Show counts for the current filter set:

- Total decisions
- Approved / accepted
- Risk rejected
- Hold / wait
- Preview ready
- Submitted
- Executed
- Needs review
- Missing linkage

Counts must be derived from the same filtered query as the result list.

### 6.3 Filter Bar

Support:

- Date/time range
- Environment
- Decision outcome/status
- Recommended action: BUY / SELL / WAIT / HOLD
- Trade accepted: yes / no
- Provider
- Venue
- Product / asset
- Quote currency
- Strategy
- Agent
- Capital campaign
- Live trading profile
- Paper account
- Market regime
- Confidence range
- Risk verdict
- First failing risk rule
- Rejection category
- Review status
- Has preview
- Has approval
- Has rehearsal
- Has submission
- Has execution
- Has Decision Snapshot
- Has Price Evidence
- Has Risk Event
- Has Counterfactual Outcome
- Has Decision Quality Score

### 6.4 Search

Full-text search should cover safe textual fields such as:

- decision ID
- preview ID
- product
- provider
- strategy name
- explanation
- rejection reason
- risk reason codes
- lesson tags
- human notes
- audit correlation ID

Search must not scan or expose secrets.

### 6.5 Sort Options

- Newest first
- Oldest first
- Highest confidence
- Lowest confidence
- Highest decision quality
- Lowest decision quality
- Largest requested notional
- Largest approved notional
- Most recently reviewed

### 6.6 Decision Result Row / Card

Each result must show:

- Decision timestamp
- Decision ID, copyable
- Product / asset
- Provider / venue
- Recommended action
- Final decision status
- Confidence
- Risk verdict
- First failing rule, when rejected
- Requested and approved notional
- Preview status
- Approval status
- Execution status
- Decision Quality Score, when available
- Review status
- Evidence completeness indicator

Use compact visual status labels:

- `ACCEPTED`
- `RISK_REJECTED`
- `WAIT`
- `PREVIEW_READY`
- `APPROVED`
- `REHEARSED`
- `SUBMITTED`
- `FILLED`
- `EXPIRED`
- `INCOMPLETE_EVIDENCE`

### 6.7 Evidence Completeness Indicator

Each result should compute a non-authoritative completeness summary:

```text
Complete
Partial
Incomplete
Unknown
```

This is not a Decision Quality Score.

Suggested required links for `Complete`:

- Decision Record
- Decision Snapshot
- execution price evidence or explicit research evidence
- Risk Event or persisted risk evaluation
- audit correlation
- outcome state appropriate to lifecycle

Hover or expand to reveal missing components.

### 6.8 Empty and Error States

Required states:

- No decisions exist
- No decisions match filters
- Decision service unavailable
- Partial results returned
- Unknown status due to missing linkage

Never display an empty successful table when the API failed.

---

## 7. Decision Inspector Page

### 7.1 Header

Display:

- Decision action and final status
- Product
- Provider / venue
- Decision timestamp
- Decision ID
- Audit correlation ID
- Review status
- Environment badge
- Read-only badge

Primary plain-language summary:

```text
OmniTrade rejected a $5 BTC-USD buy preview because the Risk Engine could not confirm an account safety state.
```

The summary must be generated from persisted reason codes and values, not an unconstrained language model.

### 7.2 Decision Lifecycle Timeline

Render a chronological lifecycle:

```text
Market / execution evidence observed
→ Strategy or operator candidate created
→ Risk evaluated
→ Decision Record persisted
→ Preview created or rejected
→ Approval created or absent
→ Rehearsal run or absent
→ Submission attempted or blocked
→ Execution outcome
→ Post-trade review
→ Counterfactual evaluation
→ Decision Quality evaluation
```

Each event must show:

- timestamp
- event type
- status
- actor
- linked record ID
- source system
- audit event ID, when available

Missing stages must display `Not reached`, `Not applicable`, or `Unknown`.

### 7.3 Overview Panel

Show:

- Final recommendation
- Trade accepted
- Decision status
- Requested notional
- Risk-approved notional
- Final quantity
- Confidence
- Market regime
- Review status
- Outcome category
- P&L when known
- Duration when known

### 7.4 Why Panel

This is the core operator explanation.

Show:

- Human-readable decision explanation
- Supporting evidence
- Opposing evidence
- Risk adjustments
- First failing rule
- Rejection reason
- Whether rejection is temporary, state-dependent, or policy-based, when derivable from stored metadata

The panel must separate:

- original decision-time reasoning;
- later AI reflection;
- later human annotation.

### 7.5 Risk Evaluation Panel

Display the deterministic rule sequence.

For every rule evaluated:

- evaluation order
- rule ID
- rule name
- result: PASS / WARN / RESIZE / REJECT / NOT_EVALUATED
- threshold
- observed value
- comparison operator
- reason code
- explanation
- configuration version

Highlight:

- first failing rule;
- rules not evaluated because of short-circuiting;
- resizing changes;
- fail-closed unknown states.

Example:

```text
1. Global Kill Switch State — PASS
2. Account Kill Switch State — REJECT
   Reason: account_kill_switch_state_unknown
   Expected: known engaged_state and rearm_required
   Observed: unavailable
3. Daily Loss Limit — NOT_EVALUATED
```

Do not infer thresholds or values that were not persisted.

### 7.6 Execution Price Evidence Panel

Show the provider-native evidence package:

- Evidence ID
- Provider
- Venue
- Product
- Base currency
- Quote currency
- Bid
- Ask
- Midpoint
- Last trade price
- Chosen reference price
- Source endpoint
- Observed timestamp
- Retrieved timestamp
- Latency
- Evidence age at evaluation
- Freshness threshold
- Freshness result
- Confidence / quality classification if supported

Explicitly show:

```text
No cross-quote substitution
```

when exact quote identity was validated.

If a fallback or mapped source is ever permitted in the future, the mapping must be displayed prominently.

### 7.7 Decision Snapshot Panel

Show immutable decision-time context:

- Asset
- Exchange
- Timeframe
- OHLCV context summary
- Indicators
- Generated features
- Regime
- Volatility
- Spread / liquidity context
- Current position state
- Open trades
- Portfolio exposure
- Account equity
- Strategy inputs
- Risk inputs
- Strategy version
- Parameter set version
- AI model version
- Decision engine version
- Risk configuration version

Large JSON-like sections should be structured, collapsible, searchable, and copyable.

### 7.8 Preview Panel

Show:

- Preview ID
- Preview status
- Created at
- Expires at
- Provider
- Product
- Side
- Requested notional
- Approved notional
- Quantity
- Estimated price
- Estimated fee
- Estimated total debit
- Available balance observed
- Minimum notional
- Quantity increment / precision
- Provider response summary
- Submission-enabled flag at preview time
- Dry-run flag

### 7.9 Approval Panel

Show:

- Approval status
- Approval ID
- Created at
- Expires at
- Actor
- Scope
- Reason
- Preconditions
- Whether approval was consumed

When no approval exists:

```text
No approval exists. No live submission was authorized.
```

The Inspector must not include an Approve button in the initial release.

### 7.10 Rehearsal Panel

Show:

- Rehearsal status
- Rehearsal ID
- Timestamp
- Inputs
- Guard state
- Simulated provider request
- Result
- Warnings
- Audit evidence

When absent:

```text
No rehearsal was run.
```

### 7.11 Submission and Execution Panel

Show:

- Submission enabled at decision time
- Submission attempted
- Submission ID
- Provider order ID, masked as appropriate
- Submitted timestamp
- Fill state
- Filled quantity
- Average fill price
- Fees
- Slippage
- Reconciliation status
- Final outcome

When no submission occurred:

```text
No order was submitted. No capital moved.
```

### 7.12 Audit Trail Panel

Display immutable chronological audit events linked by:

- decision ID
- preview ID
- risk event ID
- correlation ID
- approval ID
- rehearsal ID
- submission ID

Each event shows:

- timestamp
- actor
- action
- entity type
- entity ID
- before state
- after state

Sensitive fields must remain redacted.

### 7.13 Counterfactual Panel

When available:

- Shadow BUY
- Shadow SELL
- Shadow WAIT
- Horizon
- Hypothetical return
- Best action in hindsight
- Whether original recommendation was correct
- Lesson tags

When unavailable:

```text
Counterfactual evaluation not yet available.
```

### 7.14 Decision Quality Panel

When available:

- Overall Decision Quality Score
- Evidence quality
- Reasoning quality
- Risk discipline
- Confidence calibration
- Outcome independence marker
- Score version
- Human review status

The UI must state clearly:

```text
Decision Quality is not the same as profitability.
```

### 7.15 AI Reflection and Human Notes

Separate sections:

- Original explanation
- Post-trade review
- AI reflection
- Human notes

All later content must include author/model, timestamp, and version.

The initial Inspector is read-only; adding notes can be a later governed phase.

### 7.16 Raw Evidence Drawer

Provide a read-only, safe, structured representation of the assembled decision package.

Support:

- copy safe JSON
- download safe JSON
- field-level provenance labels

Never include secrets or unredacted provider credentials.

---

## 8. Decision Compare

Initial comparison supports 2–4 decisions.

### 8.1 Comparison Dimensions

- Product
- Provider
- Timestamp
- Regime
- Confidence
- Recommended action
- Risk verdict
- First failing rule
- Requested notional
- Approved notional
- Evidence freshness
- Preview result
- Approval / rehearsal / submission lifecycle
- Outcome
- Decision Quality Score
- Counterfactual best action

### 8.2 Comparison Use Cases

- Approved versus rejected BTC previews
- Same strategy across different regimes
- Same rule across providers
- High-confidence losses versus low-confidence wins
- Rejections that later would have won
- Risk overrides that prevented losses

### 8.3 Comparison Safety

Comparison is read-only and must never offer bulk action or approval.

---

## 9. Backend Read Model

### 9.1 Principle

Do not make the frontend assemble the decision story through many unrelated API calls.

The backend should expose a dedicated read model that joins existing persisted records into a safe, versioned response.

### 9.2 Proposed Endpoints

#### `GET /decisions`

Purpose: Decision Explorer search and pagination.

Query parameters:

```text
start_time
end_time
environment
status
action
trade_accepted
provider
venue
product
asset_id
strategy_id
agent_id
campaign_id
live_trading_profile_id
paper_account_id
market_regime
min_confidence
max_confidence
risk_verdict
first_failing_rule
rejection_category
review_status
has_preview
has_approval
has_rehearsal
has_submission
has_execution
has_snapshot
has_price_evidence
has_risk_event
has_counterfactual
has_quality_score
search
sort
limit
cursor
```

Response shape:

```json
{
  "items": [
    {
      "decision_id": "uuid",
      "timestamp": "2026-07-12T04:42:45Z",
      "asset": {"id": "uuid", "symbol": "BTC", "product": "BTC-USD"},
      "provider": "kraken_spot",
      "venue": "kraken",
      "recommended_action": "BUY",
      "decision_status": "RISK_REJECTED",
      "trade_accepted": false,
      "confidence": "0.72",
      "risk_verdict": "REJECT",
      "first_failing_rule": "account_kill_switch_state_unknown",
      "requested_notional_usd": "5.00",
      "approved_notional_usd": "0.00",
      "preview_status": "RISK_REJECTED",
      "approval_status": "MISSING",
      "execution_status": "NOT_SUBMITTED",
      "decision_quality_score": null,
      "review_status": "unreviewed",
      "evidence_completeness": "partial"
    }
  ],
  "summary": {
    "total": 1,
    "accepted": 0,
    "rejected": 1,
    "wait": 0,
    "needs_review": 1,
    "missing_linkage": 0
  },
  "next_cursor": null
}
```

#### `GET /decisions/{decision_id}`

Purpose: Complete Decision Inspector read model.

Response must be versioned:

```json
{
  "schema_version": "1.0",
  "decision": {},
  "snapshot": {},
  "execution_price_evidence": {},
  "risk_evaluation": {},
  "preview": {},
  "approval": null,
  "rehearsal": null,
  "submission": null,
  "execution": null,
  "audit_events": [],
  "counterfactuals": [],
  "decision_quality": null,
  "ai_reflections": [],
  "human_notes": [],
  "linkage_health": {
    "status": "partial",
    "missing": ["approval", "rehearsal", "submission"],
    "unexpected_missing": []
  }
}
```

#### `POST /decisions/compare`

Read-only semantic operation.

Request:

```json
{
  "decision_ids": ["uuid", "uuid"]
}
```

Response:

```json
{
  "schema_version": "1.0",
  "items": [],
  "comparison": {
    "shared": {},
    "differences": {},
    "warnings": []
  }
}
```

This endpoint performs no state mutation and must not write audit events merely for reading.

### 9.3 Error Behavior

- `400` invalid filters or comparison size
- `404` unknown decision ID
- `409` irreconcilable duplicate/corrupt linkage where a single canonical record is required
- `503` read model unavailable

A partial linkage is generally a successful `200` response with explicit `linkage_health`, not a hidden server error.

---

## 10. Read-Model Assembly Rules

### 10.1 Canonical Root

The canonical root is `DecisionRecord`.

All Inspector queries begin with a Decision Record ID.

Preview, Risk Event, Snapshot, Price Evidence, Approval, Rehearsal, Submission, Execution, Audit, Counterfactual, and Quality records are linked into that root.

### 10.2 No Heuristic Joining

Do not join records merely because timestamps, product, and account look similar.

Use explicit identifiers and correlation fields.

Heuristic matches may be displayed only as diagnostic suggestions and must never be presented as canonical linkage.

### 10.3 Duplicate Detection

If multiple records claim a one-to-one relationship:

- do not silently choose one;
- return a linkage warning;
- preserve all safe IDs for operator investigation.

### 10.4 Redaction

The read model must remove:

- API keys
- API secrets
- private keys
- passphrases
- OTP values
- authorization headers
- raw request signatures
- unmasked sensitive provider payloads

### 10.5 Numeric Precision

Return monetary and quantity values as strings.

### 10.6 Time

Return ISO 8601 UTC timestamps.

---

## 11. Data Completeness and Linkage Health

Define linkage health independently from decision outcome.

### 11.1 `complete`

All required records for the decision’s reached lifecycle stage are present.

### 11.2 `partial`

Optional or not-yet-reached lifecycle records are absent, but the decision remains explainable.

### 11.3 `incomplete`

A required record is missing, such as:

- no Decision Snapshot;
- no Risk Event for a risk decision;
- no evidence identity for a preview;
- no audit correlation.

### 11.4 `corrupt`

Conflicting one-to-one linkage, invalid IDs, impossible chronology, or mismatched provider/product identity.

The Explorer must allow filtering by linkage health.

---

## 12. Plain-Language Explanation Rules

The initial explanation generator must be deterministic and template-based.

Example templates:

### Risk Rejection

```text
OmniTrade rejected a {requested_notional} {product} {side} proposal because {rule_name}. The rule expected {expected_state_or_threshold}; the observed value was {observed_value}. No order was submitted.
```

### Resize

```text
OmniTrade reduced the proposal from {requested_notional} to {approved_notional} because {reason}. The final quantity was {quantity}.
```

### Approval Missing

```text
The preview passed risk review, but no human approval exists. Live submission remains unauthorized.
```

### Evidence Missing

```text
The decision cannot be fully explained because {missing_component} was not persisted.
```

A future LLM explanation may supplement these templates, but it must cite persisted facts and must never replace the deterministic explanation.

---

## 13. UI Components

Suggested reusable components:

```text
DecisionStatusBadge
DecisionActionBadge
EvidenceCompletenessBadge
DecisionSummaryCard
DecisionFilterBar
DecisionResultTable
DecisionLifecycleTimeline
DecisionExplanationPanel
RiskRuleSequence
PriceEvidenceCard
DecisionSnapshotViewer
PreviewCard
ApprovalCard
RehearsalCard
ExecutionCard
AuditEventTimeline
CounterfactualMatrix
DecisionQualityCard
LinkageHealthPanel
SafeJsonDrawer
DecisionCompareMatrix
```

All components must support loading, empty, partial, unknown, and error states.

---

## 14. Accessibility

- Full keyboard navigation
- Proper headings and landmarks
- Status conveyed through text and icon, not color alone
- Expandable panels use accessible disclosure controls
- Tables remain usable on narrow screens
- Copy buttons include accessible labels
- Timeline ordering remains understandable to screen readers
- No auto-refresh that steals focus

---

## 15. Performance

### Explorer

- Cursor pagination
- Default page size: 50
- Maximum page size: 200
- Server-side filtering and sorting
- Debounced search
- No N+1 queries

### Inspector

- One dedicated read-model endpoint
- Reasonable lazy loading allowed for very large audit or counterfactual collections
- Initial critical decision explanation should render before noncritical later-analysis panels

### Indexing Review

Before implementation, inspect query patterns and existing indexes.

Create migrations only when evidence shows required production query performance cannot be achieved with existing indexes.

Do not create speculative indexes.

---

## 16. Security and Authorization

### 16.1 Read Authorization

All endpoints require authenticated access.

Future role support:

- operator
- reviewer
- auditor
- researcher

Initial implementation may use the current authenticated operator boundary if role infrastructure is not yet available.

### 16.2 Data Scope

Users may only read decisions belonging to accounts, campaigns, or environments they are authorized to inspect.

### 16.3 No Mutation Routes

The initial release must not add:

```text
POST /decisions/{id}/approve
PATCH /decisions/{id}
DELETE /decisions/{id}
POST /decisions/{id}/submit
```

### 16.4 Safe Export

Safe JSON export must use the same redaction layer as the API response.

---

## 17. Observability

Track read-side metrics:

- Explorer request count
- Inspector request count
- Compare request count
- Read-model latency
- Read-model errors
- Partial linkage frequency
- Incomplete linkage frequency
- Corrupt linkage frequency
- Most common missing linkage type

Do not write application audit rows for ordinary read operations unless policy later requires access auditing.

Security access logs remain separate from decision-history audit events.

---

## 18. Testing Requirements

### 18.1 Backend Unit Tests

- filter parsing
- cursor pagination
- sort behavior
- deterministic explanation templates
- linkage health classification
- redaction
- comparison logic
- missing optional data
- missing required data
- duplicate linkage
- corrupt chronology

### 18.2 Backend Integration Tests

Create fixtures for:

1. Risk-rejected preview with complete linkage
2. Preview-ready decision without approval
3. Approved decision without rehearsal
4. Rehearsed decision without submission
5. Submitted and filled decision
6. WAIT decision without preview
7. Paper execution decision
8. Decision with counterfactual outcomes
9. Decision with Decision Quality Score
10. Decision with incomplete legacy linkage

### 18.3 Frontend Tests

- Explorer filters and pagination
- Search
- Status rendering
- Inspector loading/error/partial states
- Risk rule sequence
- Evidence display
- Timeline ordering
- Safe JSON drawer
- Compare selection and rendering
- Responsive layouts
- Accessibility checks

### 18.4 Safety Regression Tests

Prove:

- no Inspector endpoint mutates data;
- no approval is created;
- no rehearsal is run;
- no submission is enabled;
- no order is placed;
- sensitive fields are redacted;
- missing evidence remains visible;
- risk conclusions are not recomputed differently from stored results.

---

## 19. Implementation Phases

### Phase A — Repository and Linkage Audit

Deliverables:

- map all current Decision Record relationships;
- identify exact reusable services and schemas;
- identify missing explicit links;
- document legacy incomplete cases;
- confirm no new write model is required for basic Inspector delivery.

Exit criteria:

- one verified read-model map from DecisionRecord to all related artifacts.

### Phase B — Backend Read Model

Deliverables:

- `GET /decisions`
- `GET /decisions/{decision_id}`
- filters, pagination, sorting
- linkage health
- deterministic explanation
- redaction layer

Exit criteria:

- complete API integration tests for representative lifecycle states.

### Phase C — Decision Explorer UI

Deliverables:

- Explorer page
- filters
- search
- result table/cards
- summary strip
- completeness state

Exit criteria:

- operator can locate the first governed Kraken preview decision by ID and status.

### Phase D — Decision Inspector UI

Deliverables:

- lifecycle timeline
- Why panel
- risk rule sequence
- execution price evidence
- Decision Snapshot
- preview, approval, rehearsal, execution, audit panels
- raw safe JSON drawer

Exit criteria:

- operator can explain a risk rejection without terminal logs or source-code reading.

### Phase E — Compare

Deliverables:

- select 2–4 decisions
- comparison endpoint
- comparison UI

Exit criteria:

- operator can compare an approved and rejected decision and identify the first meaningful divergence.

### Phase F — Analytics Expansion — Future

Potential later work:

- rejection frequency dashboard
- rule heatmaps
- confidence calibration
- counterfactual analytics
- decision quality trends
- AI research query workspace

Not part of the initial Inspector/Explorer build.

---

## 20. Initial Production Acceptance Scenario

The first acceptance scenario should use the real Kraken preview decision generated during Phase 10B.

The system must allow the operator to search for its preview or linked Decision Record and view:

- Kraken provider-native BTC-USD price evidence
- requested $5 notional
- Risk Engine verdict
- first failing rule
- full persisted rule sequence, if available
- Decision Snapshot
- risk event ID
- preview ID
- audit events
- explicit confirmation that no order was submitted
- explicit display that approval and rehearsal are absent

If any artifact is not yet persisted, the Inspector must display the exact missing linkage rather than concealing it.

---

## 21. Acceptance Criteria

The feature is complete when:

1. Every accessible Decision Record appears in the Explorer.
2. Results can be filtered by risk rejection reason and provider.
3. A user can open a Decision Inspector from the Explorer.
4. The Inspector assembles persisted decision, snapshot, evidence, risk, preview, audit, and lifecycle data.
5. The first failing risk rule is visible when persisted.
6. Missing linkage is explicit.
7. No action on these pages can move capital or change governance state.
8. Safe JSON export contains no secrets.
9. The initial Kraken `$5` preview decision can be reconstructed from the UI.
10. Backend tests, frontend tests, frontend lint, and relevant builds pass.
11. No Risk Engine decision changes are introduced.
12. No provider-native evidence behavior changes are introduced.
13. No approval, rehearsal, or submission behavior changes are introduced.

---

## 22. Recommended ADR

Create an ADR if one does not already establish this read-model principle:

```text
DecisionRecord is the canonical root for governed decision inspection.
```

The ADR should explain:

- why the frontend should consume a dedicated assembled read model;
- why timestamp-based heuristic linkage is prohibited;
- why incomplete linkage must remain visible;
- why Inspector and Explorer are read-only;
- why deterministic explanations precede optional AI-generated summaries.

Suggested filename:

```text
docs/adr/ADR-0010-decision-inspector-canonical-read-model.md
```

---

## 23. Recommended Copilot Implementation Sequence

Use separate prompts for:

1. Repository linkage audit and implementation plan
2. Backend Explorer endpoint
3. Backend Inspector read model
4. Backend tests and redaction review
5. Explorer UI
6. Inspector UI
7. Compare workflow
8. Production validation using the real Kraken decision

Do not implement the full feature in one giant prompt.

---

## 24. Permanent Safety Statement

The Decision Inspector and Decision Explorer observe decisions.

They do not create decisions, approve decisions, rehearse decisions, submit decisions, or alter decisions.

The Risk Engine remains final authority.

Human approval remains mandatory.

Live submission remains separately governed.

The Decision Intelligence Engine remains the permanent institutional memory of OmniTrade.
