from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.services import research_activation as activation
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.interface import EvolutionRunResult
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_laboratory.interface import ResearchLaboratoryRun


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


class _FakeDb:
    def __init__(
        self,
        *,
        decision_count: int = 0,
        actionable_count: int = 0,
        trade_count: int = 0,
        best_candidate_name: str | None = None,
        latest_run_started_at: datetime | None = None,
        running_campaign_id: uuid.UUID | None = None,
    ) -> None:
        self.decision_count = decision_count
        self.actionable_count = actionable_count
        self.trade_count = trade_count
        self.best_candidate_name = best_candidate_name
        self.latest_run_started_at = latest_run_started_at
        self.running_campaign_id = running_campaign_id
        self.added: list[object] = []

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM research_laboratory_runs" in sql and "started_at" in sql:
            return self.latest_run_started_at
        if "FROM research_campaigns" in sql and "status =" in sql:
            return self.running_campaign_id
        if "count(*)" in sql and "FROM decision_records" in sql:
            return self.decision_count
        if "count(*)" in sql and "FROM signals" in sql:
            return self.actionable_count
        if "count(*)" in sql and "FROM trades" in sql:
            return self.trade_count
        if "FROM research_candidates" in sql:
            if self.best_candidate_name is None:
                return None
            return SimpleNamespace(strategy_name=self.best_candidate_name)
        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM validation_runs" in sql:
            return _ExecuteResult([uuid.uuid4()])
        return _ExecuteResult([])

    def add(self, obj):
        self.added.append(obj)


class _FakeRepository:
    def __init__(self) -> None:
        self.created_campaign_id = uuid.uuid4()
        self.recorded_runs = 0
        self.recorded_descendants = 0
        self.recorded_candidate_ids: list[uuid.UUID] = []
        self.recorded_descendant_ids: list[uuid.UUID] = []

    async def create_campaign(self, *, db, name, objective, participating_agents):
        return SimpleNamespace(
            campaign_id=self.created_campaign_id,
            name=name,
            objective=objective,
            status="IDLE",
            started_at=None,
            completed_at=None,
            participating_agents=participating_agents,
            laboratory_runs=0,
            candidates_generated=0,
            candidates_evaluated=0,
            best_candidate=None,
            best_quality_score=None,
            current_champion=None,
        )

    async def upsert_campaign_statistics(self, *, db, campaign_id, laboratory_runs_increment, candidates_generated_increment, candidates_evaluated_increment, best_candidate_id, best_quality_score, current_champion, status, participating_agents):
        return SimpleNamespace(
            campaign_id=campaign_id,
            participating_agents=participating_agents,
            candidates_generated=candidates_generated_increment,
            candidates_evaluated=candidates_evaluated_increment,
            current_champion=current_champion,
        )

    async def record_laboratory_run(self, *, db, run, candidates, evaluations, campaign_id):
        self.recorded_runs += 1
        self.recorded_candidate_ids.extend(item.candidate_id for item in candidates)

    async def record_evolved_candidates(self, *, db, descendants, campaign_id):
        self.recorded_descendants += len(descendants)
        self.recorded_descendant_ids.extend(item.candidate_id for item in descendants)

    async def list_candidates(self, *, db, limit, offset):
        return tuple()


class _FakeLaboratory:
    def __init__(self, run: ResearchLaboratoryRun) -> None:
        self._run = run

    def run(self) -> ResearchLaboratoryRun:
        return self._run


class _FakeEvolutionEngine:
    def evolve(self, *, memory_candidates, parent_candidate_id, generation_limit):
        return EvolutionRunResult(generated_count=0, descendants=tuple())


class _DescendantEvolutionEngine:
    def __init__(self, descendants) -> None:
        self._descendants = tuple(descendants)

    def evolve(self, *, memory_candidates, parent_candidate_id, generation_limit):
        return EvolutionRunResult(generated_count=len(self._descendants), descendants=self._descendants)


