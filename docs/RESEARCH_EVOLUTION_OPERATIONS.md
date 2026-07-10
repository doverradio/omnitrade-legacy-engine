# Research Evolution Operations

## Scope

This document describes the deterministic paper-only research cycle activated from the orchestration worker.

## Trigger

The worker may start a deterministic research cycle when all of the following are true:
- `RESEARCH_EVOLUTION_ENABLED=true`
- no persisted research campaign is currently `RUNNING`
- the latest persisted laboratory run is older than `RESEARCH_CYCLE_INTERVAL_MINUTES`

Default interval:
- `RESEARCH_CYCLE_INTERVAL_MINUTES=30`

## Bounded Configuration

Safe defaults:
- `RESEARCH_EVOLUTION_ENABLED=true`
- `RESEARCH_CYCLE_INTERVAL_MINUTES=30`
- `RESEARCH_MAX_CANDIDATES_PER_CYCLE=6`
- `RESEARCH_MAX_DESCENDANTS_PER_CANDIDATE=3`
- `RESEARCH_MAX_GENERATION=5`
- `RESEARCH_MIN_DECISIONS=50`
- `RESEARCH_MIN_ACTIONABLE_SIGNALS=5`
- `RESEARCH_MIN_TRADES=3`

## Candidate Lifecycle

The deterministic cycle performs:
1. research cycle start
2. baseline candidate generation
3. candidate evaluation
4. persistence to research candidate / evaluation / memory tables
5. bounded descendant generation
6. descendant persistence
7. tournament ranking summary
8. conservative champion decision
9. campaign statistics update
10. Validation Run research timeline event emission

## Deterministic Candidate Generation

The baseline generator does not depend on OpenAI.

It produces bounded, reproducible candidates such as:
- MA/RSI blend variants
- RSI period variants
- MA fast/slow variants
- threshold variants

## Evaluation and Promotion

Promotion remains conservative.

A champion is only populated when aggregate paper evidence clears the configured minimums for:
- decisions
- actionable signals
- trades

If thresholds are not met, champion remains `null`.

## Tournament

The cycle computes a deterministic tournament-style ranking from persisted candidate evaluations.

No artificial winner is required.
If evidence thresholds are not met, a champion is not promoted.

## Lineage and Descendants

Descendants persist:
- parent candidate id
- generation
- mutation reason
- parameter diff
- deterministic evaluation results

## Research Memory

Research memory grows through persisted entries for:
- laboratory runs
- laboratory candidates
- candidate evaluations
- evolved candidates

## Validation Run Integration

Active Validation Runs can observe research activity through persisted counts and events such as:
- `RESEARCH_CYCLE_STARTED`
- `CANDIDATE_GENERATED`
- `CANDIDATE_EVALUATED`
- `EVOLUTION_DESCENDANT_CREATED`
- `TOURNAMENT_COMPLETED`
- `CHAMPION_SELECTED`
- `RESEARCH_MEMORY_UPDATED`

## Live Trading Isolation

Research activation remains paper-only.

It does not:
- enable live trading
- alter Coinbase balances
- submit live orders
- promote research candidates into live execution
