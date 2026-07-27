# AUTHORITY_AND_ACCOUNTABILITY_MODEL.md

Version: 1.0
Status: Constitutional Architecture (proposed)
Layer: Governance — sits directly beneath `PROJECT_CONSTITUTION.md`, alongside `WORLD_STATE_AND_KNOWLEDGE_MODEL.md`
Scope: Who — or what — is permitted to act on knowledge, under what authority, with what accountability

**Evidence tags:** `[VERIFIED]` confirmed in the repository this session · `[DECIDED]` established in a prior ADR/authorization · `[RECOMMENDATION]` proposed here · `[CHALLENGE]` a deliberate departure from the proposed framing.

The companion document `WORLD_STATE_AND_KNOWLEDGE_MODEL.md` governs *knowing*. This document governs *acting on what is known*. The goal is not maximum autonomy; it is maximum **trustworthy, governed, accountable** autonomy.

---

## 0. The one idea

**Knowledge is not authority. Authority is a grant.** Knowing that a profitable trade is available confers no right to make it. Authority to act must be *explicitly granted, always bounded, always revocable, and always owned by an accountable human* — and it flows *down* through delegation while accountability flows *up* and never dissipates.

`[VERIFIED]` The codebase already embodies this at its core: a strategy can generate a BUY signal but cannot execute it; the Risk Engine (`evaluate_signal_risk`) can *know* a trade is attractive and still reject it; a mandate can *authorize candidacy* without *authorizing execution*; and no AI path can grant capital authority (the mandate and campaign authority services import no AI/LLM whatsoever). This document elevates that existing instinct into permanent law.

---

## 1. Purpose — why an explicit authority model is mandatory

Six concepts the system must never conflate:

- **Knowledge** — what can be known (governed by the World-State model). Passive.
- **Authority** — the granted right to act. Flows down from human ownership.
- **Permission** — a specific, scoped instance of authority (this action, this asset, this size, now).
- **Execution** — the mechanical act of doing. Requires permission; is not itself authority.
- **Responsibility** — the duty to act correctly within one's authority.
- **Accountability** — the durable, human-terminating answer to "who is answerable for what happened."

Knowledge never grants authority because the two answer different questions: knowledge answers *what is*, authority answers *what may be done*, and a capital system that lets the first imply the second has no governor at all — every observed opportunity would become a self-authorizing action. The entire safety architecture exists in the gap between "OmniTrade knows X" and "OmniTrade may act on X," and this document owns that gap.

---

## 2. Authority Hierarchy — a chain of permission, a lattice of veto

`[CHALLENGE]` The proposed hierarchy (Owner → Operator → Campaign → Risk → Execution → Provider → Market) is presented as a single linear chain. That is the most important thing to correct, because it is only half true and the missing half is where capital gets lost.

**Positive authority (the right to *initiate/permit* an action) flows down as a chain.** Negative authority (the right to *deny or halt*) does **not** — it is a distributed, non-overridable lattice. Two examples the linear model gets wrong:
- The Risk Engine sits "after" the campaign, yet a campaign cannot authorize its way past Risk. `[VERIFIED]` `evaluate_signal_risk` rejects regardless of mandate authorization; the mandate authorizes *candidacy*, Risk authorizes *execution*, and neither can grant the other's authority.
- A kill switch is "low" in execution terms yet halts everything above it, and `[VERIFIED]` only a human may rearm it (`actor_is_human`).

So the correct model is **three separable authorities**, and no component should hold all three for the same action (separation of powers):

| Authority | Meaning | Who holds it (`[VERIFIED]`) |
|---|---|---|
| **Initiating** | propose an action; produces only a candidate | Strategies, AI (proposals only), the autonomous cycle |
| **Approving** | permit a candidate to proceed | Mandate/Campaign (delegated, bounded), Risk Engine (independent veto), Human (gated transitions) |
| **Halting** | stop actions already permitted | Kill switches, Operator, Risk — exercisable *upward* against higher intentions |

