# Research Memory v1

## Purpose

Research Memory v1 preserves deterministic research history across laboratory runs.

It is designed for research observability only:

- no production execution changes
- no strategy promotion automation
- no model training
- no LLM memory
- no external AI providers

Research Memory allows future deterministic research agents to inspect prior run outcomes without mutating production state.

## Scope

Research Memory records the following per laboratory run:

- laboratory_run_id
- started_at
- completed_at
- participating_agents
- candidates_generated
- candidates_evaluated

Research Memory records the following per candidate:

- candidate_id
- originating_agent
- parameter_set
- evaluation_summary
- quality_score
- tournament_rank
- status

Additional recorded artifacts:

- tournament outcomes by candidate rank
- agent participation by run

## Architecture Placement

Research Laboratory

-> Research Memory

-> Future Research Agents

-> Candidate Generation

-> Candidate Evaluation

-> Tournament

-> Capital Allocation

-> Human Review

## API Surface

All routes are read-only and return deterministic history from in-memory research storage.

- GET /research/memory
- GET /research/memory/runs
- GET /research/memory/candidates

## Determinism and Boundaries

- Research Memory is populated only from existing deterministic laboratory and candidate evaluation flows.
- No production writes are introduced.
- No execution pipelines are modified.
- No automatic promotion logic is introduced.
- Human review remains mandatory for any promotion decisions.
