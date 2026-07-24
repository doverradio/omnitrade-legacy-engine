from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPOSITORY_ROOT / "scripts" / "activation_proof_watchdog.sh"
PACKAGE_ID = "11111111-1111-4111-8111-111111111111"


def _executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _environment(tmp_path: Path, readiness: dict, proof: dict) -> dict[str, str]:
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "readiness.json").write_text(json.dumps(readiness))
    (tmp_path / "proof.json").write_text(json.dumps(proof))
    selector = tmp_path / "selector"
    _executable(
        selector,
        """#!/usr/bin/env bash
set -euo pipefail
file="${OMNITRADE_ACTIVATION_STATE_DIR}/fake-selector"
case "$1" in
  inspect)
    value="$(cat "${file}" 2>/dev/null || printf OFF)"
    if [[ "${value}" == OFF ]]; then
      printf 'selector=OFF\nautomatic_activation=false\nlive_submission=false\nlive_preparation=true\n'
    else
      printf 'selector=PINNED_ON\nautomatic_activation=true\nlive_submission=false\nlive_preparation=true\nactivation_package_id=%s\n' "${value}"
    fi
    ;;
  on-package) printf '%s' "$2" >"${file}" ;;
  off) printf OFF >"${file}" ;;
  *) exit 64 ;;
esac
""",
    )
    operator = tmp_path / "operator"
    _executable(
        operator,
        """#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  automatic-mandate-activation-readiness) cat "${FAKE_READINESS}" ;;
  automatic-mandate-activation-proof) cat "${FAKE_PROOF}" ;;
  *) exit 64 ;;
esac
""",
    )
    return {
        **os.environ,
        "OMNITRADE_ALLOW_NON_ROOT_FOR_TESTS": "1",
        "OMNITRADE_ACTIVATION_STATE_DIR": str(state),
        "OMNITRADE_ACTIVATION_SELECTOR": str(selector),
        "OMNITRADE_OPERATOR": str(operator),
        "OMNITRADE_WATCHDOG_POLL_SECONDS": "0",
        "FAKE_READINESS": str(tmp_path / "readiness.json"),
        "FAKE_PROOF": str(tmp_path / "proof.json"),
    }


def _readiness(
    *, count: int = 1, submission: bool = False, active: int = 0,
    evaluation_status: str = "SUCCESSFUL_MATCH", provider_reachable: bool = False,
    expires_at: str = "2099-01-01T00:00:00+00:00",
) -> dict:
    if count == 0:
        reason_codes = [{"code": "stale_package"}]
    elif count > 1:
        reason_codes = [{"code": "ambiguous_eligible_packages"}]
    else:
        reason_codes = []
    return {
        "verdict": "READY_TO_ENABLE" if count == 1 and not submission and active == 0 else "NOT_READY",
        "reason_codes": reason_codes,
        "eligible_package_count": count,
        "active_activation_count": active,
        "configuration": {
            "live_crypto_order_submission_enabled": submission,
            "live_crypto_preparation_enabled": True,
        },
        "submission_boundary": {
            "submission_callable_reachable": False,
            "provider_submission_callable_reachable": provider_reachable,
        },
        "mandate": {"evaluation_readiness": {"status": evaluation_status}},
        "packages": [{
            "package_id": PACKAGE_ID,
            "stale": False,
            "superseded": False,
            "preview_expires_at": expires_at,
        }],
    }


def _proof(verdict: str = "PROVEN") -> dict:
    return {
        "verdict": verdict,
        "reason_codes": [],
        "package_id": PACKAGE_ID,
        "transitions": [
            {"state": "READY", "at": "2026-01-01T00:00:00Z"},
            {"state": "AUTHORIZED", "at": "2026-01-01T00:00:01Z"},
            {"state": "DRY_RUN_PASSED", "at": "2026-01-01T00:00:02Z"},
            {"state": "ACTIVATED", "at": "2026-01-01T00:00:03Z"},
        ],
        "provider_order_id": None,
        "submitted_at": None,
    }


