#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = Path(os.getenv("OMNITRADE_OPERATOR", str(ROOT / "operator")))
SELECTOR = Path(os.getenv("OMNITRADE_ACTIVATION_SELECTOR", str(ROOT / "scripts/activation_only_environment_selector.sh")))
STATE_DIR = Path(os.getenv("OMNITRADE_ACTIVATION_STATE_DIR", "/etc/omnitrade/activation-only"))
STATE_FILE = STATE_DIR / "watchdog-state.json"
REPORT_FILE = STATE_DIR / "watchdog-report.json"
LOCK_FILE = STATE_DIR / "watchdog.lock"
POLL_SECONDS = float(os.getenv("OMNITRADE_WATCHDOG_POLL_SECONDS", "10"))
PROVIDER = os.getenv("OMNITRADE_WATCHDOG_PROVIDER", "kraken_spot")
ENVIRONMENT = os.getenv("OMNITRADE_WATCHDOG_ENVIRONMENT", "production")
PRODUCT = os.getenv("OMNITRADE_WATCHDOG_PRODUCT", "BTC-USD")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _run(arguments: list[str], *, accept_failure: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, check=False, capture_output=True, text=True)
    if result.returncode and not accept_failure:
        raise RuntimeError(result.stderr.strip() or f"command failed: {arguments[0]}")
    return result


def _selector(action: str, package_id: str | None = None) -> str:
    arguments = [str(SELECTOR), action]
    if package_id is not None:
        arguments.append(package_id)
    return _run(arguments).stdout


def _selector_state() -> dict:
    raw = _selector("inspect")
    values = dict(re.findall(r"^([^=\n]+)=([^\n]*)$", raw, re.MULTILINE))
    return {"raw": raw, **values}


