# First Live Crypto Trade Runbook

This runbook documents the readiness and safety checks for the live crypto order path. It does not authorize live trading by itself.

## Required Environment Variables

- `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`
- `LIVE_CRYPTO_DRY_RUN_ENABLED=true`
- `LIVE_CRYPTO_MAX_ORDER_USD=5`
- `LIVE_CRYPTO_CONFIRMATION_CHALLENGE_MINUTES=1`
- `LIVE_CRYPTO_PREVIEW_MAX_AGE_SECONDS=30`
- `LIVE_CRYPTO_BALANCE_MAX_AGE_SECONDS=30`
- `LIVE_CRYPTO_READINESS_MAX_AGE_SECONDS=60`
- `LIVE_CRYPTO_PRICE_MAX_AGE_SECONDS=30`

Any change to these values requires a fresh readiness review before another dry run or live-order enablement discussion.

## Prerequisites

- Confirm the live trading profile exists, is approved, and is in the expected live lifecycle state.
- Confirm the Coinbase Advanced connection is production, credential-valid, and has trade permission.
- Confirm the operator has a bearer token and that the live-order mutation endpoints remain bearer-protected.
- Confirm the first-order limit remains $5 USD and that the server feature flag stays closed unless an explicit operator decision is made.
- Confirm the account has enough USD balance for a $5 BTC-USD market buy.

## Authorization Setup

- Live-order mutation routes require bearer authentication and operator identity matching.
- CORS configuration is not an authorization control and must not be treated as one.
- Knowing the endpoint URL is insufficient; unauthenticated and operator-mismatched requests must fail closed.
- Use an authenticated operator client for `prepare-confirmation`, `dry-run`, `submit`, `reconcile`, and `cancel`.

## Coinbase Permission Guidance

- Minimum required scopes:
	- read accounts
	- read balances
	- read products
	- read orders
	- preview orders
	- create orders (future-governed path only)
- Never require:
	- withdrawals
	- transfers
	- wallet export
	- address management
- Trade permission is required for preview/dry-run readiness.
- Withdrawal/transfer permission is treated as dangerous and must show a blocking warning.
- Confirm the production connection is the one being evaluated; sandbox credentials are not evidence for live readiness.

## Verify The Feature Flag Stays Closed

- Check `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false` in the active environment.
- Check `LIVE_CRYPTO_DRY_RUN_ENABLED=true` in the active environment.
- Verify the readiness response reports a blocked or dry-run-only verdict while submission remains disabled.
- Do not proceed if the readiness page reports operator enablement without a manual review of every blocking check.

## Safe Dry Run

- Load the live orders workspace.
- Enter the live trading profile ID, the approved preview ID, and the operator identity.
- Run the dry-run path.
- Confirm the response shows `DRY_RUN_READY` or `DRY_RUN_BLOCKED` and `provider_create_order_called=false`.
- Confirm the UI or API message says: `Dry run completed. No Coinbase order was submitted.` when the dry run is successful.
- Confirm the safe request summary only includes non-secret request fields.
- Dry-run completion does not authorize live trading.

## Evidence To Inspect

- Readiness verdict and check list.
- Preview age, balance age, readiness age, and price age.
- Risk evaluation result and any kill-switch or approval gate failure.
- Audit evidence records.
- Accounting and reconciliation records.
- Mission Control or other operator evidence surfaces that record the same live trade intent.

## Readiness Review

- Verify the readiness verdict is consistent with the current feature flags.
- Review each readiness check for profile state, production connection state, credential validity, trade permission, balance, preview freshness, risk evidence, and kill-switch status.
- Treat missing timestamps, missing balances, or missing verification evidence as blocking.

## Risk Review

- Confirm the order remains limited to `BTC-USD`, `BUY`, `MARKET`, and a maximum of `$5`.
- Confirm the Risk Engine produced a distinct execution-time decision and that preview approval is not being reused as execution approval.
- Confirm global kill switch and account-level live controls are clear before any future enablement review.

## Audit Review

- Review the stored safe provider response for non-secret contents only.
- Confirm no credential material, JWT, authorization header, or decrypted key blob appears in operator-visible evidence.
- Confirm any ambiguous provider response is documented before any retry discussion.

## Capital Ledger Review

- Confirm no dry run created live capital allocation, position, trade, or PnL.
- Confirm live and paper capital remain visibly separated in operator reporting.
- Confirm any eventual fill accounting uses actual fill quantity, price, and fee exactly once.

## Mission Control Review

- Review Mission Control as informational evidence only.
- Confirm it does not imply live-trade authorization.
- Confirm any live-order readiness decision is backed by route/service evidence, not dashboard tone.

## Handling Ambiguous Provider Responses

- Treat any missing order ID, unknown status, empty response body, or partial provider acknowledgement as unresolved.
- Stop the workflow and mark the order for reconciliation.
- Do not retry as a new trade unless the idempotency path is explicitly understood and verified.

## Rollback And Emergency Stop

- Keep `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false` to fail closed.
- Keep `LIVE_CRYPTO_DRY_RUN_ENABLED=true` if you still need evidence collection without live submit.
- Use the live kill-switch and approval controls if the operator sees unexpected state.
- Cancel or reconcile only through the authenticated live-order endpoints.
- Record the incident in the audit evidence path before attempting another dry run or submission.

## Emergency Kill-Switch Procedure

- Engage the relevant live kill-switch control.
- Freeze any retry or repeated submit attempts until reconciliation evidence is complete.
- Preserve provider response evidence, readiness state, and operator action details.

## Future Manual Steps For One $5 BTC-USD BUY

- Reconfirm all prerequisites and readiness checks.
- Generate a fresh approved preview.
- Run dry run and verify no Coinbase Create Order call occurred.
- Review risk, audit, Capital Ledger, and Mission Control evidence.
- Only after separate governance approval, consider a temporary controlled enablement review for a single `$5` `BTC-USD` `BUY` `MARKET` order.
- Immediately reconcile and inspect append-only accounting after any eventual provider acknowledgement.

## Explicit Non-Authorization

- This runbook is an evidence checklist, not a live-trading approval.
- No Coinbase Create Order call should be sent unless the operator has separately authorized it under the project governance process.