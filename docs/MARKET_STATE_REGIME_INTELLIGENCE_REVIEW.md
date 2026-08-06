# Architectural Review — Market State and Regime Intelligence Architecture

**Reviewer role:** Principal Quantitative Systems Architect (pre-implementation design review)
**Document under review:** `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` v1.0
**Review date:** 2026-08-06
**Verdict:** **B — Approve with revisions**, where the required revisions are primarily about *authority and timing*, not content.

**Evidence convention (per project review standard):**
- `[DOC]` — fact verified against the reviewed document or an existing governing document in the repo.
- `[CONST]` — grounded in `PROJECT_CONSTITUTION.md`.
- `[STATE]` — grounded in `00_PROJECT_STATE.md` / `06_NEXT_SESSION.md` / `02_DECISIONS.md`.
- `[JUDGMENT]` — my quantitative/architectural inference, not a repo fact.

---

## Executive Summary

This is, on its own terms, one of the more disciplined regime-intelligence design documents I have reviewed. `[JUDGMENT]` It is quantitatively literate, it names its own failure modes honestly (§26), and it is architecturally careful: it preserves Risk Engine final authority, refuses to become a fifth engine, keeps sizing downward-only, and defaults to filter-mode rather than standalone trade generation. If the only question were "is the craft good?", the answer is yes.

But that is not the question a pre-implementation review exists to answer. The question is *should this be built, in this form, now* — and here the document has one dominant problem that outweighs every technical detail inside it:

**The most dangerous assumption in this document is not in any section. It is the header.** `[DOC]` The file declares `Authority: Tier-1 Architecture Specification` and `Status: Architecture and Implementation Planning`, and closes with an `Immediate Implementation Plan` (§23). That framing is in direct tension with the project's own governing law and its current state:

- `[STATE]` OmniTrade has **not yet achieved its foundational milestone** — First Autonomous Profit, a single autonomous BUY→manage→SELL→reconcile→positive-net round-trip without operator intervention. It is currently blocked on a mundane authority-propagation defect in Controlled Proof Exit Recovery. The system has never once closed the loop it was built to close.
- `[CONST]` Article VII (Evidence): *"The burden of proof belongs to the new idea, not to the existing, working system it proposes to change."* This document is the output of a research/video session on HMMs — precisely the "exciting/fashionable" category Article VII is written to defend against. It has not discharged that burden, so it has not earned Tier-1 authority.
- `[STATE]` The project's own decisions of record — *Production Before Expansion*, *Runtime Evidence Before Expansion*, *Small Bounded Engineering Tasks*, and the standing rule "*Production evidence is more valuable than speculative architecture*" — all point the same direction.

This is worth naming plainly because it is the pattern the project was built to resist. `[JUDGMENT]` A 1,539-line, nine-phase, eleven-specialist-model subsystem, arriving as a Tier-1 spec with an immediate implementation plan, while the core loop is unfinished, is the *rigorous-looking* form of scope creep. Rigor applied to premature scope is still premature scope. The document even lists "Complexity Without Value" as risk 26.7 — it is, at the level of its own authority tier, an instance of the risk it names.

**None of that means the thinking should be discarded.** `[STATE]` The project already has a precedent for exactly the right disposition: the *Parallel Authorized Lanes* decision permits expansion-foundation work to proceed alongside the live milestone **when it is structurally isolated and carries no risk to the milestone.** Reclassified as a *research/vision* artifact rather than a governing spec, and with only its Phase 0 (repository audit) authorized, this document fits that lane cleanly. That is the revision.

Approve the *thinking* into a research lane. Reject the *authority header* and the *immediate implementation plan*. Gate everything past the repo audit behind (a) First Autonomous Profit or an isolation guarantee, and (b) evidence that discharges Article VII.

---

## Strengths

The strongest decisions here are real and above typical retail-quant hygiene. `[JUDGMENT]` unless tagged otherwise.

