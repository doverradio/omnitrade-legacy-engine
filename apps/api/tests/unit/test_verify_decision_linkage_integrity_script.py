from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import verify_decision_linkage_integrity as script


def _ok_summary(*, future_violations: int = 0, historical_exemptions: int = 0) -> dict:
    return {
        "scope": {
            "total_rows_scanned": 25,
            "terminal_rows_scanned": 10,
        },
        "counts": {
            "healthy": 9,
            "future_violations": future_violations,
            "historical_exemptions": historical_exemptions,
        },
    }


def test_exit_code_success_and_output_summary(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def _run_verification(*, limit: int):
        assert limit == 50
        return _ok_summary(future_violations=0, historical_exemptions=2)

    monkeypatch.setattr(script, "_run_verification", _run_verification)

    code = script._run_cli(limit=50, debug=False)
    out = capsys.readouterr().out

    assert code == 0
    assert "Status:" in out
    assert "SUCCESS" in out
    assert "Integrity violations: 0" in out
    assert "Historical compatibility exemptions: 2" in out
    assert "No action required." in out


def test_exit_code_integrity_violations(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def _run_verification(*, limit: int):
        return _ok_summary(future_violations=3, historical_exemptions=1)

    monkeypatch.setattr(script, "_run_verification", _run_verification)

    code = script._run_cli(limit=500, debug=False)
    out = capsys.readouterr().out

    assert code == 1
    assert "INTEGRITY_VIOLATIONS_FOUND" in out
    assert "Current-generation violations: 3" in out
    assert "Historical compatibility exemptions: 1" in out
    assert "Review Decision Linkage Integrity report." in out


def test_database_unavailable_connection_refused_maps_to_exit_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def _run_verification(*, limit: int):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(script, "_run_verification", _run_verification)

    code = script._run_cli(limit=500, debug=False)
    captured = capsys.readouterr()

    assert code == 2
    assert "DATABASE_UNAVAILABLE" in captured.out
    assert "Integrity verification did not execute." in captured.out
    assert "No repository conclusions were reached." in captured.out
    assert "Check PostgreSQL service or DATABASE_URL." in captured.out
    assert "Traceback" not in captured.err


def test_database_unavailable_auth_failure_maps_to_exit_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def _run_verification(*, limit: int):
        raise RuntimeError("password authentication failed for user")

    monkeypatch.setattr(script, "_run_verification", _run_verification)

    code = script._run_cli(limit=500, debug=False)
    out = capsys.readouterr().out

    assert code == 2
    assert "DATABASE_UNAVAILABLE" in out
    assert "password authentication failed" in out


def test_unexpected_exception_maps_to_exit_3_without_traceback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def _run_verification(*, limit: int):
        raise ValueError("unexpected verifier bug")

    monkeypatch.setattr(script, "_run_verification", _run_verification)

    code = script._run_cli(limit=500, debug=False)
    captured = capsys.readouterr()

    assert code == 3
    assert "INTERNAL_ERROR" in captured.out
    assert "unexpected verifier bug" in captured.out
    assert "Traceback" not in captured.err


def test_debug_mode_prints_traceback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def _run_verification(*, limit: int):
        raise ValueError("unexpected verifier bug")

    monkeypatch.setattr(script, "_run_verification", _run_verification)

    code = script._run_cli(limit=500, debug=True)
    captured = capsys.readouterr()

    assert code == 3
    assert "INTERNAL_ERROR" in captured.out
    assert "Traceback" in captured.err


def test_parse_args_supports_debug_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["verify_decision_linkage_integrity.py", "--limit", "42", "--debug"],
    )

    args = script._parse_args()

    assert isinstance(args, SimpleNamespace) is False
    assert args.limit == 42
    assert args.debug is True
