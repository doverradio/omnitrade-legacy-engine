from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.services.orchestration import automatic_package_executor as executor


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows(self.rows)


def _package(state: str = "READY") -> SimpleNamespace:
    return SimpleNamespace(
        package_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        campaign_version=3,
        decision_record_id=uuid.uuid4(),
        mandate_id=None if state == "READY" else uuid.uuid4(),
        authorization_source=None if state == "READY" else "MANDATE",
        package_state=state,
        dry_run_live_crypto_order_id=uuid.uuid4() if state in {"DRY_RUN_PASSED", "ACTIVATED"} else None,
        generated_at=datetime.now(timezone.utc),
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def _request(package: SimpleNamespace, *, include_package_id: bool = True) -> executor.AutomaticPackageExecutionRequest:
    return executor.AutomaticPackageExecutionRequest(
        campaign_id=package.campaign_id,
        campaign_version=package.campaign_version,
        decision_record_id=package.decision_record_id,
        package_id=package.package_id if include_package_id else None,
    )


def _enable(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: SimpleNamespace(
            automatic_mandate_package_activation_enabled=enabled,
            automatic_mandate_package_activation_package_id=None,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_state", "expected_calls"),
    [
        ("READY", ["authorize", "dry_run", "activate"]),
        ("AUTHORIZED", ["dry_run", "activate"]),
        ("DRY_RUN_PASSED", ["activate"]),
    ],
)
async def test_executor_resumes_each_state_through_activation(
    monkeypatch: pytest.MonkeyPatch, initial_state: str, expected_calls: list[str],
) -> None:
    _enable(monkeypatch)
    package = _package(initial_state)
    calls: list[str] = []

    async def _authorize(*, db, request):
        calls.append("authorize")
        package.package_state = "AUTHORIZED"
        package.authorization_source = "MANDATE"
        package.mandate_id = uuid.uuid4()
        return {}

    async def _dry_run(*, db, request):
        calls.append("dry_run")
        package.package_state = "DRY_RUN_PASSED"
        package.dry_run_live_crypto_order_id = uuid.uuid4()
        return {}

    async def _activate(*, db, request):
        calls.append("activate")
        package.package_state = "ACTIVATED"
        return {}

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _authorize)
    monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _dry_run)
    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _activate)

    db = _Db([package])
    outcome = await executor.execute_automatic_ready_package_through_activation(db=db, request=_request(package))

    assert calls == expected_calls
    assert outcome.activation_state == "ACTIVATED"
    assert outcome.failed_closed is False
    assert outcome.authority_source == "MANDATE"
    assert "FOR UPDATE" in str(db.statements[0]).upper()


@pytest.mark.asyncio
async def test_executor_activated_replay_creates_no_new_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    package = _package("ACTIVATED")
    calls = {"validate": 0}

    async def _validate(**kwargs):
        calls["validate"] += 1
        return SimpleNamespace(source="MANDATE")

    monkeypatch.setattr(executor, "_validate_canonical_package_authority", _validate)
    outcome = await executor.execute_automatic_ready_package_through_activation(db=_Db([package]), request=_request(package))

    assert outcome.replayed is True
    assert outcome.final_reason_code == "already_activated"
    assert calls == {"validate": 1}


@pytest.mark.asyncio
async def test_executor_resolves_existing_package_without_package_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    package = _package("DRY_RUN_PASSED")

    async def _activate(*, db, request):
        package.package_state = "ACTIVATED"
        return {}

    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _activate)
    outcome = await executor.execute_automatic_ready_package_through_activation(
        db=_Db([package]), request=_request(package, include_package_id=False),
    )
    assert outcome.package_id == package.package_id
    assert outcome.activation_state == "ACTIVATED"


@pytest.mark.asyncio
async def test_executor_feature_flag_and_package_resolution_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package()
    _enable(monkeypatch, False)
    disabled = await executor.execute_automatic_ready_package_through_activation(db=_Db([package]), request=_request(package))
    assert disabled.final_reason_code == "automatic_mandate_package_activation_disabled"

    _enable(monkeypatch, True)
    missing = await executor.execute_automatic_ready_package_through_activation(db=_Db([]), request=_request(package))
    ambiguous = await executor.execute_automatic_ready_package_through_activation(
        db=_Db([package, _package()]), request=_request(package, include_package_id=False),
    )
    assert missing.final_reason_code == "eligible_package_missing"
    assert missing.failed_closed is True
    assert ambiguous.final_reason_code == "ambiguous_eligible_packages"
    assert ambiguous.failed_closed is True


