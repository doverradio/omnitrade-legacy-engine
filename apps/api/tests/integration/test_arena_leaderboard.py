from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.arena_comparison_record import ArenaComparisonRecord
from app.models.arena_leaderboard_snapshot import ArenaLeaderboardSnapshot
from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.audit_log import AuditLog
from app.services.arena.contracts import ArenaLeaderboardFilterContract, ArenaLeaderboardSnapshotRequest
from app.services.arena.leaderboard import (
    build_arena_leaderboard_snapshot,
    read_latest_arena_leaderboard_snapshot,
)


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _BeginContext:
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.performance_snapshots: list[ArenaPerformanceSnapshot] = []
        self.comparison_records: list[ArenaComparisonRecord] = []
        self.leaderboard_snapshots: list[ArenaLeaderboardSnapshot] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_leaderboard_snapshots" in sql:
            key = params.get("idempotency_key_1")
            for item in self.leaderboard_snapshots:
                if item.idempotency_key == key:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_performance_snapshots" in sql:
            competition_id = params.get("competition_id_1")
            return _ExecuteResult(
                [item for item in self.performance_snapshots if item.competition_id == competition_id]
            )

        if "FROM arena_comparison_records" in sql:
            competition_id = params.get("competition_id_1")
            return _ExecuteResult(
                [item for item in self.comparison_records if item.competition_id == competition_id]
            )

        if "FROM arena_leaderboard_snapshots" in sql:
            competition_id = params.get("competition_id_1")
            return _ExecuteResult(
                [item for item in self.leaderboard_snapshots if item.competition_id == competition_id]
            )

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaLeaderboardSnapshot):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.leaderboard_snapshots.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_build_and_read_latest_leaderboard_snapshot() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()

    session.performance_snapshots.append(
        ArenaPerformanceSnapshot(
            id=uuid.uuid4(),
            idempotency_key="perf",
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            snapshot_scope="cycle",
            snapshot_input_hash="perf-hash",
            snapshot_payload={
                "agent_summaries": [
                    {
                        "agent_id": str(agent_a),
                        "profit": {"value": "40", "status": "available", "reason": None},
                        "drawdown": {"value": "0.4", "status": "available", "reason": None},
                        "fee_drag": {"value": "4", "status": "available", "reason": None},
                        "consistency": {"value": "0.3", "status": "available", "reason": None},
                        "risk_discipline": {"value": "0.2", "status": "available", "reason": None},
                    },
                    {
                        "agent_id": str(agent_b),
                        "profit": {"value": "20", "status": "available", "reason": None},
                        "drawdown": {"value": "0.1", "status": "available", "reason": None},
                        "fee_drag": {"value": "1", "status": "available", "reason": None},
                        "consistency": {"value": "0.9", "status": "available", "reason": None},
                        "risk_discipline": {"value": "0.9", "status": "available", "reason": None},
                    },
                ]
            },
            provenance={"source": "integration"},
            created_at=datetime(2026, 7, 7, 9, tzinfo=timezone.utc),
        )
    )
    session.comparison_records.append(
        ArenaComparisonRecord(
            id=uuid.uuid4(),
            idempotency_key="cmp",
            comparison_hash="cmp-hash",
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            comparison_scope="cycle",
            compared_agent_ids=[str(agent_a), str(agent_b)],
            comparison_payload={
                "agent_summaries": [
                    {
                        "agent_id": str(agent_a),
                        "decision_quality": {"value": "0.2", "status": "available", "reason": None},
                        "explainability_support_ratio": {
                            "value": "0.2",
                            "status": "available",
                            "reason": None,
                        },
                    },
                    {
                        "agent_id": str(agent_b),
                        "decision_quality": {"value": "0.8", "status": "available", "reason": None},
                        "explainability_support_ratio": {
                            "value": "0.8",
                            "status": "available",
                            "reason": None,
                        },
                    },
                ]
            },
            evidence_sources={},
            provenance={"source": "integration"},
            comparison_timestamp=datetime(2026, 7, 7, 9, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 7, 9, tzinfo=timezone.utc),
        )
    )

    request = ArenaLeaderboardSnapshotRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        ranking_methodology_version="v1",
        filters=ArenaLeaderboardFilterContract(
            included_agent_ids=None,
            limit=1,
            availability_mode="all",
        ),
        as_of=datetime(2026, 7, 7, 10, tzinfo=timezone.utc),
        actor="arena.integration",
        provenance={"ticket": "ARENA-88"},
    )

    built = await build_arena_leaderboard_snapshot(db=session, request=request)

    read_back = await read_latest_arena_leaderboard_snapshot(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        filters=ArenaLeaderboardFilterContract(
            included_agent_ids=None,
            limit=1,
            availability_mode="all",
        ),
    )

    assert built.entries[0].agent_id == agent_b
    assert read_back is not None
    assert read_back.leaderboard_snapshot_id == built.leaderboard_snapshot_id
    assert read_back.ranking_hash == built.ranking_hash
    assert read_back.entries[0].rank == 1
    assert len(read_back.entries) == 1
    assert session.audit_logs