def _operator_json(arguments: list[str]) -> dict:
    result = _run([str(OPERATOR), *arguments, "--json"], accept_failure=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(result.stderr.strip() or "operator returned invalid JSON") from exc


def _readiness() -> dict:
    return _operator_json([
        "automatic-mandate-activation-readiness", "--provider", PROVIDER,
        "--environment", ENVIRONMENT, "--product", PRODUCT,
    ])


def _proof(package_id: str) -> dict:
    return _operator_json(["automatic-mandate-activation-proof", "--package-id", package_id])


def _require_root(action: str) -> None:
    if os.geteuid() != 0 and os.getenv("OMNITRADE_ALLOW_NON_ROOT_FOR_TESTS") != "1":
        raise PermissionError(f"{action} requires root")


def _interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _finish(state: dict, verdict: str, reason: str, proof: dict | None) -> int:
    try:
        _selector("off")
        final_configuration = _selector_state()
    except Exception as exc:  # the report must survive even when rollback verification fails
        final_configuration = {"selector": "UNKNOWN", "rollback_error": str(exc)}
    state.update(state=verdict, completed_at=_now(), final_verdict=verdict)
    state.setdefault("reason_codes", []).append(reason)
    _atomic_json(STATE_FILE, state)
    report = {
        "final_verdict": verdict,
        "attempt": state,
        "proof": proof,
        "final_safety_configuration": final_configuration,
        "activation_disabled_afterward": final_configuration.get("selector") == "OFF"
        and final_configuration.get("automatic_activation") == "false",
        "live_order_submitted": bool(proof and (proof.get("provider_order_id") or proof.get("submitted_at"))),
        "live_submission_called": False,
        "provider_submission_called": False,
        "submission_callable_reachable": False,
        "provider_submission_callable_reachable": False,
    }
    _atomic_json(REPORT_FILE, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if verdict == "SUCCEEDED" and report["activation_disabled_afterward"] else 1


def arm() -> int:
    _require_root("arm")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another watchdog process holds the arm lock") from exc
        if STATE_FILE.exists() and json.loads(STATE_FILE.read_text()).get("state") != "DISARMED":
            raise RuntimeError("existing watchdog attempt must be explicitly disarmed before re-arming")
        initial = _selector_state()
        if initial.get("selector") != "OFF" or initial.get("live_submission") != "false":
            raise RuntimeError("selector must be OFF with live submission explicitly disabled before arming")
        state = {
            "attempt_id": str(uuid4()), "state": "ARMED", "armed_at": _now(),
            "provider": PROVIDER, "environment": ENVIRONMENT, "product": PRODUCT,
            "package_id": None, "reason_codes": [],
            "transitions": [{"state": "ARMED", "at": _now()}],
            "initial_safety_configuration": initial,
        }
        _atomic_json(STATE_FILE, state)
        REPORT_FILE.unlink(missing_ok=True)
        signal.signal(signal.SIGINT, _interrupt)
        signal.signal(signal.SIGTERM, _interrupt)
        try:
            while True:
                ready = _readiness()
                configuration = ready.get("configuration", {})
                boundary = ready.get("submission_boundary", {})
                if configuration.get("live_crypto_order_submission_enabled") is not False:
                    return _finish(state, "FAILED", "live_submission_enabled", ready)
                if configuration.get("live_crypto_preparation_enabled") is not True:
                    return _finish(state, "FAILED", "live_preparation_disabled", ready)
                if boundary.get("submission_callable_reachable") is not False:
                    return _finish(state, "FAILED", "submission_callable_reachable", ready)
                if boundary.get("provider_submission_callable_reachable") is not False:
                    return _finish(state, "FAILED", "provider_submission_callable_reachable", ready)
                if ready.get("active_activation_count") != 0:
                    return _finish(state, "FAILED", "active_activation_conflict", ready)
                count = ready.get("eligible_package_count")
                if count == 0:
                    allowed = {"no_package_available", "stale_package"}
                    codes = {item.get("code") for item in ready.get("reason_codes", [])}
                    if not codes.issubset(allowed):
                        return _finish(state, "FAILED", ",".join(sorted(codes - allowed)), ready)
                    time.sleep(POLL_SECONDS)
                    continue
                if count != 1:
                    return _finish(state, "FAILED", "ambiguous_eligible_packages", ready)
                if ready.get("verdict") != "READY_TO_ENABLE":
                    return _finish(state, "FAILED", "readiness_not_ready", ready)
                if ready.get("mandate", {}).get("evaluation_readiness", {}).get("status") != "SUCCESSFUL_MATCH":
                    return _finish(state, "FAILED", "mandate_evaluation_missing", ready)
                eligible = [item for item in ready.get("packages", []) if not item.get("stale") and not item.get("superseded")]
                if len(eligible) != 1:
                    return _finish(state, "FAILED", "eligible_package_identity_ambiguous", ready)
                package_id = eligible[0].get("package_id")
                try:
                    UUID(str(package_id))
                except (TypeError, ValueError):
                    return _finish(state, "FAILED", "package_identity_missing", ready)
                state.update(state="PACKAGE_CAPTURED", package_id=package_id, captured_at=_now(), readiness=ready)
                state["transitions"].append({"state": "PACKAGE_CAPTURED", "at": _now()})
                _atomic_json(STATE_FILE, state)
                _selector("on-package", str(package_id))
                state["state"] = "PROGRESSION_ENABLED"
                state["transitions"].append({"state": "PROGRESSION_ENABLED", "at": _now()})
                _atomic_json(STATE_FILE, state)
                expires_at = datetime.fromisoformat(str(eligible[0]["preview_expires_at"]).replace("Z", "+00:00"))
                while True:
                    effective = _selector_state()
                    if effective.get("live_submission") != "false":
                        return _finish(state, "FAILED", "live_submission_enabled", None)
                    evidence = _proof(str(package_id))
                    if evidence.get("verdict") == "CONFLICT":
                        return _finish(state, "FAILED", "proof_conflict", evidence)
                    if evidence.get("verdict") == "PROVEN":
                        return _finish(state, "SUCCEEDED", "activation_only_proven", evidence)
                    if datetime.now(timezone.utc) >= expires_at:
                        return _finish(state, "FAILED", "package_expired_during_attempt", evidence)
                    time.sleep(min(1.0, max(POLL_SECONDS, 0.05)))
        except BaseException as exc:
            return _finish(state, "FAILED", f"watchdog_interrupted:{type(exc).__name__}", None)


def inspect() -> int:
    if not STATE_FILE.exists():
        raise RuntimeError("watchdog has not been armed")
    payload = json.loads(STATE_FILE.read_text())
    payload["effective_safety_configuration"] = _selector_state()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def report() -> int:
    if not REPORT_FILE.exists():
        raise RuntimeError("no completed proof report exists")
    print(REPORT_FILE.read_text(), end="")
    return 0


def disarm() -> int:
    _require_root("disarm")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _selector("off")
    state = {"state": "DISARMED", "disarmed_at": _now(), "final_verdict": None,
             "reason_codes": [], "transitions": [{"state": "DISARMED", "at": _now()}]}
    _atomic_json(STATE_FILE, state)
    print("watchdog=DISARMED\nautomatic_activation=false\nlive_submission=false")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="activation-proof-watchdog")
    parser.add_argument("action", choices=("arm", "inspect", "report", "disarm"))
    args = parser.parse_args()
    try:
        return {"arm": arm, "inspect": inspect, "report": report, "disarm": disarm}[args.action]()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
