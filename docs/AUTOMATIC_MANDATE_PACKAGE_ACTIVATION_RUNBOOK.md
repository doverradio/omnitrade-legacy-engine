# Automatic mandate package activation runbook

This enables only `READY -> AUTHORIZED(MANDATE) -> DRY_RUN_PASSED -> ACTIVATED(MANDATE) -> STOP`. It does not enable or perform exchange submission.

## Pre-enable proof

From the deployed repository's `apps/api` directory:

```bash
python -m app.operator_cli.main automatic-mandate-activation-readiness --provider kraken_spot --environment production --product BTC-USD --json
```

Proceed only for `READY_TO_ENABLE`. Resolve every reason reported by `NOT_READY`; treat `FAILED_CLOSED` as a stop condition.

## Enable

The production worker is `omnitrade-orchestration.service`. Create its systemd drop-in:

```bash
sudo systemctl edit omnitrade-orchestration.service
```

Enter exactly:

```ini
[Service]
Environment=AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=true
```

Reload and restart only that service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart omnitrade-orchestration.service
```

Verify the effective environment and health:

```bash
systemctl show omnitrade-orchestration.service --property=Environment --value
systemctl is-active omnitrade-orchestration.service
systemctl show omnitrade-orchestration.service --property=NRestarts --value
journalctl -u omnitrade-orchestration.service -n 200 --no-pager
```

The environment must contain `AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=true`. Do not enable `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED` in this procedure.

After a package advances, prove its persisted evidence:

```bash
python -m app.operator_cli.main automatic-mandate-activation-proof --package-id PACKAGE_UUID --json
```

`PROVEN` means mandate authorization, dry-run, and activation evidence agree and the inspected records show no submission or reconciliation. It does not mean an order was submitted or a position opened.

## Rollback

Run `sudo systemctl edit omnitrade-orchestration.service`, delete the feature's `Environment` line, then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart omnitrade-orchestration.service
systemctl show omnitrade-orchestration.service --property=Environment --value
systemctl is-active omnitrade-orchestration.service
```

If `/etc/systemd/system/omnitrade-orchestration.service.d/override.conf` contains only this setting, the equivalent explicit rollback is:

```bash
sudo rm /etc/systemd/system/omnitrade-orchestration.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart omnitrade-orchestration.service
```
