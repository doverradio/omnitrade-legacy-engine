from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPOSITORY_ROOT / "scripts" / "activation_only_environment_selector.sh"


def _write_fake_systemctl(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  daemon-reload) ;;
  restart)
    mkdir -p "${OMNITRADE_PROC_ROOT}/4242"
    activation="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=//p' "${OMNITRADE_ACTIVATION_STATE_DIR}/current.env")"
    submission="$(sed -n 's/^LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=//p' "${OMNITRADE_ACTIVATION_STATE_DIR}/current.env")"
    preparation="$(sed -n 's/^LIVE_CRYPTO_PREPARATION_ENABLED=//p' "${OMNITRADE_ACTIVATION_STATE_DIR}/current.env")"
    package_id="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID=//p' "${OMNITRADE_ACTIVATION_STATE_DIR}/current.env")"
    if [[ "${FAKE_UNSAFE_ON_RESTART:-}" == "1" && "${activation}" == "true" ]]; then submission=true; fi
    printf 'AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=%s\\0LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=%s\\0LIVE_CRYPTO_PREPARATION_ENABLED=%s\\0AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID=%s\\0' \
      "${activation}" "${submission}" "${preparation}" "${package_id}" >"${OMNITRADE_PROC_ROOT}/4242/environ"
    ;;
  is-active) ;;
  show) printf '4242\\n' ;;
  *) exit 64 ;;
esac
"""
    )
    path.chmod(0o755)


def _environment(tmp_path: Path) -> dict[str, str]:
    fake_systemctl = tmp_path / "systemctl"
    _write_fake_systemctl(fake_systemctl)
    return {
        **os.environ,
        "OMNITRADE_ALLOW_NON_ROOT_FOR_TESTS": "1",
        "OMNITRADE_SYSTEMCTL": str(fake_systemctl),
        "OMNITRADE_ACTIVATION_DROPIN_DIR": str(tmp_path / "dropin"),
        "OMNITRADE_ACTIVATION_STATE_DIR": str(tmp_path / "state"),
        "OMNITRADE_PROC_ROOT": str(tmp_path / "proc"),
    }


def _run(action: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), action],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_args(arguments: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *arguments], env=environment, check=False, capture_output=True, text=True,
    )


def test_prepare_stages_explicit_safe_state_and_is_idempotent(tmp_path: Path) -> None:
    environment = _environment(tmp_path)

    first = _run("prepare", environment)
    second = _run("prepare", environment)

    assert first.returncode == second.returncode == 0
    current = (tmp_path / "state" / "current.env").read_text()
    assert "AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=false" in current
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false" in current
    assert "LIVE_CRYPTO_PREPARATION_ENABLED=true" in current
    assert "selector=PREPARED_OFF" in second.stdout


def test_on_and_off_are_atomic_verified_selections(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    assert _run("prepare", environment).returncode == 0

    enabled = _run("on", environment)
    assert enabled.returncode == 0
    assert "selector=ON" in enabled.stdout
    assert "automatic_activation=true" in enabled.stdout
    assert "live_submission=false" in enabled.stdout

    disabled = _run("off", environment)
    assert disabled.returncode == 0
    assert "selector=OFF" in disabled.stdout
    assert "automatic_activation=false" in disabled.stdout


def test_pinned_on_allows_exactly_one_package_and_off_removes_pin(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    package_id = "11111111-1111-4111-8111-111111111111"
    assert _run("prepare", environment).returncode == 0

    enabled = _run_args(["on-package", package_id], environment)

    assert enabled.returncode == 0
    assert "selector=PINNED_ON" in enabled.stdout
    assert f"activation_package_id={package_id}" in enabled.stdout
    assert f"AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID={package_id}" in (
        tmp_path / "state" / "current.env"
    ).read_text()
    disabled = _run("off", environment)
    assert disabled.returncode == 0
    assert "AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID" not in (
        tmp_path / "state" / "current.env"
    ).read_text()


def test_pinned_on_rejects_invalid_package_identity(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    assert _run("prepare", environment).returncode == 0

    result = _run_args(["on-package", "not-a-package"], environment)

    assert result.returncode != 0
    assert "canonical UUID" in result.stderr
    assert "AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=false" in (
        tmp_path / "state" / "current.env"
    ).read_text()


def test_on_rolls_back_when_process_environment_is_unsafe(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    assert _run("prepare", environment).returncode == 0
    environment["FAKE_UNSAFE_ON_RESTART"] = "1"

    result = _run("on", environment)

    assert result.returncode != 0
    assert "rolled back" in result.stderr
    current = (tmp_path / "state" / "current.env").read_text()
    assert "AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=false" in current
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false" in current


def test_refuses_to_overwrite_unexpected_managed_content(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    assert _run("prepare", environment).returncode == 0
    (tmp_path / "state" / "on.env").write_text("LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=true\n")

    result = _run("on", environment)

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert "AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=false" in (
        tmp_path / "state" / "current.env"
    ).read_text()


def test_prepare_preserves_unexpected_existing_selector(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    current = state_dir / "current.env"
    current.write_text("OPERATOR_OWNED_VALUE=preserve\n")

    result = _run("prepare", environment)

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert current.read_text() == "OPERATOR_OWNED_VALUE=preserve\n"
