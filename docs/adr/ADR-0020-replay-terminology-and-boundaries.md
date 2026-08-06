# ADR-0020: Replay Terminology and Boundaries

## Status
Accepted

## Context

A repository-wide documentation and architecture reconciliation pass (see `docs/DOCUMENTATION_DRIFT_REPORT.md` §4.3) found the word "replay" used for three genuinely distinct mechanisms in code, plus a fourth, not-yet-built meaning described redundantly across four separate architecture documents:

1. **Decision-package identity replay** — `apps/api/app/services/replay/` (`default_agent.py::DefaultReplayAgent.replay`), exposed via `POST /arena/replay`. Answers: "can this already-made, already-persisted decision be deterministically reconstructed from its own immutable evidence?" Verified via `decisions/replay_candidates.py::certify_decision_package_readiness_v0`'s double-build content-hash equality check. It never touches live candle data and never evaluates a hypothetical.
2. **Single-candidate counterfactual replay** — `apps/api/app/services/entry_intelligence/shadow_validation.py::replay_rejected_buy_candidate_counterfactual`. Answers: "would this one specific, possibly-never-executed BUY_LIMIT candidate have filled against historical candles, and what would it have earned?" A pure, read-only, single-candidate simulation, reusing `strategy_outcomes/service.py`'s candle-loading primitives.
3. **Counterfactual Outcome Ledger horizon re-evaluation** — `apps/api/app/services/decisions/counterfactuals.py::evaluate_counterfactual_outcome_ledger_v1`. Answers: "at a fixed horizon after a real decision, what would each of BUY/SELL/WAIT have earned?" Distinct from (2): it evaluates realized production decisions at fixed horizons as part of the documented Decision Intelligence Engine (`DECISION_INTELLIGENCE_ENGINE.md` §8), not hypothetical candidates.
4. **Chronological historical-market replay** — not built. Described, independently and with substantial mutual overlap, by `docs/HISTORICAL_INTELLIGENCE_PLATFORM.md`, `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md` (not tracked in git — never committed), `docs/HISTORICAL_MARKET_REPLAY_ENGINE.md`, and `PIPELINE_ARCHITECTURE.md` §15–20 — four documents, written at three different times (2026-07-25, 2026-07-31, and an untracked file with no commit date), each independently asserting the same "the system must never know the future" principle in near-identical language, with no cross-referencing between them. `docs/OMNITRADE_REPOSITORY_REALITY_CHECK.md` (row B5) independently flagged this exact collision earlier in the project's history; it was never resolved.
5. `apps/api/app/services/backtesting/` (`engine.py`, `fills.py`) is a fifth, adjacent but explicitly distinct concept — a deterministic strategy/parameter backtester that does not run the live Risk/AI/decision pipeline. `PIPELINE_ARCHITECTURE.md` §2.8 states "no separate backtest-only decision pipeline may duplicate or imitate the live decision logic" as a governing anti-goal; the backtester's non-convergence with the live pipeline is an acknowledged, not-yet-closed gap per that same document, not new information from this ADR.

None of items 1–3 are duplicates of each other in the sense of doing the same job twice — they answer genuinely different questions and each is the right, singular tool for its question. The risk this ADR addresses is purely terminological: a future contributor asked to build "the replay engine" has no way to know, from the word alone, which of three existing meanings is meant, or whether a fourth, unbuilt meaning is actually intended — and the four historical-replay documents compound this by describing the fourth meaning as if it were settled, cross-validated architecture, when it is four independent, unreconciled drafts.

## Decision

The word "replay," used alone and without qualification, must not be used in future architecture documents or code to mean any of the concepts above. Each of the following names is the required, specific term for its concept:

