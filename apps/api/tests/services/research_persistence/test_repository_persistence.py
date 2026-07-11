from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import uuid

import pytest

from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.interface import EvolvedCandidate, EvolutionMutation
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun
from app.services.research_persistence.repository import ResearchPersistenceRepository


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.candidates: dict[uuid.UUID, object] = {}
        self.lineage: dict[uuid.UUID, object] = {}
        self.campaigns: dict[uuid.UUID, object] = {}
        self.stats: dict[uuid.UUID, object] = {}

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            campaign_id = getattr(obj, "campaign_id", None)
            if campaign_id is None and obj.__class__.__name__ == "ResearchCampaign":
                setattr(obj, "campaign_id", uuid.uuid4())

            run_id = getattr(obj, "run_id", None)
            if run_id is None and obj.__class__.__name__ == "ResearchLaboratoryRun":
                setattr(obj, "run_id", uuid.uuid4())

            candidate_id = getattr(obj, "candidate_id", None)
            if candidate_id is not None and obj.__class__.__name__ == "ResearchCandidate":
                self.candidates[candidate_id] = obj

            if candidate_id is not None and obj.__class__.__name__ == "ResearchCandidateLineage":
                self.lineage[candidate_id] = obj

            if obj.__class__.__name__ == "ResearchCampaign":
                self.campaigns[obj.campaign_id] = obj

            if obj.__class__.__name__ == "ResearchCampaignStatistic":
                self.stats[obj.campaign_id] = obj

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM research_candidates" in sql:
            for candidate in self.candidates.values():
                if str(candidate.candidate_id) in sql:
                    return candidate
            return None
        if "FROM research_candidate_lineage" in sql:
            for lineage in self.lineage.values():
                if str(lineage.candidate_id) in sql:
                    return lineage
            return None
        if "FROM research_campaigns" in sql:
            for campaign in self.campaigns.values():
                if str(campaign.campaign_id) in sql:
                    return campaign
            return None
        if "FROM research_campaign_statistics" in sql:
            for stats in self.stats.values():
                if str(stats.campaign_id) in sql:
                    return stats
            return None
        return None


class _TransactionalFakeDb:
    def __init__(self, *, fail_flush_on_type: str | None = None, fail_once: bool = False) -> None:
        self._pending: list[object] = []
        self._persisted: dict[str, list[object]] = {}
        self._autoflush_enabled = True
        self._transaction_depth = 0
        self._snapshots: list[tuple[list[object], dict[str, list[object]], list[tuple[str, ...]], int]] = []
        self.fail_flush_on_type = fail_flush_on_type
        self.fail_once = fail_once
        self.flush_history: list[tuple[str, ...]] = []
        self.autoflush_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self._pending.append(obj)

    @property
    def no_autoflush(self):
        db = self

        class _NoAutoflush:
            def __enter__(self):
                db._autoflush_enabled = False
                return None

            def __exit__(self, exc_type, exc, tb):
                db._autoflush_enabled = True
                return False

        return _NoAutoflush()

    def in_transaction(self) -> bool:
        return self._transaction_depth > 0

    @asynccontextmanager
    async def begin(self):
        async with self._transaction_context():
            yield self

    @asynccontextmanager
    async def begin_nested(self):
        async with self._transaction_context():
            yield self

    @asynccontextmanager
    async def _transaction_context(self):
        pending_snapshot = list(self._pending)
        persisted_snapshot = {key: list(value) for key, value in self._persisted.items()}
        flush_history_len = len(self.flush_history)
        autoflush_snapshot = self.autoflush_count
        self._transaction_depth += 1
        self._snapshots.append((pending_snapshot, persisted_snapshot, list(self.flush_history), autoflush_snapshot))
        try:
            yield self
        except Exception:
            self._pending = pending_snapshot
            self._persisted = persisted_snapshot
            self.flush_history = self.flush_history[:flush_history_len]
            self.autoflush_count = autoflush_snapshot
            raise
        else:
            if self._pending:
                await self.flush()
        finally:
            self._snapshots.pop()
            self._transaction_depth -= 1

    async def flush(self) -> None:
        self.flush_count += 1
        batch = tuple(obj.__class__.__name__ for obj in self._pending)
        self.flush_history.append(batch)

        if self.fail_flush_on_type is not None and any(item == self.fail_flush_on_type for item in batch):
            if not self.fail_once or self.fail_flush_on_type is not None:
                failing_type = self.fail_flush_on_type
                if self.fail_once:
                    self.fail_flush_on_type = None
                raise RuntimeError(f"forced flush failure for {failing_type}")

        persisted_run_ids = {
            getattr(item, "run_id")
            for item in self._persisted.get("ResearchLaboratoryRun", [])
        }
        for obj in self._pending:
            if obj.__class__.__name__ == "ResearchAgentActivity":
                if getattr(obj, "laboratory_run_id") not in persisted_run_ids and not any(
                    pending.__class__.__name__ == "ResearchLaboratoryRun" and getattr(pending, "run_id") == getattr(obj, "laboratory_run_id")
                    for pending in self._pending
                ):
                    raise RuntimeError("child flush attempted before parent laboratory run persisted")

        pending_now = list(self._pending)
        self._pending.clear()
        for obj in pending_now:
            self._persisted.setdefault(obj.__class__.__name__, []).append(obj)

    async def scalar(self, statement):
        if self._autoflush_enabled and self._pending:
            self.autoflush_count += 1
            await self.flush()

        sql = str(statement)
        if "FROM research_candidates" in sql:
            for candidate in self._persisted.get("ResearchCandidate", []):
                if str(candidate.candidate_id) in sql:
                    return candidate
            return None
        if "FROM research_candidate_evaluations" in sql:
            for evaluation in self._persisted.get("ResearchCandidateEvaluation", []):
                if str(evaluation.evaluation_id) in sql:
                    return evaluation
            return None
        if "FROM research_candidate_lineage" in sql:
            for lineage in self._persisted.get("ResearchCandidateLineage", []):
                if str(lineage.candidate_id) in sql:
                    return lineage
            return None
        return None

    def persisted(self, class_name: str) -> list[object]:
        return list(self._persisted.get(class_name, []))


