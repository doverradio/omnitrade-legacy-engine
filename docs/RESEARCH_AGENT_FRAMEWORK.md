# Research Agent Framework v1

## Purpose
Research Agent Framework v1 defines a deterministic, read-only architecture for independent research agents that propose candidate strategies for later evaluation.

This framework does not execute strategies and does not interact with production execution.

## Inputs
- Registered research agent definitions
- Deterministic candidate generation rules per agent

## Outputs
Read-only Research Agent and Strategy Candidate payloads.

Strategy Candidate fields:
- candidate_id
- generated_at
- originating_agent
- strategy_name
- description
- parameter_set
- rationale
- status

Initial status for all v1 candidates: PROPOSED.

## Repository Boundaries
Allowed:
- Register deterministic research agents
- Generate deterministic candidate strategies
- Expose candidate and agent lists through read-only APIs

Not allowed:
- AI or LLM usage
- Replay execution
- Decision quality execution
- Strategy execution
- Production writes
- Automatic promotion

## Future Evolution
Future versions may add additional research agents, candidate lineage metadata, and controlled model-assisted ideation.

Any future evolution must keep clear separation between candidate generation and production execution.
