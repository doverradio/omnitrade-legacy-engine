# WORLD_STATE_AND_KNOWLEDGE_MODEL.md

Version: 1.0
Status: Constitutional Architecture (proposed)
Layer: Semantic / Epistemic — sits directly beneath `PROJECT_CONSTITUTION.md` and above all operational architecture
Scope: What OmniTrade believes exists, and what it can know at any point in time

**Evidence tags:** `[VERIFIED]` confirmed in the repository · `[DECIDED]` established in a prior ADR/authorization · `[RECOMMENDATION]` proposed here · `[CHALLENGE]` a deliberate departure from the proposed framing.

---

## 0. Reading guide: the two ideas this document exists to establish

Everything below reduces to two claims. If only these survive, the document has succeeded.

**0.1 — OmniTrade never touches reality. It touches evidence.** Every number the system has ever held is a lagged, lossy, possibly-wrong *projection* of a world it cannot directly access. The failure mode of every trading system is collapsing eight distinct things — *what is true*, *what was observable*, *what we collected*, *what we normalized*, *what we fed the decision*, *what we decided*, *what happened*, and *what we learned* — into one word, "data," and then treating the latest row in a database as the present truth. This document keeps those eight things permanently distinct and typed.

**0.2 — WorldState is a query, not a store.** `[CHALLENGE]` The most important architectural correction to the proposal: **OmniTrade must not hold a mutable, materialized "current WorldState" object.** The moment such an object exists, it becomes the single place where the future leaks in and where reproducibility dies — it silently reintroduces the mutable operational database as truth, undoing the entire immutable-datasets discipline. WorldState is instead a **pure function of immutable evidence and a knowledge cutoff**: `world(as_of=T)`. It is computed on demand, never stored and mutated. This one decision is what makes Historical Simulation and Production Trading *the same system with a different evidence source* (§9, §10), and it is the through-line of the whole model.

---

## 1. Purpose — why an autonomous system needs a formal knowledge model

An autonomous capital system acts on beliefs about a world it cannot see directly. Without a formal knowledge model, the gap between reality and belief is handled implicitly — in whatever shape the code happened to emit — and implicit handling of that gap is exactly where look-ahead bias, silent staleness, and unfalsifiable results come from.

The proposed chain is correct and worth formalizing, with one addition (marked):

```
Reality
  ↓ (only partially observable; most is never seen)
Observable Reality
  ↓ (only some is collected; collection lags and fails)
Collected Evidence            ← immutable once collected (§5)
  ↓ (cleaned, deduplicated, timezone-normalized; lossy)
Normalized Knowledge
  ↓ (a narrow, as-of-bounded projection is selected)
Decision Inputs               ← world(as_of=T), typed & minimal (§0.2, §11)
  ↓
Decision                      ← recorded immutably with its inputs (§ Decision Records)
  ↓ (time must pass; not knowable at decision time)
Outcome
  ↓ (evaluated against the decision, never fed back into it — §14)
Learned Knowledge             ← a new, replaceable belief layer, versioned
```

These are not the same thing, and the discipline is to never let a lower layer masquerade as a higher one. *Learned Knowledge* is not *Reality*. *Collected Evidence* is not *Observable Reality* (we miss things). *Normalized Knowledge* is not *Collected Evidence* (normalization is opinion). *Decision Inputs* are not *Normalized Knowledge* (they are a bounded slice, and the boundary is load-bearing). Collapsing any arrow is a constitutional error.

---

## 2. World State — the world OmniTrade exists inside

`[CHALLENGE]` The proposal lists ~16 domains as one flat `WorldState`. Flattening them is a category error that produces a coupled god-object — the opposite of the four-engine modularity Article VI protects. Three corrections:

**2.1 — Three tiers, not one bag.** The domains divide by *kind of knowing*:

