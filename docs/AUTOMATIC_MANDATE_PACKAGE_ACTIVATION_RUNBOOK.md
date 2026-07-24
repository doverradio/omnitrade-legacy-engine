# Automatic mandate package activation runbook

This enables only `READY -> AUTHORIZED(MANDATE) -> DRY_RUN_PASSED -> ACTIVATED(MANDATE) -> STOP`. It does not enable or perform exchange submission.

## Pre-enable proof

From the deployed repository's `apps/api` directory:

```bash
python -m app.operator_cli.main automatic-mandate-activation-readiness --provider kraken_spot --environment production --product BTC-USD --json
```

Proceed only for `READY_TO_ENABLE`. Resolve every reason reported by `NOT_READY`; treat `FAILED_CLOSED` as a stop condition.

## Pre-stage the fail-closed selector

The selector uses a later `EnvironmentFile` so its three safety values override the
application `.env` as one unit. Pre-stage it before waiting for a fresh package:

```bash
cd /home/eric/omnitrade-legacy-engine && \
sudo ./scripts/activation_only_environment_selector.sh prepare && \
sudo ./scripts/activation_only_environment_selector.sh inspect

```

Both commands must report automatic activation `false`, live submission `false`,
and live preparation `true`. `prepare` is idempotent and refuses to replace any
unexpected content at its managed paths.

## Enable

After the readiness command reports exactly one fresh eligible package and
`READY_TO_ENABLE`, switch the complete environment atomically:

```bash
cd /home/eric/omnitrade-legacy-engine && \
sudo ./scripts/activation_only_environment_selector.sh on

```

The command restarts only `omnitrade-orchestration.service`, reads the new
worker's `/proc/<MainPID>/environ`, and succeeds only when all three values are:

- `AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=true`
- `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`
- `LIVE_CRYPTO_PREPARATION_ENABLED=true`

If verification fails, the command immediately selects the explicit OFF file,
restarts the same service, verifies rollback, and exits nonzero. Do not continue
after any nonzero result.

After a package advances, prove its persisted evidence:

```bash
python -m app.operator_cli.main automatic-mandate-activation-proof --package-id PACKAGE_UUID --json
```

`PROVEN` means mandate authorization, dry-run, and activation evidence agree and the inspected records show no submission or reconciliation. It does not mean an order was submitted or a position opened.

## Rollback

Select and verify the pre-staged OFF state:

```bash
cd /home/eric/omnitrade-legacy-engine && \
sudo ./scripts/activation_only_environment_selector.sh off && \
sudo ./scripts/activation_only_environment_selector.sh inspect

```

Rollback must report automatic activation `false`, live submission `false`, and
live preparation `true`. Retain the selector files for deterministic inspection
and reuse; they contain no secrets.
