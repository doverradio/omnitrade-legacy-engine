from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
import uuid

from app.services.research_agents.interface import StrategyCandidate
from app.services.research_agents.llm_adapter.contracts import (
    CritiqueCandidateRequest,
    CritiqueCandidateResponse,
    ExplainCandidateRequest,
    ExplainCandidateResponse,
    HypothesisRequest,
    HypothesisResponse,
    SummarizeLaboratoryRequest,
    SummarizeLaboratoryResponse,
)
from app.services.research_agents.llm_adapter.interface import LLMResearchAgentAdapter
from app.services.research_agents.openai.client import OpenAIChatClient


logger = logging.getLogger(__name__)

_PROMPT_VERSION = "openai-research-agent-v1"
_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000009")


@dataclass(frozen=True, slots=True)
class OpenAIGenerationMetadata:
    generation_timestamp: datetime
    prompt_version: str
    response_duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True, slots=True)
class HypothesisIdea:
    strategy_name: str
    parameter_suggestions: dict[str, object]
    rationale: str
    expected_behavior: str
    confidence: float
    research_notes: str


class OpenAIResearchAgent(LLMResearchAgentAdapter):
    adapter_id = uuid.uuid5(uuid.UUID("00000000-0000-0000-0000-000000000008"), "openai:OpenAI Research Agent")
    adapter_name = "OpenAI Research Agent"
    provider = "openai"
    status = "UNAVAILABLE"

    def __init__(self, *, client: OpenAIChatClient | None = None, model: str = "gpt-4o-mini") -> None:
        self._client = client or OpenAIChatClient()
        self._model = model

    @property
    def is_available(self) -> bool:
        return self._client.is_available

    @property
    def adapter_status(self) -> str:
        return "AVAILABLE" if self.is_available else "UNAVAILABLE"

    def generate_hypotheses(self, request: HypothesisRequest) -> HypothesisResponse:
        ideas, _ = self.generate_hypotheses_batch(request=request)
        if not ideas:
            raise RuntimeError("No hypotheses were generated")

        first = ideas[0]
        return HypothesisResponse(
            candidate_strategy=first.strategy_name,
            rationale=f"{first.rationale}\nResearch notes: {first.research_notes}",
            expected_behavior=first.expected_behavior,
            confidence=first.confidence,
        )

    def generate_hypotheses_batch(self, *, request: HypothesisRequest) -> tuple[list[HypothesisIdea], OpenAIGenerationMetadata]:
        if not self.is_available:
            now = datetime.now(timezone.utc)
            metadata = OpenAIGenerationMetadata(
                generation_timestamp=now,
                prompt_version=_PROMPT_VERSION,
                response_duration_ms=0,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
            )
            return [], metadata

        prompt = _build_prompt(request)
        start = time.perf_counter()
        raw = self._client.create_chat_completion(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a research-only quant strategy assistant. "
                        "Return strict JSON with key 'ideas'."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        duration_ms = int((time.perf_counter() - start) * 1000)

        content = _extract_content(raw)
        ideas = _parse_hypothesis_ideas(content)
        usage = raw.get("usage") if isinstance(raw, dict) else None
        prompt_tokens = _usage_value(usage, "prompt_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens")
        total_tokens = _usage_value(usage, "total_tokens")

        metadata = OpenAIGenerationMetadata(
            generation_timestamp=datetime.now(timezone.utc),
            prompt_version=_PROMPT_VERSION,
            response_duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

        logger.info(
            "openai_research_generation",
            extra={
                "generation_timestamp": metadata.generation_timestamp.isoformat(),
                "prompt_version": metadata.prompt_version,
                "response_duration_ms": metadata.response_duration_ms,
                "prompt_tokens": metadata.prompt_tokens,
                "completion_tokens": metadata.completion_tokens,
                "total_tokens": metadata.total_tokens,
                "ideas_generated": len(ideas),
            },
        )

        return ideas[:5], metadata

    def to_strategy_candidates(self, *, ideas: list[HypothesisIdea], generated_at: datetime) -> list[StrategyCandidate]:
        candidates: list[StrategyCandidate] = []
        for index, idea in enumerate(ideas, start=1):
            seed = f"{idea.strategy_name}:{index}:{_stable_dict(idea.parameter_suggestions)}"
            candidate_id = uuid.uuid5(_NAMESPACE, seed)
            candidates.append(
                StrategyCandidate(
                    candidate_id=candidate_id,
                    generated_at=generated_at,
                    originating_agent=self.adapter_name,
                    strategy_name=idea.strategy_name,
                    description=idea.expected_behavior,
                    parameter_set=dict(idea.parameter_suggestions),
                    rationale=f"{idea.rationale}\nResearch notes: {idea.research_notes}",
                    status="PROPOSED",
                )
            )
        return candidates

    def explain_candidate(self, request: ExplainCandidateRequest) -> ExplainCandidateResponse:
        raise NotImplementedError()

    def critique_candidate(self, request: CritiqueCandidateRequest) -> CritiqueCandidateResponse:
        raise NotImplementedError()

    def summarize_laboratory(self, request: SummarizeLaboratoryRequest) -> SummarizeLaboratoryResponse:
        raise NotImplementedError()


def _build_prompt(request: HypothesisRequest) -> str:
    return (
        "Generate exactly 5 deterministic candidate strategy ideas for research evaluation.\n"
        "Each idea must include: strategy_name, parameter_suggestions (object), rationale, expected_behavior, confidence, research_notes.\n"
        "Use only the provided deterministic context.\n"
        "Return JSON object: {\"ideas\":[...]}\n\n"
        f"Research Memory Summary:\n{json.dumps(request.research_memory, sort_keys=True)}\n\n"
        f"Evolution Analytics Summary:\n{json.dumps(request.evolution_analytics, sort_keys=True)}\n\n"
        f"Candidate History:\n{json.dumps([item.model_dump(mode='json') for item in request.candidate_history], sort_keys=True)}\n\n"
        f"Tournament History:\n{json.dumps([item.model_dump(mode='json') for item in request.tournament_history], sort_keys=True)}\n"
    )


def _extract_content(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI response missing choices")

    first = choices[0]
    if not isinstance(first, dict):
        raise RuntimeError("OpenAI response choice is invalid")

    message = first.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("OpenAI response message is invalid")

    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("OpenAI response content is invalid")

    return content


def _parse_hypothesis_ideas(content: str) -> list[HypothesisIdea]:
    decoded = json.loads(content)
    if not isinstance(decoded, dict):
        raise RuntimeError("OpenAI JSON payload is invalid")

    ideas = decoded.get("ideas")
    if not isinstance(ideas, list):
        raise RuntimeError("OpenAI JSON payload missing ideas list")

    results: list[HypothesisIdea] = []
    for item in ideas:
        if not isinstance(item, dict):
            continue
        strategy_name = str(item.get("strategy_name") or "").strip()
        rationale = str(item.get("rationale") or "").strip()
        expected_behavior = str(item.get("expected_behavior") or "").strip()
        research_notes = str(item.get("research_notes") or "").strip()
        raw_confidence = item.get("confidence", 0)
        parameter_suggestions = item.get("parameter_suggestions", {})

        if not strategy_name or not rationale or not expected_behavior:
            continue
        if not isinstance(parameter_suggestions, dict):
            parameter_suggestions = {}

        try:
            confidence = float(raw_confidence)
        except Exception:
            confidence = 0.0

        confidence = max(0.0, min(confidence, 1.0))
        results.append(
            HypothesisIdea(
                strategy_name=strategy_name,
                parameter_suggestions=dict(parameter_suggestions),
                rationale=rationale,
                expected_behavior=expected_behavior,
                confidence=confidence,
                research_notes=research_notes,
            )
        )

    return results


def _usage_value(usage: object, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if isinstance(value, int):
        return value
    return None


def _stable_dict(value: dict[str, object]) -> str:
    return "|".join(
        f"{key}:{value[key]}"
        for key in sorted(value)
    )
