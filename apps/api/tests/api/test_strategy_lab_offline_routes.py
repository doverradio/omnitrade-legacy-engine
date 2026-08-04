from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.strategy_lab_offline import load_dataset
from app.services import strategy_lab_offline
from app.services import rule_discovery


def test_lists_cached_offline_datasets() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/strategy-lab/datasets")

    assert response.status_code == 200
    dataset = response.json()["items"][0]
    assert dataset["id"] == "btc_15m"
    assert dataset["candle_count"] == 2818
    assert dataset["exchange"] == "offline_csv"


def test_replays_strategy_002_deterministically() -> None:
    payload = {"dataset_id": "btc_15m", "strategy_version": "002"}
    with TestClient(create_app()) as client:
        first = client.post("/strategy-lab/replay", json=payload)
        second = client.post("/strategy-lab/replay", json=payload)

    assert first.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert len(body["candles"]) == 2818
    assert body["trades"]
    assert body["events"]
    assert body["metrics"]["verdict"] == "UNPROFITABLE"


def test_replays_selected_period_and_strategy_001() -> None:
    candles = load_dataset("btc_15m")
    end = candles[0].timestamp + timedelta(hours=12)
    payload = {
        "dataset_id": "btc_15m",
        "strategy_version": "001",
        "start_time": candles[0].timestamp.isoformat(),
        "end_time": end.isoformat(),
        "research_period": "validation",
    }
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/replay", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["strategy_version"] == "001"
    assert body["dataset"]["research_period"] == "validation"
    assert len(body["candles"]) == 49


def test_rejects_invalid_profit_allocation() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/strategy-lab/replay",
            json={
                "dataset_id": "btc_15m",
                "parameters": {
                    "profit_compound_pct": "80",
                    "profit_withdrawal_pct": "30",
                },
            },
        )

    assert response.status_code == 422


def test_missing_dataset_returns_not_found() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/replay", json={"dataset_id": "missing"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_validates_csv_quality_without_writing() -> None:
    csv_text = "timestamp,open,high,low,close,volume\n2026-01-01T00:00:00Z,10,12,9,11,5\n2026-01-01T00:30:00Z,11,13,10,12,6\n"
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/datasets/validate", json={"csv_text": csv_text, "interval": "15m"})

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "required_columns": ["timestamp", "open", "high", "low", "close", "volume"],
        "missing_columns": [],
        "total_rows": 2,
        "candle_count": 2,
        "first_timestamp": "2026-01-01T00:00:00Z",
        "last_timestamp": "2026-01-01T00:30:00Z",
        "missing_candles": 1,
        "duplicate_timestamps": 0,
        "invalid_rows": 0,
        "errors": [],
    }


def test_creates_normalized_immutable_dataset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strategy_lab_offline, "_DATASET_ROOT", tmp_path)
    csv_text = "volume,close,low,high,open,timestamp\n6,12,10,13,11,2026-01-01T00:15:00Z\n5,11,9,12,10,2026-01-01T00:00:00Z\n"
    payload = {"csv_text": csv_text, "asset": "ETH", "exchange": "research", "interval": "15m", "name": "ETH Study"}

    with TestClient(create_app()) as client:
        created = client.post("/strategy-lab/datasets", json=payload)
        listed = client.get("/strategy-lab/datasets")
        duplicate = client.post("/strategy-lab/datasets", json=payload)

    assert created.status_code == 201
    dataset = created.json()
    assert dataset["id"].startswith("eth_study_")
    assert dataset["asset"] == "ETH"
    assert listed.json()["items"] == [dataset]
    assert duplicate.status_code == 409
    normalized = (tmp_path / f"{dataset['id']}.csv").read_text()
    assert normalized.splitlines()[0] == "timestamp,open,high,low,close,volume"
    assert normalized.splitlines()[1].startswith("2026-01-01T00:00:00+00:00")


def test_rejects_invalid_or_duplicate_csv_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strategy_lab_offline, "_DATASET_ROOT", tmp_path)
    csv_text = "timestamp,open,high,low,close,volume\n2026-01-01T00:00:00Z,10,12,9,11,5\n2026-01-01T00:00:00Z,10,8,9,11,5\n"
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/datasets", json={"csv_text": csv_text, "asset": "ETH", "exchange": "research", "interval": "15m", "name": "Bad"})

    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert details["invalid_rows"] == 1


