# LLM Research Agent Adapter v1

## Purpose

LLM Research Agent Adapter v1 defines a framework-only abstraction for future research agents.

This release does not integrate any provider SDK and does not perform network calls.

## Scope

Abstract adapter interface methods:

- generate_hypotheses()
- explain_candidate()
- critique_candidate()
- summarize_laboratory()

No method implementations are provided.
All methods raise NotImplementedError.

## Prompt Contracts

HypothesisRequest includes:

- Research Memory
- Evolution Analytics
- Candidate History
- Tournament History

HypothesisResponse includes:

- Candidate Strategy
- Rationale
- Expected behavior
- Confidence

Additional request and response contracts are provided for explain, critique, and laboratory summary flows.

## Registration

The registration layer supports future adapter registration metadata for:

- OpenAI Agent
- Anthropic Agent
- Gemini Agent
- Local Model Agent

No provider-specific adapter implementations are included.

## API

Read-only adapter registration endpoint:

- GET /research/llm-adapters

Initial state returns no installed LLM adapters.

## Safety and Boundaries

- No OpenAI SDK
- No Anthropic SDK
- No API keys
- No network requests
- No production writes
- No execution changes
- No automatic promotion

## Architecture Placement

LLM Adapter

-> Research Laboratory

-> Research Memory

-> Evolution

-> Candidate Evaluation

-> Tournament

-> Capital Allocation

-> Human Review
