# Decision Quality Engine v0

## Purpose
Decision Quality Engine v0 is the first deterministic evaluation layer for Replay Results.
It scores how faithfully a replay reconstructed a historical production decision.

This layer is observational only.
It does not optimize strategies, learn from outcomes, mutate production state, schedule work, or execute trades.

## Inputs
The evaluator consumes a Replay Result.

Required input fields:
- replay_id
- replay_agent_id
- decision_package_id
- replay_timestamp
- reconstructed_action
- reconstructed_confidence
- supporting_evidence
- explanation
- metadata

For v0, the evaluator expects replay metadata to carry the original decision context when available:
- original_action
- original_confidence
- replay_duration_ms

## Outputs
The evaluator returns a Decision Quality Result with:
- quality_score
- decision_reproduced
- action_matches_original
- confidence_matches_original
- replay_duration_ms
- evaluation_timestamp

Placeholder fields for future evolution:
- calibration
- opportunity_cost
- drawdown
- risk_adjusted_return
- explanation_quality

## Evaluation Philosophy
v0 is deterministic and read-only.

The engine compares replay output against immutable replay metadata and produces a stable score.
It does not infer hidden intent, estimate future performance, or use AI/ML models.

Scoring is intentionally simple in v0:
- 100 when both action and confidence match the original decision
- 50 when exactly one matches
- 0 when neither matches

## Repository Boundaries
Decision Quality Engine belongs strictly to the evidence/replay layer.

Allowed:
- Read Replay Results
- Compare replay metadata
- Return a read-only quality assessment
- Surface evaluation in the Strategy Arena

Not allowed:
- Production writes
- Execution changes
- Scheduler work
- Worker loops
- Strategy optimization
- Learning systems
- AI Coach logic
- Decision Intelligence inference

## Future Evolution
Future versions may add richer deterministic metrics while keeping the same read-only boundary:
- Calibration against historical confidence bands
- Opportunity cost estimates from immutable alternative paths
- Drawdown analysis tied to replayed decision windows
- Risk-adjusted return summaries
- Explanation quality heuristics based on evidence completeness

Any future evolution must preserve deterministic behavior for the v0 contract and remain separate from AI or production execution paths.