def test_pattern_intelligence_analyzes_selection_deterministically() -> None:
    payload = {"dataset_id": "btc_15m", "selected_start_index": 100, "selected_end_index": 140, "partition": "training"}
    with TestClient(create_app()) as client:
        first = client.post("/strategy-lab/pattern-intelligence/analyze-selection", json=payload)
        second = client.post("/strategy-lab/pattern-intelligence/analyze-selection", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["content_hash"] == second_body["content_hash"]
    assert first_body["selected_range"] == [100, 140]
    assert first_body["findings"]
    assert len(first_body["annotations"]) == len(first_body["findings"])
    assert all(item["conditions"] and item["thresholds"] for item in first_body["findings"])


def test_pattern_intelligence_analyzes_completed_trade() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/pattern-intelligence/analyze-trade", json={"dataset_id": "btc_15m", "trade_index": 0})

    assert response.status_code == 200
    assert response.json()["selected_range"][0] <= response.json()["selected_range"][1]
    assert any(item["group"] == "Strategy Behavior" for item in response.json()["findings"])


def test_pattern_intelligence_dataset_identifier_is_path_safe() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/pattern-intelligence/analyze-selection", json={"dataset_id": "../btc_15m"})

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid dataset identifier"


def test_research_copilot_explains_selection_deterministically() -> None:
    payload = {"dataset_id": "btc_15m", "selected_start_index": 2565, "selected_end_index": 2584}
    with TestClient(create_app()) as client:
        first = client.post("/strategy-lab/research-copilot/explain-selection", json=payload)
        second = client.post("/strategy-lab/research-copilot/explain-selection", json=payload)

    assert first.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    valid_ids = {finding["finding_id"] for finding in body["evidence"]["source_findings"]}
    assert body["provider"] == "DeterministicTemplateProvider"
    assert all(set(statement["source_finding_ids"]) <= valid_ids for statement in body["statements"])
    assert all(statement["measurements"] for statement in body["statements"] if statement["label"] == "OBSERVATION")


def test_research_copilot_explains_real_losing_trade_and_gates_counterfactual() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/research-copilot/explain-trade", json={"dataset_id": "btc_15m", "trade_index": 0})

    assert response.status_code == 200
    body = response.json()
    assert body["primary_cause"]["classification"] != "INSUFFICIENT_EVIDENCE"
    assert body["counterfactual_improvement"] is None
    assert any(statement["section"] == "COUNTERFACTUAL IMPROVEMENT" and statement["label"] == "INSUFFICIENT EVIDENCE" for statement in body["statements"])


def test_research_copilot_missed_requires_replay_evidence() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/research-copilot/show-missed", json={"dataset_id": "btc_15m", "selected_start_index": 180, "selected_end_index": 188})

    assert response.status_code == 200
    body = response.json()
    missed_ids = {finding["finding_id"] for finding in body["evidence"]["source_findings"] if finding["detector_id"] == "missed_entry_v1"}
    assert missed_ids
    assert any(missed_ids.intersection(statement["source_finding_ids"]) for statement in body["statements"])
    assert body["candidate_experiments"][0]["executable_rule"] is False


def test_research_copilot_overfitting_and_path_safety() -> None:
    with TestClient(create_app()) as client:
        warning = client.post("/strategy-lab/research-copilot/overfitting-warnings", json={"dataset_id": "btc_15m", "selected_start_index": 100, "selected_end_index": 140, "final_test_used_for_development": True})
        unsafe = client.post("/strategy-lab/research-copilot/explain-selection", json={"dataset_id": "../btc_15m"})

    assert warning.status_code == 200
    assert any(item["section"] == "FINAL TEST CONTAMINATION" for item in warning.json()["statements"])
    assert unsafe.status_code == 400
    assert unsafe.json()["error"]["message"] == "Invalid dataset identifier"


def test_research_copilot_does_not_modify_strategy_files() -> None:
    root = Path(__file__).resolve().parents[4]
    paths = [
        root / "strategy_lab/strategies/trailing_limit_v1.py",
        root / "strategy_lab/strategies/trailing_limit_v2.py",
    ]
    before = {path: path.read_bytes() for path in paths}

    with TestClient(create_app()) as client:
        response = client.post("/strategy-lab/research-copilot/explain-selection", json={"dataset_id": "btc_15m", "selected_start_index": 74, "selected_end_index": 77})

    assert response.status_code == 200
    assert {path: path.read_bytes() for path in paths} == before


def _ce_001_rule_payload() -> dict:
    return {
        "name": "Block entries during negative short-window momentum",
        "description": "CE-001 controlled entry gate derived from the linked negative-slope evidence.",
        "source_analysis_id": "analysis_ce_001_btc",
        "source_finding_ids": ["finding_0001"],
        "source_candidate_experiment_id": "CE-001",
        "parent_strategy_version": "002",
        "rule_document": {
            "schema_version": "1.0.0",
            "when": {"all": [{"feature": "short_window_momentum", "operator": "<", "value": "0", "lookback": 3}]},
            "then": {"action": "BLOCK_LONG_ENTRY"},
            "risk_controls": {"minimum_occurrences": 5, "maximum_drawdown_pct": "10", "final_test_used_for_tuning": False},
        },
        "created_by": "human_with_copilot",
        "research_notes": "Review required before branch creation.",
        "evidence": {
            "suggested_by": "Research Copilot",
            "detector_versions": {"negative_slope_v1": "1.0.0"},
            "measurements": {"least_squares_slope": "-228.1542857142857142857142857"},
            "why": "The linked loss explanation classified negative slope before a declining-close exit.",
        },
    }


def test_rule_discovery_full_local_workflow_is_idempotent_and_hashed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rule_discovery, "_ARTIFACT_ROOT", tmp_path)
    root = Path(__file__).resolve().parents[4]
    strategy_paths = [
        root / "strategy_lab/strategies/trailing_limit_v1.py",
        root / "strategy_lab/strategies/trailing_limit_v2.py",
    ]
    parent_before = {path: path.read_bytes() for path in strategy_paths}

    with TestClient(create_app()) as client:
        created = client.post("/api/v1/strategy-lab/rules", json=_ce_001_rule_payload())
        repeated = client.post("/api/v1/strategy-lab/rules", json=_ce_001_rule_payload())
        assert created.status_code == 201
        assert repeated.status_code == 201
        assert created.json() == repeated.json()
        rule_id = created.json()["candidate_rule_id"]
        assert client.get("/api/v1/strategy-lab/rules").json()["items"][0]["candidate_rule_id"] == rule_id
        assert client.post(f"/api/v1/strategy-lab/rules/{rule_id}/validate").json()["valid"] is True

        branch = client.post(f"/api/v1/strategy-lab/rules/{rule_id}/create-branch")
        repeated_branch = client.post(f"/api/v1/strategy-lab/rules/{rule_id}/create-branch")
        assert branch.status_code == 200
        assert branch.json() == repeated_branch.json()
        branch_id = branch.json()["strategy_branch_id"]

        reports = {}
        for partition in ("training", "validation", "final_test", "entire_dataset"):
            response = client.post(f"/api/v1/strategy-lab/branches/{branch_id}/replay", json={"dataset_id": "btc_15m", "partition": partition})
            assert response.status_code == 200
            reports[partition] = response.json()
        assert reports["training"]["rule_events"]
        assert reports["training"]["rule_match_count"] >= reports["training"]["rule_action_count"]

        comparison = client.get(f"/api/v1/strategy-lab/branches/{branch_id}/comparison", params={"dataset_id": "btc_15m"})
        package = client.get(f"/api/v1/strategy-lab/branches/{branch_id}/package", params={"dataset_id": "btc_15m"})
        package_again = client.get(f"/api/v1/strategy-lab/branches/{branch_id}/package", params={"dataset_id": "btc_15m"})
        assert comparison.status_code == 200
        assert set(comparison.json()["reports"]) == {"training", "validation", "final_test", "entire_dataset"}
        assert package.status_code == 200
        assert package.json()["strategy_package"]["content_hash"] == package_again.json()["strategy_package"]["content_hash"]
        assert package.json()["certificate"]["strategy_package_content_hash"] == package.json()["strategy_package"]["content_hash"]

    assert {path: path.read_bytes() for path in strategy_paths} == parent_before
    assert (tmp_path / "rules" / f"{rule_id}.json").is_file()
    assert (tmp_path / "branches" / f"{branch_id}.json").is_file()


def test_rule_discovery_rejects_code_lookahead_and_unsafe_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rule_discovery, "_ARTIFACT_ROOT", tmp_path)
    code_payload = _ce_001_rule_payload()
    code_payload["rule_document"]["python"] = "__import__('os').system('id')"
    lookahead_payload = _ce_001_rule_payload()
    lookahead_payload["rule_document"]["when"] = {"all": [{"feature": "close", "operator": ">", "reference": "next_close"}]}
    with TestClient(create_app()) as client:
        code = client.post("/api/v1/strategy-lab/rules", json=code_payload)
        lookahead = client.post("/api/v1/strategy-lab/rules", json=lookahead_payload)
        unsafe_rule = client.get("/api/v1/strategy-lab/rules/..%2Fsecret")
        unsafe_branch = client.get("/api/v1/strategy-lab/branches/..%2Fsecret/comparison", params={"dataset_id": "btc_15m"})
    assert code.status_code == 400
    assert lookahead.status_code == 400
    assert unsafe_rule.status_code in {400, 404}
    assert unsafe_branch.status_code in {400, 404}