@pytest.mark.asyncio
async def test_research_cycle_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        activation,
        "get_settings",
        lambda: SimpleNamespace(research_evolution_enabled=False),
    )

    result = await activation.run_deterministic_research_cycle_if_due(db=_FakeDb())

    assert result.started is False
    assert result.reason == "research_disabled"


@pytest.mark.asyncio
async def test_research_cycle_skips_when_interval_not_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        activation,
        "get_settings",
        lambda: SimpleNamespace(
            research_evolution_enabled=True,
            research_cycle_interval_minutes=30,
        ),
    )

    now = datetime.now(timezone.utc)
    result = await activation.run_deterministic_research_cycle_if_due(
        db=_FakeDb(latest_run_started_at=now),
    )

    assert result.started is False
    assert result.reason == "research_interval_not_elapsed"


@pytest.mark.asyncio
async def test_research_cycle_skips_when_campaign_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    running_campaign_id = uuid.uuid4()
    monkeypatch.setattr(
        activation,
        "get_settings",
        lambda: SimpleNamespace(
            research_evolution_enabled=True,
            research_cycle_interval_minutes=30,
        ),
    )

    result = await activation.run_deterministic_research_cycle_if_due(
        db=_FakeDb(running_campaign_id=running_campaign_id),
    )

    assert result.started is False
    assert result.reason == "research_cycle_already_running"
    assert result.campaign_id == running_campaign_id


@pytest.mark.asyncio
async def test_research_cycle_runs_and_keeps_champion_null_without_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    candidate_id = uuid.uuid4()
    repository = _FakeRepository()
    run = ResearchLaboratoryRun(
        laboratory_run_id=uuid.uuid4(),
        started_at=now,
        completed_at=now,
        participating_agents=("Baseline Research Agent",),
        generated_candidates=1,
        evaluated_candidates=1,
        status="COMPLETED",
    )
    candidate = StrategyCandidate(
        candidate_id=candidate_id,
        generated_at=now,
        originating_agent="Baseline Research Agent",
        strategy_name="MA-RSI Blend rsi14",
        description="deterministic",
        parameter_set={"family": "ma_rsi_blend"},
        rationale="deterministic",
        status="PROPOSED",
    )
    evaluation = CandidateEvaluation(
        evaluation_id=uuid.uuid4(),
        candidate_id=candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=90,
        ai_coach_summary="summary",
        decision_intelligence_summary="intelligence",
        tournament_rank=1,
        promotion_eligible=False,
    )

    monkeypatch.setattr(
        activation,
        "get_settings",
        lambda: SimpleNamespace(
            research_evolution_enabled=True,
            research_cycle_interval_minutes=30,
            research_max_candidates_per_cycle=6,
            research_max_descendants_per_candidate=3,
            research_max_generation=5,
            research_min_decisions=50,
            research_min_actionable_signals=5,
            research_min_trades=3,
        ),
    )
    monkeypatch.setattr(activation, "ResearchPersistenceRepository", lambda: repository)
    monkeypatch.setattr(activation, "get_research_laboratory", lambda: _FakeLaboratory(run))
    monkeypatch.setattr(activation, "list_generated_strategy_candidates", lambda: (candidate,))
    monkeypatch.setattr(activation, "build_candidate_evaluations_batch_v1", lambda **kwargs: [evaluation])
    monkeypatch.setattr(activation, "list_registered_research_agents", lambda: (SimpleNamespace(agent_name="Baseline Research Agent"),))
    monkeypatch.setattr(activation, "get_evolution_engine", lambda: _FakeEvolutionEngine())
    monkeypatch.setattr(
        activation,
        "EvolutionAnalyticsService",
        lambda **kwargs: SimpleNamespace(
            build_summary=lambda: SimpleNamespace(
                best_candidate=SimpleNamespace(candidate_id=candidate_id),
                best_quality_score=90,
                top_research_agent="Baseline Research Agent",
            )
        ),
    )

    result = await activation.run_deterministic_research_cycle_if_due(
        db=_FakeDb(decision_count=0, actionable_count=0, trade_count=0)
    )

    assert result.started is True
    assert result.candidates_generated == 1
    assert result.candidates_evaluated == 1
    assert result.champion is None
    assert repository.recorded_runs == 1


