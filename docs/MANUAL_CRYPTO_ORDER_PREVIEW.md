# Manual Crypto Order Preview

## Boundary

This workflow is preview-only. It can prepare a Coinbase Advanced spot-order estimate, but it must not submit live execution instructions.

Hard rules:

- Do not call the Coinbase Create Order endpoint.
- Do not persist secrets, JWTs, or raw authenticated request payloads.
- Do not expose any UI control that implies live order placement.
- Fail closed whenever readiness, market data freshness, balance, or risk checks are not satisfied.

## Lifecycle

1. Select a Coinbase Advanced connection that is read-only ready for preview.
2. Load market data, balances, and readiness evidence from the backend.
3. Evaluate deterministic risk gates before any Coinbase preview request is sent.
4. Call the Coinbase preview endpoint and normalize the returned estimate.
5. Store the preview record, audit evidence, and refresh lineage.
6. Allow refresh or cancel actions only inside the preview workflow.

Preview records expire automatically and are treated as transient evidence, not execution instructions.

## Risk And Readiness

The preview flow reuses the existing deterministic risk engine and exchange readiness checks.

A preview can be blocked by:

- Missing or unreadable exchange credentials.
- Connection readiness verdicts that are not preview-safe.
- Stale market data.
- Insufficient available balance for the requested quote size.
- A deterministic risk rejection.
- Global or account-level kill switches.

The backend must always prefer a deny decision over a partially trusted preview result.

## Audit And Evidence

Each preview request should produce durable audit evidence with:

- The selected connection and product.
- The requested side and amount.
- The readiness verdict.
- The risk verdict and explanation.
- The normalized Coinbase preview response.
- Refresh and cancellation lineage.

Do not store raw credentials or any field that would allow replaying live execution.

## Future Live-Order Transition

If the product later grows into live trading, that work must be handled as a separate milestone and a separate API path.

Required controls before any execution feature can be added:

- Explicit product approval.
- Separate order-submission authorization.
- Stronger permission and policy checks.
- Additional audit logging for the execution event.
- User-facing confirmation that clearly distinguishes preview from execution.

Until those controls exist, the preview workflow remains read-only.
