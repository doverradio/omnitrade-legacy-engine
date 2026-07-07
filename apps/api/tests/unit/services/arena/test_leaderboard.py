from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_comparison_record import ArenaComparisonRecord
from app.models.arena_leaderboard_snapshot import (
    ArenaLeaderboardSnapshot,
    _prevent_arena_leaderboard_snapshot_delete,
    _prevent_arena_leaderboard_snapshot_update,
)
from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.audit_log import AuditLog
from app.services.arena.contracts import (
    ArenaLeaderboardFilterContract,
    ArenaLeaderboardSnapshotRequest,
)
from app.services.arena.leaderboard import build_arena_leaderboard_snapshot


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
            rows = [item for item in self.performance_snapshots if item.competition_id == competition_id]
            return _ExecuteResult(rows)

        if "FROM arena_comparison_records" in sql:
            competition_id = params.get("competition_id_1")
            rows = [item for item in self.comparison_records if item.competition_id == competition_id]
            return _ExecuteResult(rows)

        if "FROM arena_leaderboard_snapshots" in sql:
            competition_id = params.get("competition_id_1")
            rows = [item for item in self.leaderboard_snapshots if item.competition_id == competition_id]
            return _ExecuteResult(rows)

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


def _performance_snapshot(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_id: uuid.UUID,
    agent_a: uuid.UUID,
    agent_b: uuid.UUID,
) -> ArenaPerformanceSnapshot:
    return ArenaPerformanceSnapshot(
        id=uuid.uuid4(),
        idempotency_key="perf-1",
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        snapshot_scope="cycle",
        snapshot_input_hash="perf-hash",
        snapshot_payload={
            "agent_summaries": [
                {
                    "agent_id": str(agent_a),
                    "profit": {"value": "100", "status": "available", "reason": None},
                    "drawdown": {"value": "0.5000", "status": "available", "reason": None},
                    "fee_drag": {"value": "10", "status": "available", "reason": None},
                    "consistency": {"value": "0.2000", "status": "available", "reason": None},
                    "risk_discipline": {"value": "0.1000", "status": "available", "reason": None},
                },
                {
                    "agent_id": str(agent_b),
                    "profit": {"value": "20", "status": "available", "reason": None},
                    "drawdown": {"value": "0.1000", "status": "available", "reason": None},
                    "fee_drag": {"value": "1", "status": "available", "reason": None},
                    "consistency": {"value": "0.8000", "status": "available", "reason": None},
                    "risk_discipline": {"value": "0.9000", "status": "available", "reason": None},
                },
            ]
        },
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 7, 7, 10, tzinfo=timezone.utc),
    )


def _comparison_record(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_id: uuid.UUID,
    agent_a: uuid.UUID,
    agent_b: uuid.UUID,
) -> ArenaComparisonRecord:
    return ArenaComparisonRecord(
        id=uuid.uuid4(),
        idempotency_key="cmp-1",
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
                    "decision_quality": {"value": "0.1000", "status": "available", "reason": None},
                    "explainability_support_ratio": {
                        "value": "0.1000",
                        "status": "available",
                        "reason": None,
                    },
                },
                {
                    "agent_id": str(agent_b),
                    "decision_quality": {"value": "0.9000", "status": "available", "reason": None},
                    "explainability_support_ratio": {
                        "value": "0.9000",
                        "status": "available",
                        "reason": None,
                    },
                },
            ]
        },
        evidence_sources={"source": "comparison"},
        provenance={"source": "unit-test"},
        comparison_timestamp=datetime(2026, 7, 7, 10, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 7, 10, tzinfo=timezone.utc),
    )


def _request(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_id: uuid.UUID,
    availability_mode: str = "all",
    included_agent_ids: list[uuid.UUID] | None = None,
    limit: int | None = None,
) -> ArenaLeaderboardSnapshotRequest:
    return ArenaLeaderboardSnapshotRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        ranking_methodology_version="v1",
        filters=ArenaLeaderboardFilterContract(
            included_agent_ids=included_agent_ids,
            limit=limit,
            availability_mode=availability_mode,
        ),
        as_of=datetime(2026, 7, 7, 12, tzinfo=timezone.utc),
        actor="arena.leaderboard",
        provenance={"ticket": "ARENA-88"},
    )


@pytest.mark.asyncio
async def test_leaderboard_deterministic_and_not_profit_only() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()

    session.performance_snapshots.append(
        _performance_snapshot(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            agent_a=agent_a,
            agent_b=agent_b,
        )
    )
    session.comparison_records.append(
        _comparison_record(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            agent_a=agent_a,
            agent_b=agent_b,
        )
    )

    first = await build_arena_leaderboard_snapshot(
        db=session,
        request=_request(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
        ),
    )
    second = await build_arena_leaderboard_snapshot(
        db=session,
        request=_request(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
        ),
    )

    assert first.leaderboard_snapshot_id == second.leaderboard_snapshot_id
    assert first.ranking_hash == second.ranking_hash
    assert len(session.leaderboard_snapshots) == 1

    assert first.entries[0].agent_id == agent_b
    assert first.entries[0].profit.value == Decimal("20")
    assert first.entries[1].agent_id == agent_a
    assert first.entries[1].profit.value == Decimal("100")


@pytest.mark.asyncio
async def test_leaderboard_filters_apply_rank_reindex_and_known_only() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    unknown_agent = uuid.uuid4()

    session.comparison_records.append(
        ArenaComparisonRecord(
            id=uuid.uuid4(),
            idempotency_key="cmp-unknown",
            comparison_hash="cmp-hash-unknown",
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            comparison_scope="cycle",
            compared_agent_ids=[str(unknown_agent)],
            comparison_payload={
                "agent_summaries": [
                    {
                        "agent_id": str(unknown_agent),
                        "decision_quality": {"value": None, "status": "unknown", "reason": "missing"},
                        "explainability_support_ratio": {
                            "value": None,
                            "status": "unknown",
                            "reason": "missing",
                        },
                    }
                ]
            },
            evidence_sources={},
            provenance={},
            comparison_timestamp=datetime(2026, 7, 7, 9, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 7, 9, tzinfo=timezone.utc),
        )
    )

    result = await build_arena_leaderboard_snapshot(
        db=session,
        request=_request(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            availability_mode="known_only",
            included_agent_ids=[unknown_agent],
            limit=10,
        ),
    )

    assert result.entries == []
    assert result.filters.included_agent_ids == [unknown_agent]
    assert result.filters.availability_mode == "known_only"
    assert result.provenance["observational_only"] is True


def test_leaderboard_snapshot_model_is_append_only() -> None:
    with pytest.raises(
        ValueError,
        match="arena_leaderboard_snapshots is append-only and does not support updates",
    ):
        _prevent_arena_leaderboard_snapshot_update(None, None, None)

    with pytest.raises(
        ValueError,
        match="arena_leaderboard_snapshots is append-only and does not support deletes",
    ):
        _prevent_arena_leaderboard_snapshot_delete(None, None, None)
