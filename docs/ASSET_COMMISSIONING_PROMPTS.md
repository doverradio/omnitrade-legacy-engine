# OmniTrade Asset Commissioning Prompts

Version: 0.1  
Date: 2026-07-25

## Prompt 1 — Read-Only Investigation and Design

Use this first. It has already been sent to Claude in the current session.

```text
We need to replace OmniTrade’s cumbersome manual production asset-onboarding procedure with a first-class, fail-closed Asset Commissioning Service and operator API.

Current production evidence:

- BTC canonical asset ID:
  e4dc4afe-3f19-418c-89a1-a5d6f2f9c5e5

- ETH canonical asset ID:
  3626b074-93d4-4e4c-8402-b51327068209

- Production campaign:
  e9a9e8e9-9574-498d-b49e-f011218c7f2b

- Active mandate:
  ea9b2178-59bc-4505-beb8-9a6bccec2818

- Current governing mandate version:
  fbe63bcd-b100-49f3-9ed8-7082764a7a4f

- Current governing mandate allows:
  BTC-USD
  ETH-USD

- ETH has been created and received at least 95 fifteen-minute candles.

- The activation-only selector now references the new governing mandate version.

- Both omnitrade-api and omnitrade-orchestration were restarted successfully.

Important runtime question:

Recent logs being watched were initiated before ETH commissioning. Their output shows BTC-triggered activity, but that history alone does not prove the current runtime is BTC-only. Determine from code and fresh evidence whether ETH is dynamically discovered and processed.

Primary objective:

Design and implement a production-grade Asset Commissioning Service and operator API that handles the entire safe lifecycle of adding a provider asset without manual database edits, handcrafted curl payloads, or repeated shell-command sequences.

First, perform a read-only repository investigation and report:

1. How the continuous pipeline chooses which assets to process.
2. Whether BTC asset identity or the trigger name is hard-coded.
3. Whether the worker queries campaign assets dynamically.
4. Whether market-data ingestion supports multiple Kraken products concurrently.
5. Whether ETH is currently being ingested and evaluated; provide exact evidence queries/commands.
6. Every existing service, script, endpoint, model, and repository function involved in:
   - provider product verification
   - canonical asset creation
   - candle backfill
   - campaign asset membership
   - mandate-version creation
   - mandate authorization and promotion
   - runtime selector activation
   - continuous-worker asset discovery
   - readiness verification
7. Which existing functions should be reused rather than duplicated.

Then propose the smallest coherent production-safe design.

Required API behavior:

POST /operator/assets/commission/preview

Input:
- provider
- product_id
- campaign_id
- environment

Output:
- canonical normalized product identity
- provider support
- current asset state
- current candle state
- campaign mutation required
- mandate successor required
- exact preserved risk/capital constraints
- runtime-discovery mutation required
- expected changes
- blockers
- deterministic readiness plan
- no production mutation

POST /operator/assets/commission

Input:
- provider
- product_id
- campaign_id
- environment
- activate
- idempotency_key

It must:

1. Verify provider support.
2. Normalize provider/canonical identifiers.
3. Create or reuse the canonical asset.
4. Backfill the required market-data history.
5. Verify minimum candle count and freshness.
6. Add the asset to the campaign if necessary.
7. Create a successor mandate version while preserving all existing fields and limits.
8. Add the product without removing already authorized products.
9. Authorize and promote the successor version using the existing underlying service.
10. Update runtime selection safely if that mechanism remains necessary.
11. Ensure the continuous worker dynamically discovers the asset.
12. Produce an auditable commissioning record.
13. Be idempotent and resumable.
14. Fail closed on any incomplete stage.
15. Never increase capital, order-size, loss, drawdown, or autonomy limits.
16. Never force a trade merely because an asset was commissioned.

GET /operator/assets/commission/{commissioning_id}

Return stage-by-stage status and evidence.

GET /operator/assets/{product_id}/readiness

Required readiness fields:
- provider_supported
- asset_registered
- market_data_current
- candle_count
- campaign_authorized
- mandate_authorized
- runtime_selected
- strategy_evaluation_observed
- live_execution_eligible
- blockers
- warnings
- overall_status

Critical acceptance rule:

An asset must not report READY merely because its database, campaign, and mandate records exist.

READY requires proof that the continuous runtime has processed the asset, such as a strategy_roster_started/run record tied to its canonical asset ID after commissioning.

Use SOL-USD as the first acceptance-test asset after implementation.

Do not mutate production during the investigation. First return:

A. Root-cause findings regarding current multi-asset runtime behavior.
B. Proposed architecture and exact files to change.
C. Migration requirements, if any.
D. Test plan.
E. Rollback plan.
F. Risks.
G. Whether the implementation can avoid restarting systemd for every future asset.
H. A precise implementation sequence.

Wait for approval before making code changes.
```

