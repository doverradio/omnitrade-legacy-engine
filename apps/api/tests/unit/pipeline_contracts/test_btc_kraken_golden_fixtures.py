from __future__ import annotations

import json
from fnmatch import fnmatchcase
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "pipeline_contracts"
    / "btc_kraken_golden_scenarios.json"
)
EVIDENCE_PATH = FIXTURE_PATH.with_name("FIELD_EVIDENCE.json")
EXPECTED_SCENARIOS = (
    "buy",
    "hold",
    "risk_rejection",
    "risk_resize",
    "governance_rejection",
    "sell",
    "provider_rejection",
    "provider_timeout_before_confirmation",
    "ambiguous_provider_result",
    "partial_or_delayed_order",
    "reconciliation",
    "accounting",
    "controlled_proof",
    "exit_recovery",
)


def _load() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _by_scenario() -> dict[str, dict]:
    payload = _load()
    return {item["scenario"]: item for item in payload["scenarios"]}


def _leaf_paths(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            paths.extend(_leaf_paths(child, child_prefix))
        return paths
    return [prefix]


def test_every_approved_golden_scenario_loads_once() -> None:
    payload = _load()
    names = tuple(item["scenario"] for item in payload["scenarios"])
    assert payload["fixture_version"] == "phase1-commit1-v1"
    assert names == EXPECTED_SCENARIOS
    assert len(names) == len(set(names))


def test_every_fixture_field_has_an_explicit_evidence_classification() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    allowed = set(evidence["allowed_classifications"])
    scenarios = _by_scenario()
    assert set(evidence["scenarios"]) == set(scenarios)

    for name, item in scenarios.items():
        metadata = evidence["scenarios"][name]
        assert metadata["sources"]
        rules = metadata["field_rules"]
        assert rules.get("*") in allowed
        assert set(rules.values()) <= allowed
        for path in _leaf_paths(item):
            matches = [pattern for pattern in rules if fnmatchcase(path, pattern)]
            assert matches, f"{name}.{path} has no evidence classification"
            controlling = max(matches, key=lambda pattern: len(pattern.replace("*", "")))
            assert rules[controlling] in allowed


def test_all_scenarios_have_complete_reviewable_expectation_shapes() -> None:
    required_expected = {
        "action", "reason_code", "explanation", "transitions", "events",
        "provider_submission_count", "provider_outcome", "database_effects", "external_side_effects",
    }
    for name, item in _by_scenario().items():
        assert item["identifiers"], name
        assert item["decimal_fields"], name
        assert required_expected <= set(item["expected"]), name


def test_serialization_is_deterministic() -> None:
    first = _load()
    second = json.loads(json.dumps(first, sort_keys=True, separators=(",", ":")))
    assert second == first
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )


def test_decimals_identifiers_and_timestamps_are_exact() -> None:
    scenarios = _by_scenario()
    for item in scenarios.values():
        observed = datetime.fromisoformat(item["occurred_at"])
        assert observed.tzinfo is not None
        assert observed.isoformat() == item["occurred_at"]
        for name, value in item["decimal_fields"].items():
            assert isinstance(value, str), name
            assert format(Decimal(value), "f") == value
        for name, value in item["identifiers"].items():
            if name.endswith("_id") and not name.startswith("provider_"):
                assert str(UUID(value)) == value

    assert scenarios["buy"]["decimal_fields"]["base_quantity"] == "0.00005000"
    assert scenarios["accounting"]["decimal_fields"]["net_cash_impact"] == "-5.01250000"
    assert scenarios["exit_recovery"]["identifiers"]["recovery_id"] == "f0000000-0000-4000-8000-000000000014"


def test_actions_reasons_transitions_and_event_order_are_frozen() -> None:
    scenarios = _by_scenario()
    assert scenarios["buy"]["expected"]["action"] == "BUY"
    assert scenarios["hold"]["expected"]["reason_code"] == "hold_action"
    assert scenarios["risk_rejection"]["expected"]["reason_code"] == "risk_veto"
    assert scenarios["risk_resize"]["expected"]["reason_code"] == "position_resized_by_risk_engine"
    assert scenarios["governance_rejection"]["expected"]["reason_code"] == "active_mandate_policy_requires_authorized_version"
    assert scenarios["provider_rejection"]["expected"]["transitions"] == ["BUY_PENDING", "CANCELLED"]
    assert scenarios["reconciliation"]["expected"]["events"] == ["order_reconciled", "fill_reconciled"]
    assert scenarios["accounting"]["expected"]["events"] == ["fill_reconciled", "accounting_recorded", "fee_attributed"]


def test_provider_call_expectations_and_ambiguous_retry_policy_are_frozen() -> None:
    scenarios = _by_scenario()
    assert scenarios["buy"]["expected"]["provider_submission_count"] == 1
    assert scenarios["sell"]["expected"]["provider_submission_count"] == 1
    for name in ("hold", "risk_rejection", "governance_rejection"):
        assert scenarios[name]["expected"]["provider_submission_count"] == 0
    for name in ("provider_timeout_before_confirmation", "ambiguous_provider_result"):
        expected = scenarios[name]["expected"]
        assert expected["provider_submission_count"] == 1
        assert "reconcile" in expected["retry_policy"] or "lookup" in expected["retry_policy"]
        assert "resubmit" in expected["retry_policy"]


def test_reconciliation_accounting_and_synthetic_lineage_are_explicit() -> None:
    scenarios = _by_scenario()
    assert scenarios["reconciliation"]["expected"]["idempotency"] == {
        "duplicate_fill_replays_existing_ids": True,
        "terminal_state_must_not_regress": True,
    }
    accounting = scenarios["accounting"]
    assert accounting["expected"]["idempotency"]["duplicate_fill_replays_existing_ids"] is True
    assert set(accounting["lineage_authority"].values()) == {
        "synthetic_non_authoritative_when_upstream_missing"
    }


def test_controlled_proof_and_exit_recovery_are_fake_provider_expectations() -> None:
    scenarios = _by_scenario()
    proof = scenarios["controlled_proof"]["expected"]
    recovery = scenarios["exit_recovery"]["expected"]
    assert proof["provider_outcome"] == "fake_provider_buy_and_sell_acknowledged"
    assert proof["provider_submission_count"] == 2
    assert recovery["provider_outcome"] == "fake_provider_acknowledged"
    assert recovery["provider_submission_count"] == 1
    assert all(side_effect.startswith("fake_kraken_") for side_effect in proof["external_side_effects"])
    assert all(side_effect.startswith("fake_kraken_") for side_effect in recovery["external_side_effects"])
