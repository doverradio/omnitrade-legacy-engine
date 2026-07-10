# First Live Crypto Trade Runbook

This runbook documents the readiness and safety checks for the live crypto order path. It does not authorize live trading by itself.

## Prerequisites

- Confirm the live trading profile exists, is approved, and is in the expected live lifecycle state.
- Confirm the Coinbase Advanced connection is production, credential-valid, and has trade permission.
- Confirm the operator has a bearer token and that the live-order mutation endpoints remain bearer-protected.
- Confirm the first-order limit remains $5 USD and that the server feature flag stays closed unless an explicit operator decision is made.
- Confirm the account has enough USD balance for a $5 BTC-USD market buy.

## Verify The Feature Flag Stays Closed

- Check `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false` in the active environment.
- Verify the readiness response reports a blocked or dry-run-only verdict while submission remains disabled.
- Do not proceed if the readiness page reports operator enablement without a manual review of every blocking check.

## Safe Dry Run

- Load the live orders workspace.
- Enter the live trading profile ID, the approved preview ID, and the operator identity.
- Run the dry-run path.
- Confirm the response shows `DRY_RUN_READY` or `DRY_RUN_BLOCKED` and `provider_create_order_called=false`.
- Confirm the safe request summary only includes non-secret request fields.

## Evidence To Inspect

- Readiness verdict and check list.
- Preview age, balance age, readiness age, and price age.
- Risk evaluation result and any kill-switch or approval gate failure.
- Audit evidence records.
- Accounting and reconciliation records.
- Mission Control or other operator evidence surfaces that record the same live trade intent.

## Handling Ambiguous Provider Responses

- Treat any missing order ID, unknown status, empty response body, or partial provider acknowledgement as unresolved.
- Stop the workflow and mark the order for reconciliation.
- Do not retry as a new trade unless the idempotency path is explicitly understood and verified.

## Rollback And Emergency Stop

- Keep `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false` to fail closed.
- Use the live kill-switch and approval controls if the operator sees unexpected state.
- Cancel or reconcile only through the authenticated live-order endpoints.
- Record the incident in the audit evidence path before attempting another dry run or submission.

## Explicit Non-Authorization

- This runbook is an evidence checklist, not a live-trading approval.
- No Coinbase Create Order call should be sent unless the operator has separately authorized it under the project governance process.