@pytest.mark.asyncio
async def test_executor_proof_package_pin_rejects_every_other_package(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package()
    pinned_package_id = uuid.uuid4()
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: SimpleNamespace(
            automatic_mandate_package_activation_enabled=True,
            automatic_mandate_package_activation_package_id=pinned_package_id,
        ),
    )

    outcome = await executor.execute_automatic_ready_package_through_activation(
        db=_Db([package]), request=_request(package),
    )

    assert outcome.final_reason_code == "proof_package_pin_mismatch"
    assert outcome.failed_closed is True
    assert outcome.package_id is None


@pytest.mark.asyncio
async def test_executor_proof_package_pin_allows_only_the_captured_package(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package("DRY_RUN_PASSED")
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: SimpleNamespace(
            automatic_mandate_package_activation_enabled=True,
            automatic_mandate_package_activation_package_id=package.package_id,
        ),
    )

    async def _activate(*, db, request):
        package.package_state = "ACTIVATED"
        return {}

    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _activate)
    outcome = await executor.execute_automatic_ready_package_through_activation(
        db=_Db([package]), request=_request(package),
    )

    assert outcome.activation_state == "ACTIVATED"
    assert outcome.failed_closed is False


@pytest.mark.asyncio
async def test_executor_proof_package_pin_resolves_captured_package_after_worker_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package("DRY_RUN_PASSED")
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: SimpleNamespace(
            automatic_mandate_package_activation_enabled=True,
            automatic_mandate_package_activation_package_id=package.package_id,
        ),
    )

    async def _activate(*, db, request):
        package.package_state = "ACTIVATED"
        return {}

    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _activate)
    outcome = await executor.execute_automatic_ready_package_through_activation(
        db=_Db([package]), request=_request(package, include_package_id=False),
    )

    assert outcome.package_id == package.package_id
    assert outcome.activation_state == "ACTIVATED"


@pytest.mark.asyncio
async def test_executor_contains_expected_authority_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    package = _package("READY")

    async def _reject(*, db, request):
        raise PermissionError("ambiguous matching ACTIVE LEVEL_2 mandates")

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _reject)
    outcome = await executor.execute_automatic_ready_package_through_activation(db=_Db([package]), request=_request(package))
    assert outcome.failed_closed is True
    assert "ambiguous" in outcome.final_reason_code
    assert package.package_state == "READY"


@pytest.mark.asyncio
async def test_executor_rejects_package_identity_mismatch_even_after_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    package = _package("READY")
    request = _request(package)
    package.decision_record_id = uuid.uuid4()
    outcome = await executor.execute_automatic_ready_package_through_activation(db=_Db([package]), request=request)
    assert outcome.failed_closed is True
    assert outcome.final_reason_code == "resolved package identity mismatch"


@pytest.mark.asyncio
async def test_repeated_attempt_observes_activated_state_and_does_not_duplicate_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    package = _package("READY")
    counts = {"authorize": 0, "dry": 0, "activate": 0}

    async def _authorize(*, db, request):
        counts["authorize"] += 1
        package.package_state = "AUTHORIZED"
        package.authorization_source = "MANDATE"
        package.mandate_id = uuid.uuid4()
        return {}

    async def _dry(*, db, request):
        counts["dry"] += 1
        package.package_state = "DRY_RUN_PASSED"
        package.dry_run_live_crypto_order_id = uuid.uuid4()
        return {}

    async def _activate(*, db, request):
        counts["activate"] += 1
        package.package_state = "ACTIVATED"
        return {}

    async def _validate(**kwargs):
        return SimpleNamespace(source="MANDATE")

    monkeypatch.setattr(executor, "authorize_canonical_preview_package_under_mandate", _authorize)
    monkeypatch.setattr(executor, "run_dry_run_for_canonical_preview_package", _dry)
    monkeypatch.setattr(executor, "activate_canonical_proving_campaign", _activate)
    monkeypatch.setattr(executor, "_validate_canonical_package_authority", _validate)
    db = _Db([package])
    first = await executor.execute_automatic_ready_package_through_activation(db=db, request=_request(package))
    second = await executor.execute_automatic_ready_package_through_activation(db=db, request=_request(package))
    assert first.activation_state == second.activation_state == "ACTIVATED"
    assert second.replayed is True
    assert counts == {"authorize": 1, "dry": 1, "activate": 1}