1. **Neutral HMM state IDs until empirically characterized (§4.4, §9.3, §9.5).** Refusing to attach names like `accumulation` before examining emission profiles is the single best discipline in the document. It directly defeats the narrative fallacy that kills most regime projects.
2. **Separated confidence types (§13.1).** Distinguishing state confidence, regime posterior, transition uncertainty, data-quality, agreement, and calibrated decision confidence — rather than one blended percentage — is genuinely sophisticated. Most systems conflate all six.
3. **Calibration treated as first-class (§13.2).** The reliability-diagram framing ("0.80 confidence that wins 0.55 is miscalibrated") is correct and rarely implemented outside institutions.
4. **Walk-forward with purge/embargo and no-lookahead clock (§15).** This is López de Prado–grade leakage awareness. The overlapping-window / effective-independent-sample concern (§8.4) is a level of rigor most practitioners never reach.
5. **Mandatory baselines and ablation (§16).** Requiring every complex model to beat "strategy with no filter" and a deterministic baseline is the correct scientific spine, and it sets the system up to discover its own null results — which is a feature.
6. **Downward-only, bounded [0,1] sizing multipliers (§14).** Preserves Risk authority structurally; the subsystem can only ever de-risk. `[DOC]` Consistent with `RISK_ENGINE.md` and Constitution Article VIII.
7. **"Agreement is not a vote count" (§11.2) and "conflict is valuable evidence" (§11.3).** Correctly refuses to treat five correlated price-derived indicators as five confirmations, and refuses to average disagreement away.
8. **Explicit acknowledgment of regime *delay* (§26.9) and suppression-cost via counterfactuals (§26.10).** Regime lag is *the* structural weakness of regime trading, and the doc names it and ties measurement to the existing COL.
9. **Fail-closed throughout (§19).** Stale/missing/conflicting evidence yields `state_unknown`/WAIT, not fabricated certainty. `[CONST]` Aligns with the fail-closed posture across the constitution.

---

## Weaknesses

Brutally, in descending order of importance.

1. **Authority-tier and timing (see Executive Summary).** Highest-impact weakness. `[DOC]`/`[CONST]`/`[STATE]` The Tier-1 header and §23 implementation plan are unearned at this project stage and violate Article VII in spirit.

2. **The compatibility matrix's resolution vastly exceeds the information content of the data (§12.2).** `[JUDGMENT]` This is the most important *technical* weakness. Compatibility is to be learned per {strategy × parameter-set × asset × timeframe × venue × state/regime × holding-horizon}. Multiply it out and you get hundreds to thousands of cells. Each cell needs enough *independent* walk-forward episodes to estimate expectancy. But regimes are persistent and switch rarely, so a year of BTC contains only a handful of *independent* regime episodes. Most cells will be structurally data-starved — the exact "false precision on small samples" the doc warns about in §26.3, except here the *table design itself* manufactures the problem. The §18.1 example proudly cites "walk-forward sample count: 286," but 286 overlapping-window observations across a few regime episodes may represent 5–10 truly independent experiences. **The design invites confident numbers backed by almost no independent evidence.**

3. **Confidence calibration needs volume the system will not have for years (§13.2).** `[JUDGMENT]` Calibrating by {bin × asset × strategy × regime × timeframe × horizon × model-version} is the same combinatorial explosion. Reliable calibration curves need hundreds of outcomes *per bin per stratum*. `[STATE]` A system that has not completed its first autonomous trade has zero live calibration data. This stratification is aspirational presented as near-term.

4. **The HMM is the component least likely to earn its keep, yet it anchors the document.** `[JUDGMENT]` State-count selection for HMMs on financial data is notoriously weak: held-out likelihood rises almost monotonically with states, information criteria penalize too gently, and cross-fold "stability" is itself unstable. Realistically this converges on 2–3 states — which a deterministic ADX+volatility classifier already delivers deterministically and explainably. The doc's own ablation question (§16.3, "did the HMM improve on deterministic state?") is exactly right, and `[JUDGMENT]` the honest prior on the answer is "frequently no, especially after costs." The architecture is admirably built to discover that null — but the framing (a whole "HMM Research Architecture," §9) over-weights the component most likely to be cut.

