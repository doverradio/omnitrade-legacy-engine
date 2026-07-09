# Evolution Engine v1

## Purpose

Evolution Engine v1 creates second-generation deterministic strategy candidates from prior successful research candidates.

This is deterministic evolutionary search only:

- no machine learning
- no LLM
- no stochastic optimization
- no production writes
- no automatic promotion

## Inputs

Evolution Engine reads Research Memory candidate history and selects top-performing parents.

Optional inputs:

- parent_candidate_id
- generation_limit

## Deterministic Mutation Rules

Mutations are fixed and reproducible.

RSI example:

- 14 -> 12
- 14 -> 16

MA crossover example:

- 20/100 -> 18/100
- 20/100 -> 22/100
- 20/100 -> 20/90
- 20/100 -> 20/110

No random seeds are used.

## Lineage Tracking

Every evolved candidate stores:

- parent_candidate_id
- generation
- mutation_reason
- parameter_diff

## API

POST /research/evolve

Returns generated deterministic descendants, including lineage and deterministic evaluation fields.

## Architecture Placement

Research Memory

-> Evolution Engine

-> Research Laboratory

-> Candidate Evaluation

-> Tournament

-> Capital Allocation

-> Human Review
