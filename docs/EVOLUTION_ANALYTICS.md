# Evolution Analytics Dashboard v1

## Purpose

Evolution Analytics Dashboard v1 provides deterministic visibility into long-term research progress.

This scope is analytics only:

- no production writes
- no execution changes
- no AI systems

## Data Source

The analytics service reuses existing Research Memory records.

No duplicate storage is introduced.

## Metrics

The dashboard exposes:

- total_laboratory_runs
- total_candidates_generated
- total_evolved_candidates
- average_quality_score
- best_quality_score
- best_candidate
- successful_mutations
- unsuccessful_mutations
- generation_distribution
- lineage_depth
- top_research_agent

## API

Read-only endpoint:

- GET /research/evolution-analytics

## Charts

The Decision Arena analytics panel renders:

1. Quality Score over Time
2. Candidates Generated per Laboratory Run
3. Generation Distribution
4. Mutation Success Rate
5. Research Agent Leaderboard
6. Largest Lineage Tree

## Architecture Placement

Research Memory

-> Evolution Analytics

-> Human Insight

-> Future Adaptive Research