5. **No defense against multiple-testing / data-snooping across the configuration space (§26.4 names it, the architecture doesn't control it).** `[JUDGMENT]` With this many models, features, thresholds, timeframes, and state counts, walk-forward alone does **not** protect you — you will walk-forward-overfit simply by trying enough configurations and keeping what validated. The missing controls are (a) pre-registration of hypotheses, (b) a *deflated* performance criterion (e.g., deflated Sharpe) that accounts for the number of trials, and (c) a hold-out era that is **never touched** during research. This is a methodology gap, not a metric gap.

6. **Secular base-rate contamination (unaddressed).** `[JUDGMENT]` BTC has a strong upward drift across most historical windows. A regime model trained on such data "learns" that most regimes have positive forward return, and any long-biased strategy will appear compatible with most regimes — not because regime detection works, but because the base rate was up-and-to-the-right. §16.4 lists buy-and-hold as a baseline (good), but the *compatibility layer* (§12) does not demand that regime-conditional performance be measured as **excess over the same-window buy-and-hold base rate**. Without that, regime value is systematically overstated.

7. **Phase B/C evidence is largely non-actionable for a spot-only venue (§5.2–5.3).** `[JUDGMENT]`/`[DOC]` Funding rates, basis, open interest, and liquidations are decision-relevant when you can trade the instruments that express them. As pure directional *context* for spot BTC on Kraken, most of them are weak signal and add real ingestion fragility (the §26.8 operational risk). The phasing defers them (good), but the menu should be pruned to "evidence that can actually change a spot BUY/SELL/WAIT," not "everything a derivatives desk watches."

8. **Duplication with already-running capability is acknowledged but not resolved (§28).** `[STATE]` Production runtime already proves "regime-aware strategy weighting is production-validated," and `[DOC]` `STRATEGY_ENGINE.md` already has a deterministic trend-regime filter while `AI_LAYER.md` already has a regime classifier, confidence scorer, and allocator. The document notes this in §28 but then proceeds to specify a fresh `services/market_state/` tree (§20). `[CONST]`/`[STATE]` This collides with the standing rule "Do not create duplicate documentation / duplicate services." The document should be reframed as *consolidate and evolve the regime capability we already run in production*, not *insert a new market-understanding layer beside it*.

---

## Dangerous Assumptions

1. **"Now is the time to design this."** The most consequential. `[STATE]` The evidence that would tell you *which* regime distinctions matter is generated by running the core loop live — which hasn't happened yet. Designing the regime taxonomy in detail before First Autonomous Profit risks encoding guesses as architecture.

2. **"A finer conditioning grid produces finer knowledge."** `[JUDGMENT]` It produces finer *cells*, each with less evidence. Beyond a coarse grid, resolution buys noise, not insight.

3. **"An HMM will tell us when it doesn't know."** `[JUDGMENT]` It won't, by default. A fitted HMM assigns *some* state with *some* posterior even to market conditions unlike anything in training. Absent an explicit novelty/out-of-distribution guard, the fail-closed design has a blind spot precisely where it matters most (novel regimes).

4. **"Walk-forward validation defeats overfitting."** `[JUDGMENT]` It defeats *lookahead*. It does not defeat *selection across many trials*. These are different failure modes and the document conflates their remedies.

5. **"More evidence families ⇒ better decisions."** The doc mostly resists this (§5.4, research Q13/Q14), but the sheer size of the Phase B/C menu implies an accretion bias that Article VII and §26.7 both warn against.

6. **"Transition probabilities are decision-relevant."** `[JUDGMENT]` For a trend/mean-reversion gate, the only transition quantity that usually matters is the diagonal — persistence, p_ii. The full off-diagonal matrix is elegant and largely low-ROI relative to its estimation cost.

---

## Missing Components

Only additions that genuinely improve the architecture — and the honest headline is that the main thing "missing" is *evidence, not architecture.* With that said:

1. **An out-of-distribution / novelty detector as a first-class fail-closed output.** `[JUDGMENT]` Implementable within the existing HMM: a low sequence likelihood (forward/Viterbi) under all trained models signals "this market resembles nothing I was trained on → emit `regime_unavailable`." This closes the §26 blind spot and fits the fail-closed philosophy exactly. High value, low cost.

2. **Transaction cost as a hard *promotion gate*, not a reporting column.** `[JUDGMENT]` §16.4 lists fee drag and slippage as metrics. Given thin edges, "must beat the baseline *after* realistic costs" should be a binary promotion criterion in §17.3, not something you notice afterward.

3. **A pre-registered, never-touched hold-out era + a trial-count-aware (deflated) performance criterion.** `[JUDGMENT]` The only real defense against the data-snooping surface the design creates (Weakness 5).

4. **Excess-over-base-rate framing in the compatibility layer.** `[JUDGMENT]` Regime-conditional performance measured against same-window buy-and-hold, to neutralize BTC's secular drift (Weakness 6).

**Explicitly recommend AGAINST adding now:** reinforcement learning, causal inference, probabilistic graphical models beyond the HMM, portfolio optimization, neural regime models. `[JUDGMENT]` Every one of these deepens the exact hole. They are complexity magnets, and none of them is the current bottleneck. The current bottleneck is a live authority-propagation bug and an unfinished round-trip.

---

## Overengineering

`[JUDGMENT]` throughout.

1. **The eleven-specialist-model ensemble (§11.1)** — aspirational to the point of fiction for the current stage. Two evidence families (price/vol + one microstructure or cross-asset source) would already exceed what the data can independently support.
2. **The full transition matrix (§8.2)** vs. the persistence diagonal that carries the usable signal.
3. **The HMM research architecture (§9)** relative to the deterministic baseline it is unlikely to beat after costs.
4. **The compatibility matrix dimensionality (§12.2).**
5. **The ten-state promotion lifecycle (§17.1)** — `defined → research_only → walk_forward_validated → paper_shadow → paper_advisory → paper_filter_active → live_shadow → live_advisory → live_filter_candidate → retired`. Ten governance states for a capability that does not exist yet is governance-for-its-own-sake. Three (`research`, `shadow`, `candidate`) would suffice until reality demands more.
6. **The Phase B/C evidence menu (§5.2–5.3)** for a spot-only venue.

---

## Underengineering

The document is paradoxically vaguest exactly where the hard, overfittable choices live.

1. **The multi-timeframe synthesizer (§10.3–10.4).** It correctly says "more agreement isn't automatically better" and then hides the entire method behind `alignment_score`, `conflict_score`, and `policy_version`. *How* you collapse a 1d/4h/1h state stack into a scalar is where silent degrees of freedom (i.e., overfitting surface) accumulate. This needs a concrete, testable specification, not a policy pointer.
2. **The evidence-agreement computation (§11.2).** "Account for family diversity, feature overlap, model correlation" names the inputs to the hard problem and specifies no method for it. This is the crux of the whole "independent evidence" claim and it is hand-waved.
3. **Deterministic-threshold promotion (§7.2).** "Evaluated through walk-forward replay" is stated; the actual accept/reject *rule* for a threshold set is not. Without it, threshold selection is unfalsifiable.

---

## Recommended Changes (highest impact first)

1. **Demote the document's authority.** `[DOC]`/`[CONST]` Strike `Authority: Tier-1 Architecture Specification` and `Status: Architecture and Implementation Planning`. Reclassify as **Research / Vision (non-binding)**, per the Constitution's explicit Vision-vs-Architecture separation. It may inform future architecture; it may not govern until it has discharged Article VII's burden of proof. *(This is the same disposition previously applied to the Learning Intelligence Architecture, and for the same reason.)*

2. **Replace §23's "Immediate Implementation Plan" with a single authorized activity: Phase 0 (repository audit).** `[STATE]` Phase 0 is safe, isolated, changes no runtime behavior, and directly answers the most important open question — whether this duplicates the regime capability already in production. Everything past Phase 0 is gated on First Autonomous Profit *or* a Parallel-Authorized-Lane isolation guarantee, **plus** evidence.

3. **Reframe from "new layer" to "consolidate what we already run" (§20, §28).** `[STATE]` Have Phase 0 inventory the existing production regime-aware weighting, `STRATEGY_ENGINE.md`'s trend-regime filter, and `AI_LAYER.md`'s classifier first. Do not specify a new `services/market_state/` tree until that audit proves the existing homes cannot absorb the capability.

4. **Collapse the conditioning grid to what data can support.** `[JUDGMENT]` Start compatibility at, at most, {direction × volatility}, pooled across parameter sets and (initially) across strategies. Earn finer resolution only when independent-episode counts justify it. Same principle for calibration: pooled/global first, stratify later.

5. **Add the anti-snooping controls** (pre-registration, deflated criterion, untouched hold-out era) before *any* model-selection work, not after.

6. **Add the OOD/novelty fail-closed output** and make **cost-net performance a hard promotion gate.**

7. **Add excess-over-base-rate measurement** to the compatibility layer.

8. **Demote the HMM to "one optional experiment gated behind a deterministic baseline that must lose to it on out-of-sample, cost-adjusted evidence,"** and prune the eleven-model ensemble and Phase B/C menu to what a spot venue can act on.

9. **Specify the currently hand-waved parts** (multi-timeframe synthesis, agreement computation, threshold promotion rule) *before* they are built — or defer them explicitly. They are where the overfitting will actually happen.

---

## Immediate Recommendation

**B — Approve with revisions.**

Not A (approve immediately), because approving it *as written* — a Tier-1 governing specification with an immediate implementation plan — would authorize the project to begin building an eleven-model regime subsystem before it has closed its first autonomous trade, in direct tension with Articles VI and VII and with the project's own standing decisions. `[JUDGMENT]` That would be the single largest strategic mistake currently available to the project, and it would repeat the exact failure mode the Constitution exists to prevent.

Not C (reject and redesign), because the *thinking* is genuinely good and should not be thrown away. The leakage discipline, the confidence architecture, the neutral-ID/anti-narrative rigor, and the baseline/ablation spine are assets worth preserving.

The correct action is to **keep the intellectual content, strip the authority, and authorize only the repository audit.** `[STATE]` That places this work in the same principled parallel lane as Historical Intelligence Platform Phase 3: real, valuable, and structurally unable to endanger First Autonomous Profit. When the core loop closes and the live system starts producing the decision evidence that would tell you *which regime distinctions actually change outcomes*, revisit this document — and let that evidence, not this design session, decide how much of it gets built.

The best compliment I can pay the document is that it already contains the principle that should govern its own fate: *evidence before adoption; the burden of proof belongs to the new idea.* Hold it to its own standard.