def _run() -> ResearchLaboratoryRun:
    return ResearchLaboratoryRun(
        laboratory_run_id=uuid.uuid4(),
        started_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 10, 10, 1, tzinfo=timezone.utc),
        participating_agents=("Baseline Research Agent",),
        generated_candidates=1,
        evaluated_candidates=1,
        status="COMPLETED",
    )


def _candidate(candidate_id: uuid.UUID) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=candidate_id,
        generated_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        originating_agent="Baseline Research Agent",
        strategy_name="Deterministic Candidate",
        description="deterministic",
        parameter_set={"fast_period": 9, "slow_period": 30},
        rationale="deterministic",
        status="PROPOSED",
    )


def _evaluation(candidate_id: uuid.UUID) -> CandidateEvaluation:
    return CandidateEvaluation(
        evaluation_id=uuid.uuid4(),
        candidate_id=candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=87,
        ai_coach_summary="summary",
        decision_intelligence_summary="intelligence",
        tournament_rank=1,
        promotion_eligible=False,
    )


@pytest.mark.asyncio
async def test_create_campaign_persists_research_campaign_and_statistics() -> None:
    db = _FakeDb()
    repository = ResearchPersistenceRepository()

    campaign = await repository.create_campaign(
        db=db,
        name="Deterministic Research",
        objective="Bounded paper-only cycle",
        participating_agents=("Baseline Research Agent",),
    )

    tables = {getattr(item, "__tablename__", "") for item in db.added}
    assert "research_campaigns" in tables
    assert "research_campaign_statistics" in tables
    assert campaign.campaign_id is not None


@pytest.mark.asyncio
async def test_record_laboratory_run_persists_candidates_runs_and_memory_entries() -> None:
    db = _FakeDb()
    repository = ResearchPersistenceRepository()

    candidate_id = uuid.uuid4()
    await repository.record_laboratory_run(
        db=db,
        run=_run(),
        candidates=[_candidate(candidate_id)],
        evaluations=[_evaluation(candidate_id)],
        campaign_id=uuid.uuid4(),
    )

    tables = [getattr(item, "__tablename__", "") for item in db.added]
    assert "research_laboratory_runs" in tables
    assert "research_candidates" in tables
    assert "research_memory_entries" in tables


@pytest.mark.asyncio
async def test_record_laboratory_run_flushes_parent_before_child_activity_rows() -> None:
    db = _TransactionalFakeDb()
    repository = ResearchPersistenceRepository()
    candidate_id = uuid.uuid4()

    await repository.record_laboratory_run(
        db=db,
        run=_run(),
        candidates=[_candidate(candidate_id)],
        evaluations=[_evaluation(candidate_id)],
        campaign_id=uuid.uuid4(),
    )

    assert db.flush_history[0] == ("ResearchLaboratoryRun",)
    assert len(db.persisted("ResearchAgentActivity")) == 1


@pytest.mark.asyncio
async def test_record_laboratory_run_persists_multiple_activity_rows() -> None:
    db = _TransactionalFakeDb()
    repository = ResearchPersistenceRepository()
    run = ResearchLaboratoryRun(
        laboratory_run_id=uuid.uuid4(),
        started_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 10, 10, 1, tzinfo=timezone.utc),
        participating_agents=("Baseline Research Agent", "OpenAI Sandbox"),
        generated_candidates=0,
        evaluated_candidates=0,
        status="COMPLETED",
    )

    await repository.record_laboratory_run(
        db=db,
        run=run,
        candidates=[],
        evaluations=[],
        campaign_id=uuid.uuid4(),
    )

    activities = db.persisted("ResearchAgentActivity")
    assert len(activities) == 2
    assert {item.agent_name for item in activities} == {"Baseline Research Agent", "OpenAI Sandbox"}
    assert {item.laboratory_run_id for item in activities} == {run.laboratory_run_id}