- **ObservedWorld** — things that exist independently of OmniTrade, which it can only *observe*, always lagged, revisable, and uncertain. Point-in-time bounded (§7). *Market, Media, Macroeconomic, Corporate, Blockchain, Venue-capability, Reference/Identity-of-instruments, Calendar.*
- **SelfState** — OmniTrade's own state, which it *authoritatively knows* because it *is* that state — not observed, not lagged in the same way. *Portfolio, Execution (its own orders/fills), Campaign/Mandate, System Health, its own Identity.*
- **AuthorityState** — the frame OmniTrade is *permitted to act within*. *Operator Authorization, Risk policy/limits, Run context (mode, knowledge_cutoff, simulation binding).*

Knowing "the global kill switch is engaged" (SelfState/Authority) is a fundamentally different act than believing "BTC is trading at X" (ObservedWorld, a time-lagged observation). `[VERIFIED]` the current risk contract already respects this instinctively — it passes kill-switch/equity/cooldown as authoritative context and passes `data_is_stale`/`data_has_gaps` as *quality metadata about observations*, i.e., it already treats observed-world knowledge as uncertain and self/authority knowledge as certain.

**2.2 — "Historical Context" and "Simulation State" are the lens, not the world.** `[CHALLENGE]` They do not belong *inside* the world being observed; they are the *frame through which ObservedWorld is computed* — the `as_of=T` pointer plus the immutable evidence bundle (§9). Placing them inside WorldState is a level confusion that invites a run to observe its own simulation metadata as if it were market fact.

**2.3 — WorldState is conceptual, not a monolithic runtime struct.** `[RECOMMENDATION]` No subsystem ever receives "the whole world." Each receives a **narrow, typed, immutable projection** of exactly the tier and domains its purpose requires (§11, §12), all derived from the same `world(as_of=T)` function. The unifying thing is the *discipline* (as-of-bounded, typed, uncertainty-carrying, minimal-surface), not a shared mega-object.

Domain sketch (each is observed-or-owned, and each carries its own availability + confidence semantics):

| Tier | Domain | What it holds |
|---|---|---|
| Observed | Market | prices, candles, order book, trades — as-of-bounded |
| Observed | Media | news/social as bitemporal, revisable facts (`knowledge_available_at`) |
| Observed | Macro | releases/revisions, strictly bitemporal |
| Observed | Corporate | earnings, corporate actions |
| Observed | Blockchain | on-chain/protocol events |
| Observed | Venue-capability | historical venue availability, fees, limits |
| Observed | Reference/Identity | canonical asset identity over time (forks/renames/delistings) |
| Observed | Calendar | sessions, holidays, halts |
| Self | Portfolio | cash, positions, cost basis, exposure |
| Self | Execution | OmniTrade's own orders, fills, reconciliation |
| Self | Campaign/Mandate | active mandates, authorizations, campaign identity |
| Self | System Health | ingestion freshness, worker liveness, data gaps |
| Authority | Operator Authorization | human approvals, commissioning state |
| Authority | Risk posture/limits | policy, kill switches, drawdown/loss limits |
| Authority | Run context | mode, knowledge_cutoff, dataset bundle, seed |

---

## 3. Knowledge State — five things that must never merge

- **Things that exist** — reality. OmniTrade *never* has this. It is the unreachable ground truth.
- **Things OmniTrade knows** — observed facts with provenance and an availability time ≤ now/cutoff. Certain only as *observations*, not as reality.
- **Things OmniTrade believes** — derived or probabilistic inferences (regime labels, confidence scores). Replaceable.
- **Things OmniTrade predicts** — forward-looking estimates. Expire; must never be stored as facts.
- **Things OmniTrade cannot yet know** — explicit `UNKNOWN`, including the future and un-ingested present.

`[VERIFIED]` the platform already has the instinct: `replay_context.py` uses an explicit `UNKNOWN` sentinel and an `evidence_completeness` field, and `DecisionRecord` separates real-time `explanation` from later `ai_reflection`. This document elevates that instinct to law: these five are different *types*, and a value's type may never be silently upgraded (a prediction becoming a "fact," an unknown becoming a default). **Truth is never inferred; it is only observed or left unknown.**

---