def _run(action: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(SCRIPT), action], env=environment, check=False, capture_output=True, text=True)


def test_one_shot_success_pins_one_package_reports_proof_and_disables_activation(tmp_path: Path) -> None:
    environment = _environment(tmp_path, _readiness(), _proof())

    result = _run("arm", environment)

    assert result.returncode == 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert report["final_verdict"] == "SUCCEEDED"
    assert report["attempt"]["package_id"] == PACKAGE_ID
    assert report["activation_disabled_afterward"] is True
    assert report["live_order_submitted"] is False
    assert [item["state"] for item in report["proof"]["transitions"]] == [
        "READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED",
    ]
    assert (tmp_path / "state" / "fake-selector").read_text() == "OFF"


def test_multiple_packages_fail_closed_and_leave_selector_off(tmp_path: Path) -> None:
    environment = _environment(tmp_path, _readiness(count=2), _proof("NOT_PROVEN"))

    result = _run("arm", environment)

    assert result.returncode != 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert report["final_verdict"] == "FAILED"
    assert "ambiguous_eligible_packages" in report["attempt"]["reason_codes"]
    assert (tmp_path / "state" / "fake-selector").read_text() == "OFF"


def test_live_submission_enabled_is_rejected_before_package_capture(tmp_path: Path) -> None:
    environment = _environment(tmp_path, _readiness(submission=True), _proof("NOT_PROVEN"))

    result = _run("arm", environment)

    assert result.returncode != 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert report["final_verdict"] == "FAILED"
    assert "live_submission_enabled" in report["attempt"]["reason_codes"]


def test_missing_evaluation_fails_closed_before_selector_enablement(tmp_path: Path) -> None:
    readiness = _readiness(evaluation_status="PREFLIGHT_BLOCKED")
    readiness["verdict"] = "READY_TO_ENABLE"
    environment = _environment(tmp_path, readiness, _proof("NOT_PROVEN"))

    result = _run("arm", environment)

    assert result.returncode != 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert "mandate_evaluation_missing" in report["attempt"]["reason_codes"]
    assert (tmp_path / "state" / "fake-selector").read_text() == "OFF"


def test_provider_callable_reachability_fails_closed(tmp_path: Path) -> None:
    environment = _environment(tmp_path, _readiness(provider_reachable=True), _proof("NOT_PROVEN"))

    result = _run("arm", environment)

    assert result.returncode != 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert "provider_submission_callable_reachable" in report["attempt"]["reason_codes"]


def test_package_expiry_during_attempt_fails_and_disables_activation(tmp_path: Path) -> None:
    environment = _environment(
        tmp_path, _readiness(expires_at="2000-01-01T00:00:00+00:00"), _proof("NOT_PROVEN"),
    )

    result = _run("arm", environment)

    assert result.returncode != 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert "package_expired_during_attempt" in report["attempt"]["reason_codes"]
    assert report["activation_disabled_afterward"] is True


def test_proof_identity_conflict_fails_and_disables_activation(tmp_path: Path) -> None:
    proof = _proof("CONFLICT")
    proof["reason_codes"] = ["package_identity_mismatch"]
    environment = _environment(tmp_path, _readiness(), proof)

    result = _run("arm", environment)

    assert result.returncode != 0
    report = json.loads((tmp_path / "state" / "watchdog-report.json").read_text())
    assert "proof_conflict" in report["attempt"]["reason_codes"]
    assert report["activation_disabled_afterward"] is True


def test_disarm_is_idempotent_and_completed_attempt_requires_explicit_disarm(tmp_path: Path) -> None:
    environment = _environment(tmp_path, _readiness(), _proof())
    assert _run("arm", environment).returncode == 0
    assert _run("arm", environment).returncode != 0

    first = _run("disarm", environment)
    second = _run("disarm", environment)

    assert first.returncode == second.returncode == 0
    assert json.loads((tmp_path / "state" / "watchdog-state.json").read_text())["state"] == "DISARMED"
