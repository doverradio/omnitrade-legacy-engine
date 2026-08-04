from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.strategy_lab_offline import load_dataset
from app.services import strategy_lab_offline


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