## 4. Knowledge Timeline — the lifecycle of a fact

Formalized, with the critical boundary marked:

```
Reality: event occurs                        (event_time)
  ↓
Information is published                      (published_at)
  ↓
Information becomes available to OmniTrade    (knowledge_available_at)  ← the visibility boundary
  ↓
Evidence collected / dataset updated          (observation_time / ingestion_time)
  ↓
Gateway exposes it iff availability ≤ cutoff  (ADR-0011, fail-closed)
  ↓
world(as_of=T) reflects it
  ↓
A subsystem observes its typed projection
  ↓
Decision occurs                               (decision_time)
  ↓
Execution / settlement                        (execution_time / settlement_time)
  ↓
Outcome occurs                                (only after time passes)
  ↓
Learning occurs                               (learning_time)  ← never flows backward (§14)
```

`[CHALLENGE]` The proposal's timeline lists "Information published → available" as adjacent steps; the gap between them is not incidental, it is *the* thing that separates honest history from hindsight. A fact must become visible at `knowledge_available_at`, which is often strictly later than `published_at` (latency, paywalls, ingestion lag). `[VERIFIED]` today's `Candle` model carries `close_time` (a sound availability basis for a completed candle) but not a general `available_at`; revisable domains (media, macro) require the explicit axis (§7).

---

## 5. Evidence Model — every fact is typed, timed, and graded