The levels, restated with what each **does** and **does not** possess:

- **Human Owner** — source of all authority; may grant, bound, revoke, and amend. Possesses constitutional authority. Does not possess the right to bypass the audit trail (even the owner's actions are recorded).
- **Human Operator** — commissions venues, approves gated transitions, halts. `[VERIFIED]` venue commissioning requires an `actor` + explicit `confirm` + an enabled gate and raises `PermissionError` if commissioning authority is missing. Does not possess constitutional-amendment authority.
- **Approved Campaign / Mandate** — the operational unit of *delegated, bounded* autonomy. `[VERIFIED]` a mandate version pins `max_order_notional_usd`, `allowed_products`, `allowed_order_sides`, `allowed_strategy_versions`, and an `autonomy_level`; a campaign carries an `owner`, a lifecycle, and DB-enforced capital/risk ceilings. Possesses authority to *initiate candidates within its grant*. Does **not** possess authority to exceed its ceilings, override Risk, or extend itself.
- **Risk Engine** — the independent approving/halting veto. Possesses reject/resize/hold/kill authority. Does **not** possess authority to *originate* trades, allocate capital, or terminate a campaign (§10).
- **Execution Engine** — translates an approved, risk-cleared decision into a provider submission. Possesses submission authority within a permission. Does **not** possess discretion to alter the decision.
- **Execution Provider / Exchange / Market** — `[CHALLENGE]` **outside OmniTrade's trust boundary.** They are external counterparties and evidence sources, not delegates. They may *refuse* (reject an order) but they never *hold OmniTrade's authority*. Authority ends at OmniTrade's execution boundary; beyond it lies submission to an external party and reconciliation of what it actually did.

---

## 3. Sources of Authority

Every legitimate source, and its nature:

- **Human authorization** — the root. All other sources derive from it.
- **Campaign / mandate approval** — `[VERIFIED]` delegated, bounded authority; `HUMAN_REQUIRED` vs `MANDATE_ALLOWED` approval policy; `human_approval_required` recorded per evaluation.
- **Risk approval** — permission conditioned on the world's observability and the account's state; independent, non-overridable veto.
- **Policy approval** — the risk/aggression policy under which a campaign runs (`[VERIFIED]` `aggression_mode` up to `MAXIMUM_GOVERNED` — never "ungoverned").
- **Operator commissioning** — `[VERIFIED]` `venue_commissioning` gated by `venue_commissioning_enabled`, actor, and explicit confirmation.
- **Production configuration** — the deployment-level enablement of live capability (`[VERIFIED]` `live_crypto_dry_run_enabled`, dry-run boundary).
- **Simulation authorization** — authority scoped to an isolated, non-writable-to-production namespace (ADR-0010); can never touch live capital by construction.
- **Emergency override / halt** — distributed halting authority (§13).
- **Time-based authorization** — `[VERIFIED]` `LiveApprovalEvent.expires_at` + `approval_renewed`/`checkpoint_evaluated` events. `[CHALLENGE]` `expires_at` is nullable — "never expires" is representable, which is a latent gap (§5, §18).
- **Budget authorization** — `[VERIFIED]` campaign `capital_budget`, `remaining_unallocated_capital`, `maximum_total_exposure`, enforced by DB CHECK constraints.

`[RECOMMENDATION]` Add **provenance authorization**: an action's authority is only as valid as the provenance of the evidence it rests on. An action authorized on evidence that cannot state its own availability time (World-State model §5) is not fully authorized — it must fail closed or be downgraded. Authority and evidence integrity are coupled.

---

## 4. Delegation

- **Humans may delegate** — to mandates/campaigns, bounded and revocable. This is the primary delegation.
- **Campaigns may sub-delegate only within their grant** — a campaign may authorize a candidate to a strategy version it already lists, never beyond its own ceilings. `[VERIFIED]` eligibility checks confine candidacy to `allowed_products`/`sides`/`strategy_versions`.
- **AI may never delegate** — because it holds no authority to delegate (§7).
- **Automation may never widen a grant** — the autonomous cycle may act *within* a mandate; it can never grant itself a larger one.

`[RECOMMENDATION]` **What may never be delegated (permanent):** constitutional amendment, the granting of *new* live authority, kill-switch rearm, promotion to production, and custody of the credentials that physically enable execution (§18). These remain human, and among humans, owner-level for the constitutional subset. Delegation may narrow authority; it may never manufacture it.

---

## 5. Limits of Authority

Every grant is bounded on every axis, and the boundaries belong as close to the data as possible. `[VERIFIED]` campaign capital/risk limits are enforced by **DB CHECK constraints** (`capital_budget > 0`, `maximum_position_size >= minimum_position_size`, exposure/drawdown non-negative, etc.) — constitutional boundaries live at the schema layer, harder to bypass than application logic.

Permanent limit axes: **capital** (budget, position size, exposure), **risk** (drawdown, daily loss, kill switches), **time** (authorization expiry, cooldowns), **asset** (`allowed_products`), **side** (`allowed_order_sides`), **venue** (commissioned venues only), **strategy** (`allowed_strategy_versions`), **campaign** (per-campaign ceilings), **simulation** (isolated namespace, no live reachability), **jurisdiction** (`[RECOMMENDATION]` explicit, currently `[UNKNOWN]` whether modeled), **provider** (only capabilities a venue actually supports — `[VERIFIED]` `require_provider_capabilities`), and **learning** (research may never self-promote — §12).

**Constitutional boundaries that may never be crossed by any grant:**
- No action may exceed its explicit authorization (`[VERIFIED]` even the max mode is `MAXIMUM_GOVERNED`).
- No autonomous authority may originate a *new* live grant or widen its own.
- No component may bypass Risk, audit, or the kill switch.
- No simulation may write to production or reach a live provider (ADR-0010).
- `[CHALLENGE][RECOMMENDATION]` **Live authority must carry a finite expiry** and require renewal; standing (non-expiring) live authority is the most dangerous grant and should be constitutionally disallowed — closing the nullable-`expires_at` gap.

---

## 6. Accountability — it terminates at a human, always

Authority without accountability must be structurally impossible. Accountability is the durable answer to "who is answerable," and `[CHALLENGE]` it must **never evaporate into 'the system did it.'**

| For… | Accountable party |
|---|---|
| Knowledge (its provenance/integrity) | The operators who commissioned the data pipelines |
| Decisions | The human authorizer of the mandate/campaign under which the decision was made |
| Risk | The human who set the risk policy in force |
| Execution | The operator who commissioned the venue + the authorizer of the campaign |
| Losses | The accountable owner of the campaign that incurred them (`[VERIFIED]` campaigns carry `owner`) |
| Learning | The human who approves a promotion (§12) |
| Infrastructure | The operators/maintainers |
| Campaigns | Their `owner` |
| Operator actions | The operator, by recorded identity (`[VERIFIED]` `approver_id`/`approver_role`/`rationale` in the approval ledger) |
| AI recommendations | The human who chose to act on them — never the AI |

`[VERIFIED]` accountability is already traceable to human identity: `LiveApprovalEvent` records `approver_id`, `approver_role`, `rationale`, `approval_scope`; campaigns record `owner`; mandate evaluations record `human_approval_required`. The constitutional principle: **every autonomous action inherits a human accountability owner by construction — the authorizer of the grant under which it acted.** An action that cannot name its accountable human is, by definition, unauthorized.

---

## 7. AI Authority — none over capital, ever

`[VERIFIED]` No AI/LLM is on the decision or authority path today: the trading decision is deterministic and LLM-free; the research LLM adapter is `PLANNED`/`NotImplementedError`; and mandate/campaign authority services import no AI. This document makes that permanent, not incidental.

**AI may:** generate hypotheses, recommend strategies (as candidates), annotate decisions (`[VERIFIED]` `DecisionRecord.ai_reflection` is a separate, hindsight-tagged field), summarize, critique, and surface patterns — all as *typed beliefs*, never facts, never authority.

**AI may permanently never:** authorize capital, approve campaigns, modify limits, change policies, override or weaken Risk, issue production orders, rearm a kill switch, promote to production, or hold custody of execution credentials.

`[CHALLENGE]` The bright line is not "AI is untrusted"; it is **AI has no accountability, therefore it may hold no authority.** Accountability terminates at a human (§6); a component that cannot be held accountable cannot be granted authority over capital. The instant an AI action could move real capital without a human in the accountable path, the accountability chain breaks — which is a constitutional violation regardless of how good the AI is. "AI proposes; governed systems and humans approve" is a permanent invariant, not a maturity phase.

---

## 8. Human Authority — the permanent reserve

The following remain human, permanently, and the constitutional subset remains owner-level:

- **Production authorization** and the granting of new live authority.
- **Policy changes** (risk, aggression, limits).
- **Capital allocation at the mandate/campaign-granting level.**
- **Kill switches** — engagement widely available, **rearm human-only** (`[VERIFIED]`).
- **Promotion gates** — the research→production boundary (§12).
- **Constitutional changes** and **repository governance.**

Why: because each of these either *creates* authority or *removes a safety boundary*, and both acts must sit with an accountable human. `[CHALLENGE]` Note the deliberate asymmetry — **halting is widely distributed and easy; resuming is narrow and hard** (`[VERIFIED]` many can trip the kill switch; only a human may rearm). This is the correct default for capital: fail-safe dominates fail-available. Its cost — if the sole authorized human is unavailable, the system stays halted — is accepted, and argues for *breadth* of halting authority and a documented succession of rearm authority (§14, §18).

---

## 9. Campaign Authority — the operational unit of bounded autonomy

`[VERIFIED]` Campaigns are the operational vehicle. Grounded facts:

- **Identity:** a campaign has a stable identity carried through execution (`[VERIFIED]` prior "Campaign Identity" decision; `canonical_campaign_id`/version in `replay_context`).
- **Ownership:** `[VERIFIED]` `capital_campaign.owner` (indexed) — every campaign has an accountable owner.
- **Lifecycle:** `[VERIFIED]` `DRAFT → READY → RUNNING → PAUSED → TARGET_REACHED → COMPLETED → ARCHIVED`; the definition adds `CAPITAL_EXHAUSTED`, `CANCELED`, and `MANUAL_REVIEW_REQUIRED` (explicit human escalation).
- **Permissions:** bounded by mandate (`allowed_products`/`sides`/`strategy_versions`, `autonomy_level`).
- **Limits:** `[VERIFIED]` DB-enforced `capital_budget`, `maximum_open_positions`, `maximum_position_size`, `maximum_total_exposure`, `maximum_drawdown`; `aggression_mode` ceilinged at `MAXIMUM_GOVERNED`.
- **Expiration / suspension / termination:** `PAUSED` (suspension), `CAPITAL_EXHAUSTED`/`TARGET_REACHED`/`COMPLETED` (natural end), `CANCELED`/`ARCHIVED` (termination), `MANUAL_REVIEW_REQUIRED` (escalation to human).
- **Accountability:** anchored to `owner`; every order attributable to the campaign that authorized it (`[VERIFIED]` Campaign Identity decision).

`[RECOMMENDATION]` A campaign should carry an explicit authorization expiry (§5); a long-running campaign whose authorization never lapses is standing authority by another name.

---

## 10. Risk Authority — veto and halt, never origination

`[VERIFIED]` `evaluate_signal_risk` is a pure, deterministic 12-gate pipeline returning `APPROVE` / `RESIZE` / `REJECT`, and the kill-switch logic can block and demands human rearm.

**Risk may:** reject, resize (down or up-to-minimum within bounds), pause (via kill switch state), and escalate (surface a reason). **Risk should never:** originate a trade, approve capital allocation, terminate a campaign, or be overridden by a campaign or by AI.

`[CHALLENGE]` Risk's authority is deliberately *negative and independent*. It is not "one step in the chain that the campaign flows through and can therefore be reasoned around" — it is a separate branch of government with a veto that no positive-authority holder may overrule. Two nuances the current implementation exposes and this document should preserve:
- Risk consumes *observation quality* (`[VERIFIED]` `data_is_stale`/`data_has_gaps`) and refuses to act on stale/gapped observations — Risk's authority *includes the authority to refuse to act on a world it cannot trust*.
- Risk must remain **outcome-blind**: it evaluates the decision, never the outcome that would follow. Letting realized outcomes feed Risk would be both leakage and a circular corruption of its independence.

---

## 11. Execution Authority — submission, then reconciliation

- **Execution Engine:** holds authority to *submit* an approved, risk-cleared decision faithfully; no discretion to alter it. `[VERIFIED]` real `submit_order` occurs at exactly three gated sites, each requiring a resolved provider + decrypted credentials + environment + dry-run boundary.
- **Execution Provider / Exchange / Broker:** `[CHALLENGE]` external counterparties, **outside the trust boundary**; they execute or refuse, but hold none of OmniTrade's authority.
- **Settlement / Reconciliation:** `[VERIFIED]` a full live accounting/reconciliation stack (`live_reconciliation_event`, `live_accounting_record`) — OmniTrade's authority *after* submission is the authority (and duty) to reconcile what actually happened against what it authorized.

The boundary principle: **OmniTrade is accountable for correctly submitting and faithfully reconciling; it is not accountable for the venue's internal behavior, but it is accountable for having chosen to trust that venue.** Choosing a provider is itself an exercise of authority (commissioning, §2) with its own accountability.

---

## 12. Learning Authority — proposal only, human-gated promotion

Can learning modify production? **Never directly.** The promotion path is a sequence of authority checkpoints, each producing immutable evidence:

```
Learning (belief)  → produces candidates, no authority
  ↓
Research           → hypotheses as typed evidence
  ↓
Validation         → immutable scorecards  [VERIFIED: validation_run_scorecards]
  ↓
Historical Simulation (point-in-time, isolated)  [ADR-0010/0011]
  ↓
Untouched-Test window  → provably untouched (immutable datasets)
  ↓
Forward Paper      → real-time, no capital
  ↓
Human Approval     → NON-DELEGABLE, human-only, recorded  [VERIFIED: approval ledger]
  ↓
Production (bounded live)
```

`[VERIFIED]` even the research arena's risk-gate decisions are append-only immutable (`ArenaRiskGateDecision`), so the promotion evidence trail cannot be rewritten. `[CHALLENGE]` The single most consequential authority transfer in the whole system is the promotion boundary — it lets *learned belief* influence *real capital*. It must therefore be the most heavily governed: human-approved, non-delegable to AI, evidence-backed by immutable datasets, and reversible (a promotion must be revocable like any other grant). "It performed well in development" is never authority to reach production.

---

## 13. Emergency Authority — halt widely, resume narrowly

Emergency governance is the distributed halting lattice made concrete:

- **Kill switches / circuit breakers:** `[VERIFIED]` global and account scope; engagement halts trading; **rearm is human-only**. `[VERIFIED]` the reconciliation guard fails closed on any unresolved/ambiguous order state.
- **Operator intervention:** commissioning, suspension, cancellation, `MANUAL_REVIEW_REQUIRED` escalation.
- **Provider failures / market halts:** observation-quality gates cause Risk to refuse; venue-capability limits prevent unsupported operations.
- **Security incidents / unexpected AI behavior / data corruption:** must trip a halt and require human re-authorization to resume; `[VERIFIED]` dataset integrity failures fail closed (ADR-0011 / immutable datasets).

Escalation and revocation: `[RECOMMENDATION]` **anyone with halting authority may halt; only an accountable human may revoke a halt or a grant.** Revocation must be as easy as granting was hard, and must itself be recorded as an authority event (`[VERIFIED]` `approval_revoked`/`approval_suspended` exist in the ledger). The asymmetry (easy to stop, hard to resume) is intentional and constitutional.

---

## 14. Stewardship — expanding Article X

OmniTrade is a **steward** of capital, not its owner. A steward acts for a beneficiary, within a mandate, and answers for the exercise of a trust it did not originate. This reframes every authority above as *fiduciary*: bounded by the beneficiary's interest, exercised with care, and fully accountable.

- **Fiduciary thinking:** the default is capital preservation, not opportunity capture (Article VIII); when preservation and performance conflict, preservation wins. Authority is exercised *for* the beneficiary, never for the system's own optimization targets.
- **Long-term accountability:** accountability outlives any single decision, campaign, or maintainer. The immutable evidence trail (decisions, snapshots, approvals, datasets) exists so that accountability can be discharged *years later*, to people who were not present (Article X).
- **Future maintainers:** `[CHALLENGE]` stewardship includes an obligation the proposal implies but should state — **succession of authority.** A multi-generational, family-legacy system must define how owner/operator authority (and, critically, credential custody and kill-switch rearm) passes to successors, and what happens to running capital if the authorized humans become unavailable. This is not operational trivia; it is the continuity of the accountability chain itself, and it is currently `[UNKNOWN]` in the repository.

---

## 15. Auditability — every exercise of authority is reconstructable

`[VERIFIED]` The substrate exists and is strong: `audit_log` (append-only, polymorphic), immutable `DecisionRecord`/`DecisionSnapshot` with `field_provenance`/`source_lineage`, immutable `ArenaRiskGateDecision`, and the sequenced, hash-chained `LiveApprovalEvent` ledger (`approver_id`, `approver_role`, `rationale`, `approval_scope`, `expires_at`, grant/revoke/suspend/renew/checkpoint).

Every authority exercise must answer, reconstructably: **who** authorized (identity + role), **why** (rationale), **on what evidence** (dataset/version/provenance), **under what policy** (risk/aggression version), **within what limits** (scope), **with what approvals** (ledger chain), **at what versions** (the five snapshot pins + engine/execution-model versions), and **with what accountability** (the human owner in the chain).

`[RECOMMENDATION]` A future auditor should be able to take any executed action and mechanically walk: action → decision record → snapshot (versions) → risk decision → mandate/campaign (owner, limits) → approval ledger (approver, rationale, expiry) → the immutable dataset the decision observed. The World-State model guarantees the *evidence* is reconstructable; this model guarantees the *authority* is. Together they make "why did OmniTrade do this, and who is answerable" a re-executable query, not a story.

---

## 16. Constitutional Principles

1. Knowledge never implies authority.
2. Authority is explicitly granted — never assumed, never inferred from capability.
3. Authority is always bounded on every axis (capital, risk, time, asset, venue, strategy, provider).
4. Authority is always revocable, and revocation is easier than granting.
5. **Authority decays — live authority must expire and be renewed, never stand indefinitely.**
6. Every action has an accountable owner, and **accountability always terminates at a human.**
7. AI proposes; governed systems and humans approve. **AI holds no authority over capital because it holds no accountability.**
8. Positive authority flows down; negative authority (deny/halt) is distributed and non-overridable.
9. **No single component holds initiating, approving, and halting authority for the same action** (separation of powers).
10. There is no ungoverned mode — the ceiling is always a governed ceiling.
11. Capital is stewarded, not owned; authority is fiduciary.
12. Constitutional boundaries live as close to the data as possible (schema-enforced where feasible).
13. Halting is widely held and easy; resuming is narrowly held, human, and hard.
14. **Authorized authority and physical capability must coincide by governance — whoever holds execution credentials effectively holds execution authority, so custody is itself a governed grant** (§18).
15. Learning may never self-promote; the research→production boundary is human and non-delegable.

---

## 17. Relationship to Existing Architecture

This document is the **governance layer**, defining *who may act*. It fits without modifying anything:

- **`PROJECT_CONSTITUTION.md`** — supreme; defines *what we value*. This document operationalizes Articles VIII (Safety) and X (Stewardship) into an authority model, and sits directly beneath it.
- **`WORLD_STATE_AND_KNOWLEDGE_MODEL.md`** — its complement: that governs *knowing*, this governs *acting on the known*. The pair is symmetric.
- **`IMMUTABLE_HISTORICAL_DATASETS.md` / future ADR-0012** — the immutable evidence on which authorized actions rest; §3's provenance-authorization couples the two.
- **`SYSTEM_ARCHITECTURE.md`** — the concrete components that implement these authorities.
- **Historical Intelligence Platform** — its promotion gate is an authority mechanism this document governs (§12); its simulation authority is constitutionally incapable of touching live capital (ADR-0010).
- **Decision Records** — the immutable record of authority-exercised-at-decision-time (§15).
- **Risk Engine** — the independent veto branch (§10).
- **Campaigns / Mandates** — the delegated, bounded operational authority (§9).
- **Execution Providers** — external counterparties at the trust boundary (§11).

Per the operator's constraint, none of these documents is modified; they fit beneath this one.

---

## 18. Architectural Critique

The brief asked for criticism. The sharpest objections:

1. **The proposed hierarchy is linear; authority is not.** Corrected in §2 — a downward chain of positive authority plus a distributed, non-overridable veto/halt lattice. Accepting the linear model would imply a campaign could authorize past Risk, which the code correctly forbids and the doc must forbid constitutionally.
2. **Provider and Market are not authority levels.** They are outside the trust boundary (§2, §11). Modeling them as delegates confuses accountability.
3. **The de jure / de facto authority gap — the biggest hidden assumption.** `[CHALLENGE]` The entire mandate/campaign/Risk stack governs *authorized* authority, but *physical* authority to move capital is held by **whoever possesses the exchange credentials**. `[VERIFIED]` credentials are decrypted per connection and real submission requires them — so anyone (or any process) holding the keys can, in principle, bypass the whole authority stack. This gap between *authorized authority* and *physical capability* is the single most important unmodeled risk, and it is currently treated as operational configuration rather than constitutional governance. It deserves a dedicated document (§20).
4. **Accountability drift is the standing risk.** As autonomy increases, the temptation to say "the system decided" grows. §6/§7 hold the line — accountability terminates at a human — but this is a discipline that must be actively defended, not assumed.
5. **Standing authority via nullable expiry.** `[VERIFIED]` `expires_at` is nullable; a non-expiring live approval is representable. §5 recommends disallowing it. Left unaddressed, it quietly reintroduces open-ended authority.
6. **Succession is unmodeled.** `[UNKNOWN]` how owner/operator authority, credential custody, and rearm authority pass to successors, or what happens to live capital if authorized humans vanish. For a multi-generational legacy system this is a genuine constitutional gap (§14, §20).
7. **Over-governance risk.** `[CHALLENGE]` An authority model this rich could ossify the First Autonomous Profit lane. Mitigation: the model is constitutional (it constrains *what authority is legitimate*), not procedural (it does not add approval steps to already-authorized, in-bounds autonomous action). Governance should be *invisible* when everything is within its grant and *decisive* only at boundaries.

---

## 19. Future Vision

The authority model is what lets autonomy grow without accountability shrinking:

- **Human-operated → Semi-autonomous:** humans grant bounded mandates; automation acts only within them; every action names its human authorizer.
- **Semi-autonomous → Autonomous campaigns:** campaigns carry owners, ceilings, lifecycles, and revocable, expiring authority; Risk vetoes independently; kill switches halt widely.
- **Autonomous campaigns → Historical Intelligence:** research runs in simulation authority that cannot touch live capital by construction.
- **Historical Intelligence → Evidence-based AI research:** AI proposes typed beliefs over immutable evidence; it holds no authority because it holds no accountability.
- **→ Autonomous capital allocation:** every promotion is human-approved, evidence-backed, bounded, expiring, and revocable — so even at full autonomy, every dollar moved traces to an accountable human, a bounded grant, and a reconstructable authority chain.

The invariant that survives all the way up: **autonomy increases; human accountability does not decrease.** Capability may be delegated; answerability never is.

---

## 20. Final Reflection — is the constitutional architecture complete?

`[CHALLENGE]` **Not yet — but it is close, and the goal is a *minimal* complete set, not maximal document production.** The four documents cover four of the five foundations a governed autonomous capital steward needs:

- `PROJECT_CONSTITUTION.md` — *what we value* ✓
- `WORLD_STATE_AND_KNOWLEDGE_MODEL.md` — *what we can know* ✓
- `IMMUTABLE_HISTORICAL_DATASETS.md` — *how knowledge is preserved* ✓
- `AUTHORITY_AND_ACCOUNTABILITY_MODEL.md` — *who may act on it* ✓

Remaining constitutional gaps, ranked by importance with justification:

**1. Custody & Security (`CUSTODY_AND_SECURITY_MODEL.md`) — genuinely constitutional, highest priority.** This document governs *authorized* authority; it does not govern *physical* authority. Whoever holds the exchange credentials, the signing keys, and the deployment access holds the real, ultimate execution power — able in principle to bypass every mandate, Risk gate, and kill switch. The de jure/de facto gap (§18.3) is the deepest unmodeled risk in the system. Because *authority and accountability are meaningless if physical capability is ungoverned*, this is not an operational concern — it is the physical root of the authority tree, and it deserves constitutional status. **Rank 1.**

**2. Identity & Continuity Across Time (`IDENTITY_AND_CONTINUITY_MODEL.md`) — constitutional.** Two intertwined gaps: (a) **canonical identity across time** — authority is granted over assets/campaigns/providers whose identity must survive forks, renames, delistings, and migrations, or a grant silently drifts to a different thing than was authorized; and (b) **succession/continuity** — how owner/operator authority and custody pass across maintainer generations, and what governs live capital if authorized humans become unavailable (§14). Both are load-bearing for a decades-long legacy system and neither has a home. **Rank 2.**

**3. Amendment & Constitutional Change (`CONSTITUTIONAL_AMENDMENT_PROCESS.md`) — arguably a section, arguably a document.** The Constitution says it should change "extremely rarely" but defines no *process* for amending itself or the documents beneath it, no supersession rules across decades, no versioning of the constitutional layer. `[VERIFIED]` repository governance is human, but the *procedure* is unwritten. For a system meant to be maintained by people who were not present at its creation, the amendment procedure is itself constitutional. This could reasonably be folded into `PROJECT_CONSTITUTION.md` rather than stand alone. **Rank 3.**

**A deliberate note against over-proliferation:** `[CHALLENGE]` the goal is trustworthy governed autonomy, not a maximal constitution. Ranks 2b (continuity) and 3 (amendment) could be *sections* of existing documents rather than new ones; only **Custody & Security (Rank 1)** clearly demands its own constitutional document, because it introduces a category — physical capability — that none of the existing five address and that silently underlies all of them. My recommendation: treat Custody & Security as the next and likely final *required* constitutional document; treat identity/continuity and amendment as gaps to close, but weigh folding them into existing documents before minting new ones. Constitutional restraint is itself a form of stewardship.

---

*This document is architecture and governance only. No code was written, the repository was not modified, and no deployment is proposed. It is submitted for adoption as permanent constitutional architecture, sitting directly beneath `PROJECT_CONSTITUTION.md` alongside `WORLD_STATE_AND_KNOWLEDGE_MODEL.md`, with the recommendation that `CUSTODY_AND_SECURITY_MODEL.md` be identified as the next required constitutional document.*
