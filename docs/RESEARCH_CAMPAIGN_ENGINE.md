# Research Campaign Engine v1

## Purpose

Research Campaign Engine v1 coordinates repeated deterministic research cycles under a named campaign.

The engine is strictly research-only:

- No production writes
- No automatic promotion
- No live trading
- No execution path changes

## Campaign Model

Each campaign tracks:

- `campaign_id`
- `name`
- `objective`
- `status`
- `started_at`
- `completed_at`
- `participating_agents`
- `laboratory_runs`
- `candidates_generated`
- `candidates_evaluated`
- `best_candidate`
- `best_quality_score`

## Deterministic Orchestration Flow

A campaign run executes the following deterministic chain:

1. Research Laboratory run
2. Research Memory persistence (via existing laboratory memory recorder)
3. Evolution Engine run
4. Descendant evaluation (via existing evolution deterministic evaluator)
5. Campaign statistics update
6. Tournament snapshot build (read-only)
7. Capital allocation recommendation build (read-only)
8. Human review handoff (policy gate remains outside campaign execution)

## Reuse and No-Duplicate-Orchestration

Research Campaign Engine v1 reuses existing modules:

- Research Laboratory
- Research Memory
- Evolution Engine
- Candidate Evaluation
- Tournament
- Capital Allocation

No production mutation or execution integration is introduced.

## API Endpoints

- `GET /research/campaigns`
- `GET /research/campaigns/{id}`
- `POST /research/campaigns`
- `POST /research/campaigns/{id}/run`

All endpoints are research-domain only.

## Frontend Surface

Decision Arena includes a Research Campaigns panel with:

- Campaign Name
- Status
- Runs
- Candidates
- Best Candidate
- Best Score
- Current Champion
- Progress
- Run Campaign action

## Architectural Goal

Research Campaign

↓

Research Laboratory

↓

Research Memory

↓

Evolution

↓

Evaluation

↓

Tournament

↓

Capital Allocation

↓

Human Review

This sequence remains deterministic and no-drift.