Evidence, once collected, is **immutable** (this is the substrate the Immutable Historical Datasets doc and ADR-0010 rest on). Types, ordered by authority (mirroring the platform's decided evidence classes and the HIP hierarchy):

- **Live production observations** — `PRODUCTION_LIVE`. Highest confidence.
- **Forward-paper observations** — `FORWARD_PAPER`.
- **Point-in-time historical** — `HISTORICAL_POINT_IN_TIME`.
- **Historical media** — bitemporal, revisable, deduplicated (§ Historical Media).
- **Provider responses / order execution / portfolio values** — SelfState evidence: authoritative about OmniTrade's own actions, still subject to reconciliation.
- **Derived features** — computed only from point-in-time inputs; carry observation cutoff + source version.
- **Generated hypotheses** — AI-produced *beliefs*, never facts (§13).
- **Counterfactual / simulation / synthetic** — `COUNTERFACTUAL` / generative; useful, never authoritative; never presented as real history.
- **Unit-test evidence** — `UNIT_TEST`; never mixed with any real class.
- **Unknown** — an explicit, first-class value.

`[RECOMMENDATION]` Confidence is not a single number. Every fact carries at least: an **evidence_class** (its authority tier), an **availability basis** (why it was knowable at its stamped time), a **completeness/quality** grade (stale? gapped? partial?), and where probabilistic, a **calibrated confidence**. `[VERIFIED]` the risk path already consumes quality metadata (`data_is_stale`, `data_has_gaps`) — confidence is thus not decoration; it is an admissibility criterion a subsystem may act on.

---

## 6. Truth vs Belief — how each lives in the world

| Kind | Nature | Lifecycle | May influence a decision? |
|---|---|---|---|
| Observed fact | knowable at ≤ cutoff, with provenance | immutable | yes, as fact |
| Derived fact | deterministic function of observed facts | immutable given inputs | yes, as fact (if fully point-in-time) |
| Probabilistic belief | inference with calibrated confidence | replaceable | yes, *as a belief* (typed) |
| Prediction | forward estimate | expires; invalid after its horizon | yes, but never stored as fact |
| Hypothesis | unproven proposal (often AI-generated) | promoted or discarded via the gate | only through the promotion gate |
| Unknown | explicit absence | resolved or remains unknown | yes — a subsystem may act *on the unknown itself* (e.g., refuse) |
| Contradictory evidence | conflicting sources | preserved, not silently resolved | surfaced with its conflict, never averaged away |

`[CHALLENGE]` Contradiction must be **represented, not resolved by fiat**. Silently picking a winner (or averaging) destroys the information that the sources disagreed — which is often the most decision-relevant fact present. The world model stores the conflict and its provenance; resolution, if any, is an explicit, recorded belief with its own confidence.

---

## 7. Time — eight clocks, never one

The eight temporal coordinates the proposal lists are correct and each must be a distinct field, never coalesced:

`event_time` · `observation_time` · `knowledge_available_time` · `simulation_time` · `decision_time` · `execution_time` · `settlement_time` · `learning_time`.

Why every subsystem must respect them: the entire integrity of point-in-time correctness is the relationship *visibility uses `knowledge_available_time`, never `event_time`*. `[VERIFIED]` the current production path reads wall-clock `datetime.now()` pervasively in `autonomous_cycle/orchestrator.py` and loads candles with no as-of boundary — safe live, unsafe if reused for simulation — which is exactly why ADR-0008 (clock adapter) and ADR-0011 (fail-closed visibility) exist. `[RECOMMENDATION]` Constitutionally: **in any non-production mode, the only source of "now" is the simulation clock, and the only gate on visibility is `knowledge_available_time ≤ cutoff`.** `settlement_time` and `execution_time` deserve their own axes because a decision's cost and its outcome resolve on different clocks than the decision — collapsing them into `decision_time` is a subtle but real leakage of settlement knowledge into decision evaluation.

---

## 8. Uncertainty — and how strategies should consume it

Uncertainty is represented, per fact, as: **confidence/probability** (calibrated), **missing-data markers** (explicit, never a default value), **conflict markers** (§6), **partiality** (this is a partial observation), and **latency/delay** (this fact is stale by Δ, or was reported late).

`[CHALLENGE]` The proposal asks how strategies should *consume* uncertainty; the honest architectural answer is that most strategies today **cannot**, and pretending otherwise is dangerous. `[VERIFIED]` the current `Signal` carries a `strength`/`confidence`, and strategies receive frozen candles — they consume *point estimates*, not distributions. The correct evolution is not to force every strategy to become Bayesian, but to make uncertainty **admissible and refusable**: a subsystem may (a) ignore it (legacy strategies, explicitly), (b) act on it as a gate (refuse when stale/gapped — which `[VERIFIED]` the risk engine already does), or (c) consume it richly (future strategies). The world model *always carries* the uncertainty; whether a consumer uses it is the consumer's declared capability, never a silent assumption. **Missing is never zero. Stale is never fresh. Unknown is never a default.**

---

## 9. Historical Simulation and WorldState

Historical Simulation reconstructs `world(as_of=T)` and advances `T` chronologically. `[CHALLENGE]` It does **not** "replay prices." It reconstructs the *observable world* at each simulated moment — market *and* the media, macro, venue-capability, and reference facts that were genuinely knowable by `T` — and lets the unchanged decision logic observe its narrow projection of that world. This is precisely why §0.2 matters: because WorldState is `world(as_of=T)` computed from immutable evidence, simulation gets point-in-time reconstruction *for free* from the same function production uses, with no forked "backtest world."

`[DECIDED]` The evidence is bound by immutable dataset version + bundle hash + `knowledge_cutoff` (ADR-0010/0011, ADR-0012). The simulation clock is the sole `now`. Visibility fails closed.

---

## 10. Production Trading and WorldState

Production computes the *same* `world(as_of=now)`; the only differences are the **evidence source** (live providers/ingestion instead of an immutable dataset) and that `now` is the wall clock rather than the simulation clock. `[CHALLENGE]` This equivalence is the payoff of the whole model and the reason to reject a stored WorldState: **production and simulation are one system observing one conceptual world through two evidence adapters.** Anything true of the decision logic in simulation is true in production, because it never knew which world it was in — it only ever received `world(as_of=T)`. `[VERIFIED]` the decision logic is already source-agnostic and pure (`evaluate_signal_risk`, `generate_signal`), which is what makes this achievable rather than aspirational.

---

## 11. Strategy Interface — candles or WorldState?

`[VERIFIED]` Today strategies consume `StrategyContext` = frozen `candles` + `asset_metadata` + `interval` + `current_position` + `strategy_parameters`; `Strategy.generate_signal(context) -> Signal`.

`[CHALLENGE]` **Neither raw candles nor the full WorldState. The right answer is a scoped, typed `ObservationView(as_of=T)`.**

- *Against raw candles:* they are too poor for the platform's ambitions (media, macro, cross-asset), and they hide the availability boundary inside the caller.
- *Against handing strategies the whole WorldState:* it maximizes the leakage surface (a strategy could reach a domain it shouldn't and pull a future-tainted field), couples every strategy to a god-object (violating Article VI modularity), and makes strategies untestable in isolation.
- *The synthesis:* strategies receive an **immutable, as-of-bounded, uncertainty-carrying projection scoped to exactly what that strategy is authorized to observe.** Candles are one facet; a media-aware strategy gets a media facet; none gets more than its declared need. `[VERIFIED]` the current `StrategyContext` is already frozen (`MappingProxyType`) — that immutability precedent carries forward; the change is *breadth-with-boundaries*, not mutability.

So: evolve **beyond candles**, but **not to the monolith** — to a minimal, typed observation view. Least-observation is both a leakage control and a coupling control.

---

## 12. Risk Engine — what it may see

`[VERIFIED]` `evaluate_signal_risk` is pure, takes `RiskEvaluationRequest` + `RiskEvaluationContext`, with `evaluation_time` explicit.

- **From ObservedWorld:** only *observation-quality* signals it must gate on — staleness, gaps, venue availability. `[VERIFIED]` it already consumes `data_is_stale`/`data_has_gaps`. Risk should generally *not* re-derive market beliefs; it governs whether to act, given the proposal and the world's observability.
- **In `RiskEvaluationContext` (SelfState + Authority):** account equity, drawdown, positions, kill-switch state, cooldown, limits, campaign authorization. `[VERIFIED]` these are already passed explicitly — the correct design.
- **Never visible to Risk:** the **future** and the **outcome**. Risk must never see forward returns, the realized PnL of the trade it is evaluating, or any Learned Knowledge derived from outcomes — that is leakage and circularity (§14). `[CHALLENGE]` Risk should also never see *predictions dressed as facts*: a belief must arrive tagged as a belief so Risk can weight it as one. And Risk evaluates the *decision*, not the *outcome that would follow* — outcome-blindness is a permanent property, not a phase.

---

## 13. AI Research — observe, generate, never mutate

`[VERIFIED]` No LLM is on the decision path today; the research LLM adapter is `PLANNED`/`NotImplementedError`. The governance model must keep it that way by construction:

- AI **observes** immutable evidence and typed world projections.
- AI **generates** hypotheses, critiques, and annotations as **new, separately-classed evidence** (`beliefs`/`hypotheses`), never facts.
- AI **annotates** without altering — `[VERIFIED]` `DecisionRecord.ai_reflection` is already a separate, nullable, hindsight-tagged field distinct from the real-time `explanation`; that pattern is the law: **AI adds typed knowledge alongside a record; it never edits reality, observed evidence, or a prior decision.**
- AI **never directly mutates** WorldState, evidence, or production truth. Its outputs reach production *only* through the promotion gate (§14, §18), under human authorization.

Governance in one line: **AI may propose; only the governed gate, with a human, may promote.**

---

## 14. Learning — without contaminating production truth

```
Reality → Decision → Outcome → Evidence → Knowledge → Improved Strategy
```

Two invariants make this safe:

1. **Learning never flows backward.** `[CHALLENGE]` A past decision's recorded inputs are never re-labeled with what was later learned — that is hindsight leakage and it silently corrupts the historical record. `[VERIFIED]` the platform already embodies this: counterfactual/quality results are *downstream, immutable, separate* tables, and Decision Snapshots are immutable — the past is enriched with *linked* new records, never edited.
2. **Learned Knowledge is a replaceable belief layer, not truth.** It is versioned, and it earns influence over capital only by surviving the promotion gate (train → validate → **untouched test** → forward-paper → bounded live). The immutable-datasets discipline is what makes "untouched" *falsifiable* rather than merely promised.

Production truth stays clean because learning produces *new versioned artifacts* (beliefs, candidate strategies), never mutations of evidence or of the observed world.

---

## 15. Constitutional Principles

Adopting the proposed principles and extending them:

1. Reality is never modified.
2. **The world is observed, never possessed** — OmniTrade holds evidence, never reality.
3. **WorldState is a query, not a store** — `world(as_of=T)`, computed from immutable evidence, never materialized-and-mutated.
4. Knowledge is versioned. Evidence is immutable. Beliefs are replaceable. Predictions expire. Unknown remains unknown.
5. Truth is never inferred — only observed or left unknown.
6. Historical knowledge is point-in-time bounded; the world is reconstructed, never rewritten.
7. **Every fact carries its time and its confidence; a fact without provenance is inadmissible.**
8. **Self-knowledge and world-observation are different kinds of knowing and are never conflated.**
9. **Absence of evidence is represented explicitly — missing is never zero, stale is never fresh.**
10. **No subsystem may observe more of the world than its purpose requires** (least-observation).
11. **An outcome is never an input to the decision that produced it** (anti-circularity).
12. Contradiction is preserved and surfaced, never silently resolved.
13. In any non-production mode, the simulation clock is the only `now`, and `knowledge_available_time ≤ cutoff` is the only gate on visibility.

---

## 16. Relationship to existing architecture

This document is the **semantic/epistemic layer**: it defines *what exists and what can be known*. It slots cleanly without modifying anything:

- **`PROJECT_CONSTITUTION.md`** — defines *what OmniTrade values*; this document defines *what OmniTrade can know*. The Constitution remains supreme; this sits directly beneath it and above all mechanism.
- **`SYSTEM_ARCHITECTURE.md`** — *how it is built*; it instantiates `world(as_of=T)` as concrete services and adapters.
- **Historical Intelligence Platform / Historical Media Intelligence** — mechanisms for reconstructing ObservedWorld across time and sources.
- **Immutable Historical Datasets / future ADR-0012** — the immutable *evidence substrate* that `world(as_of=T)` reads.
- **ADR-0010** — evidence classification and provenance = the *typing* of §5.
- **ADR-0011** — the fail-closed *visibility rule* that computes ObservedWorld at a cutoff.
- **ADR-0008/0009** — the mode/adapter and terminology decisions that make one world observable through two evidence sources (§10).
- **Decision Records / Snapshots** — the immutable *record of knowledge-at-decision-time* (§3, §14).
- **Risk Engine** — a consumer of SelfState + Authority + observation-quality, blind to future and outcome (§12).
- **Strategy Engine** — a consumer of a scoped `ObservationView(as_of=T)` (§11).
- **Execution Providers** — an *evidence source and a SelfState mutator of OmniTrade's own orders*, never a mutator of the observed world.

Per the operator's constraint, none of these documents is modified; they fit *underneath* this one.

---

## 17. Architectural Critique (of this very proposal)

The brief asked for criticism, not agreement. The strongest objections, and where they land:

1. **A monolithic WorldState is an anti-pattern** — addressed by making it a query, not a store (§0.2), and by refusing to hand any subsystem the whole thing (§2.3, §11). If this correction is rejected, the rest of the model becomes a liability: a god-object and a leakage magnet.
2. **The 16-domain list conflates categories** — observations of an external world vs. authoritative self-state vs. governance/authority. Merged, they hide that "knowing my kill switch is on" and "believing a price" are different kinds of knowing (§2.1). Split into ObservedWorld / SelfState / AuthorityState.
3. **"Historical Context" and "Simulation State" are not world domains** — they are the lens/frame (§2.2). Keeping them inside the world invites a run to observe its own metadata as fact.
4. **Over-formalization risk.** `[CHALLENGE]` A knowledge model this rich can become ceremony that slows the First Autonomous Profit lane and over-engineers strategies that only need candles. Mitigation: the model is **constitutional and typed, not a mandatory runtime object** — legacy candle-only strategies remain valid (§8, §11); richness is opt-in. The model constrains *what is allowed to be believed*, not *how much machinery each consumer must carry*.
5. **"Confidence" is under-specified as a scalar** — replaced by a small structured set (class, availability basis, completeness, calibrated probability) in §5, because a single number cannot distinguish "stale" from "conflicted" from "low-probability," which are different decisions.
6. **Reproducibility vs. richness tension.** The more of the world a decision observes (media, macro), the larger the surface that must be made point-in-time-correct and immutable. This is not free; each new domain must earn its way in by proving it can be made bitemporal and fail-closed, or it enters only as an explicitly lower evidence class. The model should *resist* breadth until integrity is proven — consistent with the platform's "evidence before features" doctrine.

Net: WorldState is the right idea *if and only if* it is a query over immutable, typed, as-of-bounded evidence, tiered into world/self/authority, and consumed through minimal scoped projections. As a stored, flat, mutable mega-object it would do more harm than good.

---

## 18. Future Vision

The knowledge model is what lets each rung of the ladder bear real weight:

- **Automated Trading** acts on `world(as_of=now)` with typed, provenance-bearing inputs — no silent staleness, no default-value leakage.
- **Historical Simulation** reconstructs `world(as_of=T)` from immutable evidence — the same decision logic, honestly blind to the future.
- **Historical Intelligence** accumulates because results across years are comparable — they reference retrievable, versioned worlds, not a history that shifted underneath them.
- **Autonomous Research** is trustworthy because AI can only *propose typed beliefs* over immutable evidence, and "untouched test" is *falsifiable* — the precondition for letting research influence capital.
- **Evidence-Based Capital Allocation** is defensible because "why did we allocate?" is answered by re-executing `world(as_of=T)` and the recorded decision — the operational form of Article X: a maintainer who was not present re-derives exactly what the system saw and why.

The single discipline that carries all the way up: **the system never confuses what it believes with what is true, and never lets what it later learned change what it once saw.**

---

## 19. Is another constitutional document still missing?

`[RECOMMENDATION]` **Yes — one.** This document governs *knowing*. It deliberately does not govern *authority to act on knowledge*. Scattered across the HIP (promotion gate), the mandate/campaign layer, and Operator Authorization is an implicit answer to *"how does a belief earn the right to move real capital, and how is human accountability preserved as autonomy increases?"* — but there is no single constitutional document that owns it.

The missing document is an **`AUTHORITY_AND_ACCOUNTABILITY_MODEL.md`** (equivalently, *Governed Autonomy*): the epistemic counterpart to this one. Where this document answers *what can be known*, that one answers *who or what may act on it, under what authorization, with what human accountability, and how authority is granted, bounded, escalated, and revoked* across the human/AI boundary as the platform climbs from suggestion to autonomous allocation. It would own the promotion gate as a constitutional object, the human-in-the-loop guarantees (Article X), the authority ladder, and the revocation/kill semantics as *governance*, not merely mechanism. Given the family-legacy, multi-generational, real-capital framing, the gap between "the system knows X" and "the system is permitted to act on X" is exactly where a stewardship failure would occur — and it currently has no constitutional home.

A secondary, narrower candidate is a constitutional **Identity & Naming** model (canonical identity of assets, campaigns, providers, and instruments *across time* — forks, renames, delistings, migrations). It is partly implied by the Reference domain (§2) and the Asset Registry, but its cross-temporal identity guarantees are load-bearing enough to deserve their own treatment. It is, however, closer to specification than constitution, so I rank the Authority & Accountability model as the more urgent missing piece.

---

*This document is architecture and governance only. No code was written, the repository was not modified, and no deployment is proposed. It is submitted for adoption as permanent constitutional architecture, sitting directly beneath `PROJECT_CONSTITUTION.md`, with the recommendation that `AUTHORITY_AND_ACCOUNTABILITY_MODEL.md` be identified as the next and possibly final missing constitutional document.*
