# ADR-0019: Canonical Capital Campaign Governance Layer

## Status
Accepted

## Context

A repository-wide documentation and architecture reconciliation pass (see `docs/DOCUMENTATION_DRIFT_REPORT.md` §3.1, §2.11-equivalent) found the platform's capital-stewardship responsibility split across implementations under overlapping names, with no document naming which is authoritative:

1. **`apps/api/app/services/capital_campaigns/` (plural)** — a straightforward CRUD/lifecycle service (`GET/POST /capital-campaigns`, `PATCH/DELETE /capital-campaigns/{uuid}`, profit-policy/profit-cycle sub-resources) matching `docs/CAPITAL_CAMPAIGNS.md`'s description closely, including that document's claim that the feature "does not enable live automation."
2. **`apps/api/app/services/capital_campaign_domain/` (singular) + `apps/api/app/services/capital_campaign_orchestration/`** — a larger, richer "definition + runtime pin" system (`capital_campaign_domain/service.py`, `preview_engine.py`, `commissioned_state_machine.py`, `commissioned_entry_execution.py`, `commissioned_control_plane.py`, and `capital_campaign_orchestration/authoritative.py`, 3,000+ lines) that reconciles onto the *same* `capital_campaigns` table via a `_ensure_runtime_campaign_pin` mechanism, and which is the layer that actually submits real live orders via `commissioned_entry_execution.py` → `LiveCryptoOrderService`. This layer, and the fact that it now drives live capital, is not mentioned in `docs/CAPITAL_CAMPAIGNS.md` at all.
3. **ADR-0008 (Capital Allocation Engine)** — an "architectural intent only" ADR describing an unimplemented `Master Account → Paper Portfolios → Strategies → Future Agents` hierarchy, self-declared to introduce "no implementation details, schema changes, API changes, or code changes." No `MasterAccount`, `PaperPortfolio`, or portfolio-scoped `Agent` model exists anywhere in the codebase.
4. **`docs/CAPITAL_ALLOCATION_ENGINE.md` / `apps/api/app/services/capital_allocation/`** — a real, small, deterministic, tournament-ranking-driven *paper-capital allocation recommendation* generator for the Strategy Arena UI (`GET /arena/capital-allocation`). It shares ADR-0008's name but none of its scope: no portfolios, no agents, no rebalancing, no idle-capital recovery.
5. **ADR-0011 (Autonomous Capital Mandate Engine)** — the actual, implemented governance layer (`apps/api/app/services/mandates/`) that determines whether a proposed autonomous action is authorized, working alongside the layer in item 2.

In practice, what got built to govern real, live capital is items 2 and 5 together (Campaign Domain + Autonomous Capital Mandates), under a data model shaped nothing like ADR-0008's portfolio/agent hierarchy. ADR-0008 was never formally superseded, and the phrase "Capital Allocation Engine" now refers to two unrelated things (items 3 and 4) neither of which is Capital Campaigns. This creates real risk: a future contributor reading ADR-0008 could reasonably conclude the portfolio/agent model is either already built or is the intended target for new capital-allocation work, when neither is true.

## Decision

`apps/api/app/services/capital_campaign_domain/` and `apps/api/app/services/capital_campaign_orchestration/`, together with `apps/api/app/services/mandates/` (ADR-0011), are the canonical governance layer for live and paper capital deployment in this platform today. Any future work touching campaign authorization, runtime capital pins, or the path from a strategy proposal to a governed order must build on this layer.

`apps/api/app/services/capital_campaigns/` (plural) remains the canonical CRUD/lifecycle surface for the `capital_campaigns` table's operator-facing fields (status transitions, profit policy, profit cycles) — it is not deprecated, but its scope is narrower than "capital campaign governance" as a whole, and it must not be read as covering the live-execution-authorizing responsibility that `capital_campaign_domain`/`capital_campaign_orchestration` actually own.

ADR-0008's `Master Account → Paper Portfolios → Strategies → Future Agents` hierarchy is recorded as **not implemented and not the direction the platform actually took**. ADR-0008 is not reversed or deleted by this ADR — it remains a legitimate record of architectural intent expressed at the time it was written — but this ADR establishes that Capital Campaigns + Autonomous Capital Mandates (ADR-0011), not the ADR-0008 portfolio/agent model, is where capital-governance responsibility actually lives today. Any future work that wants to build ADR-0008's portfolio/agent hierarchy must first reconcile it explicitly against the campaign/mandate model that has since been built — it cannot simply be implemented as originally envisioned without that reconciliation, since the two would otherwise become two competing capital-authority systems.

`docs/CAPITAL_ALLOCATION_ENGINE.md`'s Strategy Arena paper-allocation recommendation generator (`apps/api/app/services/capital_allocation/`) is confirmed as its own, correctly-scoped, real subsystem — it does not need renaming, but this ADR records explicitly that it is **not** an implementation of ADR-0008's Capital Allocation Engine concept, despite the shared name, to prevent future conflation.

## Alternatives Considered

- **Formally supersede ADR-0008 with this ADR.** Rejected: ADR-0008 documents intent from a specific point in the platform's evolution and is not itself factually wrong about what it says it decided ("architectural intent only... no implementation details"). It is the platform's *subsequent, undocumented* divergence from that intent that needed recording, not a reversal of ADR-0008's own content. A future ADR may formally supersede ADR-0008 if and when the platform makes an explicit decision to either build the portfolio/agent hierarchy or retire the idea; this ADR is not that decision.
- **Consolidate `capital_campaigns` (plural) and `capital_campaign_domain` (singular) into one service now.** Rejected: this is a documentation-reconciliation ADR, not a refactor authorization; consolidating two live services that both write to the same table is a real, risky code change requiring its own dedicated review, explicitly out of scope for this pass.
- **Rename `apps/api/app/services/capital_allocation/` to avoid the name collision with ADR-0008.** Rejected for the same reason — renaming production code is outside this task's scope (`docs/DOCUMENTATION_DRIFT_REPORT.md` and this ADR are documentation-only). Recorded as a disambiguation instead of a rename.

## Consequences

Benefits:
- Future capital-governance work has one clearly-identified canonical layer (`capital_campaign_domain` + `capital_campaign_orchestration` + `mandates`), removing the risk that a contributor extends the narrower CRUD layer believing it covers live-execution authorization.
- `docs/CAPITAL_CAMPAIGNS.md` can now be corrected to acknowledge the layer it omits, and its "no live automation" claim can be scoped accurately (per `docs/DOCUMENTATION_DRIFT_REPORT.md` §3.1) instead of read as a platform-wide guarantee it no longer is.
- Prevents ADR-0008's aspirational hierarchy from being read as either "already built" or "the current target" by a future implementer skimming the ADR index.

Trade-offs:
- Two real services (`capital_campaigns` plural, `capital_campaign_domain` singular) continue to write to the same table via an ad hoc runtime-pin reconciliation mechanism rather than a single owning service — this ADR names the situation and its canonical layer, but does not resolve the underlying duplication at the code level. A future consolidation remains a legitimate, separate piece of technical debt.
- If the platform later decides to build ADR-0008's portfolio/agent hierarchy after all, that work must now explicitly address how it relates to the campaign/mandate model this ADR names as canonical — a real, nontrivial reconciliation cost that exists regardless of whether this ADR is written, but is now at least visible in advance rather than discovered mid-implementation.