@pytest.mark.asyncio
async def test_candidate_queries_do_not_trigger_premature_autoflush() -> None:
    db = _TransactionalFakeDb()
    repository = ResearchPersistenceRepository()
    candidate_id = uuid.uuid4()

    await repository.record_laboratory_run(
        db=db,
        run=_run(),
        candidates=[_candidate(candidate_id)],
        evaluations=[_evaluation(candidate_id)],
        campaign_id=uuid.uuid4(),
    )

    assert db.autoflush_count == 0
    assert db.flush_count >= 2


@pytest.mark.asyncio
async def test_failure_after_parent_flush_rolls_back_complete_laboratory_write() -> None:
    db = _TransactionalFakeDb(fail_flush_on_type="ResearchCandidate")
    repository = ResearchPersistenceRepository()
    candidate_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="forced flush failure"):
        await repository.record_laboratory_run(
            db=db,
            run=_run(),
            candidates=[_candidate(candidate_id)],
            evaluations=[_evaluation(candidate_id)],
            campaign_id=uuid.uuid4(),
        )

    assert db.persisted("ResearchLaboratoryRun") == []
    assert db.persisted("ResearchAgentActivity") == []
    assert db.persisted("ResearchCandidate") == []
    assert db.persisted("ResearchMemoryEntry") == []


@pytest.mark.asyncio
async def test_session_remains_usable_after_rollback() -> None:
    db = _TransactionalFakeDb(fail_flush_on_type="ResearchCandidate", fail_once=True)
    repository = ResearchPersistenceRepository()
    candidate_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="forced flush failure"):
        await repository.record_laboratory_run(
            db=db,
            run=_run(),
            candidates=[_candidate(candidate_id)],
            evaluations=[_evaluation(candidate_id)],
            campaign_id=uuid.uuid4(),
        )

    retry_candidate_id = uuid.uuid4()
    await repository.record_laboratory_run(
        db=db,
        run=_run(),
        candidates=[_candidate(retry_candidate_id)],
        evaluations=[_evaluation(retry_candidate_id)],
        campaign_id=uuid.uuid4(),
    )

    assert len(db.persisted("ResearchLaboratoryRun")) == 1
    assert len(db.persisted("ResearchAgentActivity")) == 1
    assert len(db.persisted("ResearchCandidate")) == 1


@pytest.mark.asyncio
async def test_successful_retry_after_rollback_persists_single_completed_run() -> None:
    db = _TransactionalFakeDb(fail_flush_on_type="ResearchCandidate", fail_once=True)
    repository = ResearchPersistenceRepository()
    run = _run()
    candidate_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="forced flush failure"):
        await repository.record_laboratory_run(
            db=db,
            run=run,
            candidates=[_candidate(candidate_id)],
            evaluations=[_evaluation(candidate_id)],
            campaign_id=uuid.uuid4(),
        )

    await repository.record_laboratory_run(
        db=db,
        run=run,
        candidates=[_candidate(candidate_id)],
        evaluations=[_evaluation(candidate_id)],
        campaign_id=uuid.uuid4(),
    )

    persisted_runs = db.persisted("ResearchLaboratoryRun")
    assert len(persisted_runs) == 1
    assert persisted_runs[0].run_id == run.laboratory_run_id


@pytest.mark.asyncio
async def test_record_evolved_candidates_persists_lineage_and_memory_growth() -> None:
    db = _FakeDb()
    repository = ResearchPersistenceRepository()

    parent_id = uuid.uuid4()
    descendant_id = uuid.uuid4()

    db.candidates[parent_id] = type(
        "CandidateRow",
        (),
        {
            "candidate_id": parent_id,
            "laboratory_run_id": uuid.uuid4(),
            "campaign_id": uuid.uuid4(),
            "parent_candidate_id": None,
            "originating_agent": "Baseline Research Agent",
            "strategy_name": "Parent",
            "description": "parent",
            "parameter_set": {"fast_period": 9},
            "rationale": "parent",
            "status": "EVALUATED",
            "generation": 1,
            "mutation_reason": None,
            "parameter_diff": [],
            "generated_at": datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        },
    )()

    descendant = EvolvedCandidate(
        candidate_id=descendant_id,
        parent_candidate_id=parent_id,
        generation=2,
        mutation_reason="rsi_period 14->12",
        parameter_diff=(
            EvolutionMutation(
                parameter_name="rsi_period",
                previous_value=14,
                new_value=12,
            ),
        ),
        parameter_set={"rsi_period": 12},
        strategy_name="Descendant",
        originating_agent="Baseline Research Agent",
        generated_at=datetime(2026, 7, 10, 10, 2, tzinfo=timezone.utc),
        quality_score=92,
        tournament_rank=1,
        status="EVALUATED",
    )

    await repository.record_evolved_candidates(
        db=db,
        descendants=[descendant],
        campaign_id=uuid.uuid4(),
    )

    tables = [getattr(item, "__tablename__", "") for item in db.added]
    assert "research_candidates" in tables
    assert "research_candidate_lineage" in tables
    assert "research_memory_entries" in tables
