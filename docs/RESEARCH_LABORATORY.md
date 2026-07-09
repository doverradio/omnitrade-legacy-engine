# Research Laboratory v1

## Purpose
Research Laboratory v1 is a deterministic, research-only orchestration layer that coordinates registered research agents.

It does not trade, does not modify production execution, does not promote strategies, and does not bypass governance.

## Responsibilities
- Discover registered research agents.
- Execute candidate generation for participating agents.
- Collect generated candidate batches.
- Maintain laboratory batch metadata.
- Submit generated candidates into the existing Candidate Evaluation Pipeline.

## Laboratory Run
A laboratory run records:
- laboratory_run_id
- started_at
- completed_at
- participating_agents
- generated_candidates
- evaluated_candidates
- status

## API
- GET /research/laboratory
  - Returns current laboratory status and last run summary.

- POST /research/laboratory/run
  - Starts a deterministic laboratory run and returns run summary.

## Repository Boundaries
Allowed:
- Research-only orchestration.
- Deterministic candidate generation and evaluation coordination.
- Read-only reuse of existing Candidate Evaluation Pipeline.

Not allowed:
- Live trading.
- Production writes.
- Strategy promotion.
- Execution path changes.
- Autonomous governance bypass.
- LLM or model calls.

## Future Evolution
Future versions may add richer run history views, laboratory filtering, and agent-specific diagnostic outputs.

All future evolution must preserve research-only boundaries and explicit human governance for any promotion decisions.
