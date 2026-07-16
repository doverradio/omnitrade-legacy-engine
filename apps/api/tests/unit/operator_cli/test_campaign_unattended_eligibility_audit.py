from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.operator_cli.service as service


def _definition(*, campaign_id: UUID, version: int = 1, status: str = "READY", instruments: list[str] | None = None, venues: list[str] | None = None, metadata_evidence: dict | None = None):
    return SimpleNamespace(
        campaign_id=campaign_id,
        version=version,
        status=status,
        allowed_instruments=instruments if instruments is not None else ["BTC-USD"],
        allowed_venues=venues if venues is not None else ["kraken_spot"],
        activated_at=None,
        paused_at=None,
        completed_at=None,
        campaign_modes=["AUTONOMOUS"],
        metadata_evidence=metadata_evidence if metadata_evidence is not None else {},
    )


def _runtime(*, campaign_id: UUID, definition_version: int = 1):
    return SimpleNamespace(
        id=2,
        uuid=campaign_id,
        definition_version=definition_version,
        status="READY",
        paper_account_id=UUID("8e76a2fa-ae85-45c6-95d1-798cce8f8cc9"),
        starting_capital=Decimal("100"),
        current_equity=Decimal("100"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        roi=Decimal("0"),
        definition_campaign_id=campaign_id,
    )


def _build_payload(
    *,
    definition=None,
    available_versions=None,
    latest_version=None,
    runtime_exact=None,
    runtime_linked=None,
    unattended_considered=None,
    unattended_eligible=None,
    unattended_skipped=None,
):
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    campaign_version = 1
    return service._build_campaign_unattended_eligibility_audit_payload(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        definition=definition,
        available_versions=[1] if available_versions is None else available_versions,
        latest_version=1 if latest_version is None else latest_version,
        runtime_exact=runtime_exact,
        runtime_linked=runtime_linked,
        unattended_considered=[{"campaign_id": str(campaign_id), "version": 1, "status": "READY"}] if unattended_considered is None else unattended_considered,
        unattended_eligible=[{"campaign_id": str(campaign_id), "version": 1, "status": "READY"}] if unattended_eligible is None else unattended_eligible,
        unattended_skipped=[] if unattended_skipped is None else unattended_skipped,
    )


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (_build_payload(definition=None, available_versions=[], latest_version=None, runtime_exact=None, unattended_considered=[], unattended_eligible=[]), "DEFINITION_ROW_MISSING"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")), latest_version=2, unattended_eligible=[]), "REQUESTED_VERSION_NOT_LATEST"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")), runtime_exact=None, unattended_eligible=[]), "RUNTIME_CAMPAIGN_MISSING"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")), runtime_exact=None, runtime_linked=SimpleNamespace(id=9, uuid=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), definition_campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=1), unattended_eligible=[]), "RUNTIME_UUID_LINK_MISMATCH"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")), runtime_exact=_runtime(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=2), unattended_eligible=[]), "RUNTIME_DEFINITION_VERSION_MISMATCH"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), status="COMPLETED"), runtime_exact=_runtime(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=1), unattended_eligible=[]), "CAMPAIGN_STATUS_INELIGIBLE"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), status="DRAFT"), runtime_exact=_runtime(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=1), unattended_eligible=[]), "DRAFT_EXCLUDED_FROM_UNATTENDED_MODE"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), instruments=["ETH-USD"]), runtime_exact=_runtime(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=1), unattended_eligible=[]), "PRODUCT_NOT_ALLOWED"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), venues=["coinbase"]), runtime_exact=_runtime(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=1), unattended_eligible=[]), "PROVIDER_NOT_ALLOWED"),
        (_build_payload(definition=_definition(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), metadata_evidence={"effective_end_at": "2001-01-01T00:00:00+00:00"}), runtime_exact=_runtime(campaign_id=UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b"), definition_version=1), unattended_eligible=[]), "CAMPAIGN_OUTSIDE_EFFECTIVE_WINDOW"),
    ],
)
def test_campaign_unattended_eligibility_root_cause_codes(payload, expected_code) -> None:
    assert payload["root_cause_code"] == expected_code


def test_campaign_unattended_eligibility_eligible_path() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    payload = _build_payload(
        definition=_definition(campaign_id=campaign_id, status="READY"),
        runtime_exact=_runtime(campaign_id=campaign_id, definition_version=1),
        unattended_eligible=[{"campaign_id": str(campaign_id), "version": 1, "status": "READY"}],
    )
    assert payload["root_cause_code"] == "ELIGIBLE"
    assert payload["unattended_scan"]["would_appear_in_unattended_candidate_list_today"] is True


def test_campaign_unattended_eligibility_other_code_when_unresolved() -> None:
    campaign_id = UUID("e9a9e8e9-9574-498d-b49e-f011218c7f2b")
    payload = _build_payload(
        definition=_definition(campaign_id=campaign_id, status="READY"),
        runtime_exact=_runtime(campaign_id=campaign_id, definition_version=1),
        unattended_eligible=[],
    )
    assert payload["root_cause_code"].startswith("OTHER:")


def test_campaign_unattended_eligibility_command_is_read_only() -> None:
    source = service.campaign_unattended_eligibility_audit.__code__.co_names
    assert "commit" not in source
    assert "add" not in source
    assert "create_canonical_preview_package" not in source
    assert "authorize_canonical_preview_package" not in source
    assert "activate_canonical_proving_campaign" not in source