- **Decision Replay** — item 1 (`apps/api/app/services/replay/`). Identity/audit reconstruction of an already-made decision.
- **Shadow Validation** or **Counterfactual Candidate Replay** — item 2 (`apps/api/app/services/entry_intelligence/shadow_validation.py`). Single-candidate, offline, what-if simulation of a hypothetical or rejected order.
- **Counterfactual Outcome Ledger (COL)** — item 3 (`apps/api/app/services/decisions/counterfactuals.py`), per its existing, already-correct name in `DECISION_INTELLIGENCE_ENGINE.md` §8. This one is not renamed by this ADR; it is named here only to distinguish it from item 2, with which it is easy to conflate since both compute hypothetical BUY/SELL/WAIT outcomes.
- **Historical Simulation** — item 4, not yet built. This name is chosen because it is already the name of the one piece of real scaffolding that exists for this concept (`apps/api/app/services/historical_simulation/`, per ADR-0012/ADR-0013/ADR-0014's `RunMode.HISTORICAL_SIMULATION`/`EvidenceClass.HISTORICAL_POINT_IN_TIME`). Future work must use this name, not "replay," "historical replay," or "market replay," for the not-yet-built chronological pipeline-replay concept these four documents describe.
- **Backtesting** — item 5, already correctly named and not affected by this ADR; recorded here only to complete the picture.

When any future document or module needs to refer to more than one of these concepts together, it must name each one explicitly rather than using "replay" as an umbrella term.

The four documents describing Historical Simulation (`HISTORICAL_INTELLIGENCE_PLATFORM.md`, `HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md`, `HISTORICAL_MARKET_REPLAY_ENGINE.md`, and `PIPELINE_ARCHITECTURE.md` §15–20) are not merged, deleted, or ranked against each other by this ADR — that is a separate, substantive architecture decision this reconciliation pass is not authorized to make. This ADR only fixes the shared vocabulary they should use once that future decision is made, and flags that `HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md` being untracked in git means it has not been through this project's normal review process and should not be treated as equally authoritative to the other three until it is committed and reviewed.

## Alternatives Considered

- **Pick one of the four historical-replay documents as canonical now.** Rejected: this is a substantive architecture decision (which vision for chronological historical replay the platform actually wants), not a terminology fix, and is explicitly out of scope for a documentation-reconciliation pass whose mandate is "reconcile docs to reality," not "invent new architecture" or "pick between competing unbuilt visions."
- **Rename `apps/api/app/services/replay/` to something more specific (e.g. `decision_package_replay/`) to force disambiguation at the code level.** Rejected: renaming production code/module paths is outside this task's explicit constraints (no renaming, no moving files). The naming discipline is enforced going forward, in documentation and in new code, rather than retroactively.
- **Do nothing, on the grounds that the three existing meanings are each internally clear from their own module's context.** Rejected: this is true locally but not architecturally — the risk is specifically at the level of a document or a conversation that says "the replay engine" without qualification, which is exactly the level at which `docs/OMNITRADE_REPOSITORY_REALITY_CHECK.md` already found this collision causing confusion once.

## Consequences

Benefits:
- Future documents and prompts can refer to "Decision Replay," "Shadow Validation," "the Counterfactual Outcome Ledger," or "Historical Simulation" unambiguously, and a reader immediately knows which of the (up to) three real, working mechanisms — or the one not-yet-built one — is meant.
- `docs/PIPELINE_ARCHITECTURE.md` §15's "Historical Replay Engine" heading and `docs/DECISION_REPLAY_ENGINE.md`/`docs/REPLAY_AGENT_INTERFACE.md` can, in future edits, adopt this vocabulary explicitly rather than continuing to layer new uses of the bare word "replay" onto an already-overloaded term.
- Flags `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md`'s uncommitted status as a process gap worth closing (either commit it through normal review, or remove it from the working tree) independently of its content being reconciled with the other three.

Trade-offs:
- This ADR creates a naming obligation without resolving the underlying substantive question (which historical-replay vision, if any, the platform should actually build) — that decision is deferred, correctly, to whoever is authorized to make it, but readers should not mistake this ADR for having made it.
- Four Historical Simulation documents remain unreconciled with each other in content, even though they must now converge on the same name for the concept they all describe — a partial fix, chosen deliberately to stay within this reconciliation pass's documentation-only mandate.
