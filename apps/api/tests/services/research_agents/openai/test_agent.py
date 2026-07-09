from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from app.services.research_agents.llm_adapter.contracts import (
    CritiqueCandidateRequest,
    ExplainCandidateRequest,
    HypothesisRequest,
    SummarizeLaboratoryRequest,
)
from app.services.research_agents.openai.agent import OpenAIResearchAgent


class StubOpenAIClient:
    def __init__(self, *, available: bool, response: dict[str, object] | None = None) -> None:
        self.is_available = available
        self._response = response or {}

    def create_chat_completion(self, **_: object) -> dict[str, object]:
        return self._response


def _hypothesis_request() -> HypothesisRequest:
    return HypothesisRequest(
        research_memory={"total_laboratory_runs": 2, "total_candidates": 8},
        evolution_analytics={"average_quality_score": 70.0, "lineage_depth": 2},
        candidate_history=[
            {
                "candidate_id": "11111111-1111-1111-1111-111111111111",
                "generation": 1,
                "quality_score": 80,
                "tournament_rank": 1,
                "parameter_set": {"fast_period": 12},
            }
        ],
        tournament_history=[
            {
                "tournament_id": None,
                "generated_at": None,
                "ranking": [{"candidate_id": "11111111-1111-1111-1111-111111111111", "rank": 1}],
            }
        ],
    )


def test_generate_hypotheses_batch_and_candidate_conversion() -> None:
    client = StubOpenAIClient(
        available=True,
        response={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"ideas": ['
                            '{"strategy_name": "Momentum Variant", "parameter_suggestions": {"fast_period": 11}, '
                            '"rationale": "Trend continuation", "expected_behavior": "Captures breakouts", '
                            '"confidence": 0.85, "research_notes": "Use as sandbox candidate"}, '
                            '{"strategy_name": "Mean Revert Variant", "parameter_suggestions": {"rsi_period": 10}, '
                            '"rationale": "Short-term oversold", "expected_behavior": "Reversion entries", '
                            '"confidence": 0.7, "research_notes": "Pair with volatility filter"}'
                            "]}"
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 180,
                "total_tokens": 300,
            },
        },
    )
    agent = OpenAIResearchAgent(client=client)

    ideas, metadata = agent.generate_hypotheses_batch(request=_hypothesis_request())
    assert len(ideas) == 2
    assert ideas[0].strategy_name == "Momentum Variant"
    assert metadata.prompt_version == "openai-research-agent-v1"
    assert metadata.prompt_tokens == 120
    assert metadata.completion_tokens == 180
    assert metadata.total_tokens == 300

    generated_at = datetime(2026, 7, 9, tzinfo=timezone.utc)
    candidates_a = agent.to_strategy_candidates(ideas=ideas, generated_at=generated_at)
    candidates_b = agent.to_strategy_candidates(ideas=ideas, generated_at=generated_at)

    assert len(candidates_a) == 2
    assert candidates_a[0].candidate_id == candidates_b[0].candidate_id
    assert candidates_a[0].originating_agent == "OpenAI Research Agent"
    assert candidates_a[0].status == "PROPOSED"

    hypothesis = agent.generate_hypotheses(request=_hypothesis_request())
    assert hypothesis.candidate_strategy in {"Momentum Variant", "Mean Revert Variant"}


def test_generate_hypotheses_batch_unavailable_returns_empty() -> None:
    agent = OpenAIResearchAgent(client=StubOpenAIClient(available=False))

    ideas, metadata = agent.generate_hypotheses_batch(request=_hypothesis_request())

    assert ideas == []
    assert metadata.response_duration_ms == 0
    assert metadata.prompt_tokens is None
    assert metadata.completion_tokens is None
    assert metadata.total_tokens is None


def test_generate_hypotheses_raises_when_no_ideas() -> None:
    client = StubOpenAIClient(
        available=True,
        response={
            "choices": [{"message": {"content": '{"ideas": []}'}}],
        },
    )
    agent = OpenAIResearchAgent(client=client)

    with pytest.raises(RuntimeError, match="No hypotheses were generated"):
        agent.generate_hypotheses(request=_hypothesis_request())


def test_only_generate_hypotheses_is_implemented() -> None:
    agent = OpenAIResearchAgent(client=StubOpenAIClient(available=False))

    with pytest.raises(NotImplementedError):
        agent.explain_candidate(
            ExplainCandidateRequest(
                candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                parameter_set={},
                quality_score=None,
            )
        )

    with pytest.raises(NotImplementedError):
        agent.critique_candidate(
            CritiqueCandidateRequest(
                candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                parameter_set={},
                quality_score=None,
                tournament_rank=None,
            )
        )

    with pytest.raises(NotImplementedError):
        agent.summarize_laboratory(
            SummarizeLaboratoryRequest(
                laboratory_run_id=None,
                run_summary={},
            )
        )