@pytest.mark.asyncio
async def test_research_cycle_selects_champion_when_thresholds_are_met(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    candidate_id = uuid.uuid4()
    repository = _FakeRepository()
    run = ResearchLaboratoryRun(
        laboratory_run_id=uuid.uuid4(),
        started_at=now,
        completed_at=now,
        participating_agents=("Baseline Research Agent",),
        generated_candidates=1,
        evaluated_candidates=1,
        status="COMPLETED",
    )
    candidate = StrategyCandidate(
        candidate_id=candidate_id,
        generated_at=now,
        originating_agent="Baseline Research Agent",
        strategy_name="MA-RSI Blend rsi14",
        description="deterministic",
        parameter_set={"family": "ma_rsi_blend"},
        rationale="deterministic",
        status="PROPOSED",
    )
    evaluation = CandidateEvaluation(
        evaluation_id=uuid.uuid4(),
        candidate_id=candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=95,
        ai_coach_summary="summary",
        decision_intelligence_summary="intelligence",
        tournament_rank=1,
        promotion_eligible=False,
    )

    monkeypatch.setattr(
        activation,
        "get_settings",
        lambda: SimpleNamespace(
            research_evolution_enabled=True,
            research_cycle_interval_minutes=30,
            research_max_candidates_per_cycle=6,
            research_max_descendants_per_candidate=3,
            research_max_generation=5,
            research_min_decisions=50,
            research_min_actionable_signals=5,
            research_min_trades=3,
        ),
    )
    monkeypatch.setattr(activation, "ResearchPersistenceRepository", lambda: repository)
    monkeypatch.setattr(activation, "get_research_laboratory", lambda: _FakeLaboratory(run))
    monkeypatch.setattr(activation, "list_generated_strategy_candidates", lambda: (candidate,))
    monkeypatch.setattr(activation, "build_candidate_evaluations_batch_v1", lambda **kwargs: [evaluation])
    monkeypatch.setattr(activation, "list_registered_research_agents", lambda: (SimpleNamespace(agent_name="Baseline Research Agent"),))
    monkeypatch.setattr(activation, "get_evolution_engine", lambda: _FakeEvolutionEngine())
    monkeypatch.setattr(
        activation,
        "EvolutionAnalyticsService",
        lambda **kwargs: SimpleNamespace(
            build_summary=lambda: SimpleNamespace(
                best_candidate=SimpleNamespace(candidate_id=candidate_id),
                best_quality_score=95,
                top_research_agent="Baseline Research Agent",
            )
        ),
    )

    result = await activation.run_deterministic_research_cycle_if_due(
        db=_FakeDb(decision_count=100, actionable_count=10, trade_count=5, best_candidate_name="MA-RSI Blend rsi14")
    )

    assert result.started is True
    assert result.champion == "MA-RSI Blend rsi14"


@pytest.mark.asyncio
async def test_research_cycle_enforces_bounded_outputs_and_emits_memory_growth(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    candidate_id = uuid.uuid4()
    descendant_in_scope_id = uuid.uuid4()
    descendant_out_of_scope_id = uuid.uuid4()

    repository = _FakeRepository()
    run = ResearchLaboratoryRun(
        laboratory_run_id=uuid.uuid4(),
        started_at=now,
        completed_at=now,
        participating_agents=("Baseline Research Agent",),
        generated_candidates=1,
        evaluated_candidates=1,
        status="COMPLETED",
    )
    candidate = StrategyCandidate(
        candidate_id=candidate_id,
        generated_at=now,
        originating_agent="Baseline Research Agent",
        strategy_name="MA-RSI Blend bounded",
        description="deterministic",
        parameter_set={"family": "ma_rsi_blend"},
        rationale="deterministic",
        status="PROPOSED",
    )
    evaluation = CandidateEvaluation(
        evaluation_id=uuid.uuid4(),
        candidate_id=candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=88,
        ai_coach_summary="summary",
        decision_intelligence_summary="intelligence",
        tournament_rank=1,
        promotion_eligible=False,
    )

    descendants = (
        SimpleNamespace(
            candidate_id=descendant_in_scope_id,
            parent_candidate_id=candidate_id,
            generation=2,
            mutation_reason="rsi_period 14->12",
            parameter_diff=tuple(),
            parameter_set={"rsi_period": 12},
            strategy_name="descendant-in-scope",
            originating_agent="Baseline Research Agent",
            generated_at=now,
            quality_score=77,
            tournament_rank=2,
            status="EVALUATED",
        ),
        SimpleNamespace(
            candidate_id=descendant_out_of_scope_id,
            parent_candidate_id=candidate_id,
            generation=6,
            mutation_reason="rsi_period 14->10",
            parameter_diff=tuple(),
            parameter_set={"rsi_period": 10},
            strategy_name="descendant-out-of-scope",
            originating_agent="Baseline Research Agent",
            generated_at=now,
            quality_score=75,
            tournament_rank=3,
            status="EVALUATED",
        ),
    )

    monkeypatch.setattr(
        activation,
        "get_settings",
        lambda: SimpleNamespace(
            research_evolution_enabled=True,
            research_cycle_interval_minutes=30,
            research_max_candidates_per_cycle=1,
            research_max_descendants_per_candidate=3,
            research_max_generation=3,
            research_min_decisions=0,
            research_min_actionable_signals=0,
            research_min_trades=0,
        ),
    )
    monkeypatch.setattr(activation, "ResearchPersistenceRepository", lambda: repository)
    monkeypatch.setattr(activation, "get_research_laboratory", lambda: _FakeLaboratory(run))
    monkeypatch.setattr(activation, "list_generated_strategy_candidates", lambda: (candidate,))
    monkeypatch.setattr(activation, "build_candidate_evaluations_batch_v1", lambda **kwargs: [evaluation])
    monkeypatch.setattr(activation, "list_registered_research_agents", lambda: (SimpleNamespace(agent_name="Baseline Research Agent"),))
    monkeypatch.setattr(activation, "get_evolution_engine", lambda: _DescendantEvolutionEngine(descendants))
    monkeypatch.setattr(
        activation,
        "EvolutionAnalyticsService",
        lambda **kwargs: SimpleNamespace(
            build_summary=lambda: SimpleNamespace(
                best_candidate=SimpleNamespace(candidate_id=candidate_id),
                best_quality_score=88,
                top_research_agent="Baseline Research Agent",
            )
        ),
    )

    fake_db = _FakeDb(decision_count=1, actionable_count=1, trade_count=1, best_candidate_name="MA-RSI Blend bounded")
    result = await activation.run_deterministic_research_cycle_if_due(db=fake_db)

    assert result.started is True
    assert result.candidates_generated == 1
    assert result.candidates_evaluated == 1
    assert result.descendants_generated == 1
    assert repository.recorded_runs == 1
    assert repository.recorded_descendants == 1
    assert repository.recorded_candidate_ids == [candidate_id]
    assert repository.recorded_descendant_ids == [descendant_in_scope_id]

    memory_events = [
        event
        for event in fake_db.added
        if getattr(event, "event_type", None) == "RESEARCH_MEMORY_UPDATED"
    ]
    assert len(memory_events) == 1
    metadata = memory_events[0].payload["metadata"]
    assert metadata["candidates_generated"] == 2
    assert metadata["candidates_evaluated"] == 2
    assert metadata["descendants_generated"] == 1