## Prompt 2 — Review Claude’s Investigation

Send only after Claude returns Prompt 1 findings and after ChatGPT reviews them.

```text
Using your completed read-only investigation, revise the implementation plan to address every confirmed root cause without speculative redesign.

Requirements:

1. Reuse existing domain services wherever possible.
2. Do not launch shell scripts from API handlers.
3. Keep the API thin and place orchestration in an application service.
4. Preserve all existing production risk, capital, autonomy, and audit constraints.
5. Make commissioning idempotent and resumable.
6. Require runtime-observed strategy evaluation before READY.
7. Include exact files to add or modify.
8. Include exact database migration objects, indexes, and constraints if a migration is required.
9. Include unit, integration, API, idempotency, failure-recovery, and runtime-discovery tests.
10. Include rollback behavior.
11. Identify any portions that should be split into separate commits.
12. Do not modify production data or deploy.

Return the final implementation plan and wait for approval.
```

## Prompt 3 — Implement Locally

Send only after the final plan is approved.

```text
Implement the approved Asset Commissioning Service and operator API locally.

Constraints:

- Follow the approved file-by-file plan.
- Reuse existing provider, market-data, campaign, mandate, and audit services.
- Do not weaken or bypass any safety gate.
- Do not mutate production.
- Add all approved tests.
- Run the narrowest relevant test suites first, then the broader regression suite.
- Report every changed file, test result, migration requirement, and remaining risk.
- Do not commit or deploy until review.
```

## Prompt 4 — Correct Defects After Review

```text
Address only the review findings listed below. Do not broaden scope or redesign unrelated components.

[PASTE REVIEW FINDINGS]

After corrections:

- Run the relevant targeted tests.
- Run the approved broader regression suite.
- Report exact changes and results.
- Do not deploy.
```

## Prompt 5 — Commit Preparation

```text
The implementation has passed review. Prepare the commit plan.

Return:

1. Final changed-file list.
2. Migration status.
3. Test commands and results.
4. Suggested commit message.
5. Any deployment-order constraints.
6. Exact rollback procedure.

Do not commit or deploy yet.
```

## Prompt 6 — Production Preview and SOL Acceptance Test

Use only after code is committed, deployed, and the migration state is confirmed.

```text
Perform a production-safe preview for SOL-USD through the new Asset Commissioning API.

Requirements:

- Preview only; no mutation.
- Confirm provider mapping.
- Confirm preserved campaign, risk, capital, and autonomy constraints.
- Show every planned mutation.
- Show runtime-discovery behavior.
- Show blockers and warnings.
- Confirm the request is safe and idempotent.

Return the preview evidence and a go/no-go recommendation. Do not execute commissioning without explicit operator approval.
```

## Prompt 7 — Execute SOL Commissioning

```text
Execute the explicitly approved SOL-USD commissioning plan through the Asset Commissioning API using a unique idempotency key.

Then verify:

- Canonical asset registration
- Candle backfill and freshness
- Campaign membership
- Governing mandate inclusion
- Preserved risk/capital/autonomy limits
- Runtime discovery
- Post-commission SOL strategy-roster evaluation
- Readiness endpoint result
- Audit coherence

Do not force a trade. Report all evidence and any blocker.
```

## Prompt 8 — Scale Assets 4 Through 10

Use only after SOL passes the complete acceptance test.

```text
Prepare a ranked candidate list for assets 4 through 10 using only products supported by the commissioned provider and current production architecture.

For each candidate report:

- Provider support
- Liquidity considerations
- Data availability
- Minimum-order compatibility with Small Account Mode
- Correlation/diversification considerations
- Operational risks
- Recommended onboarding order

Do not commission any asset until the operator approves the list and each asset passes the commissioning preview.
```

