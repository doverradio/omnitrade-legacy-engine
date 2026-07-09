# AI Coach v0

## Purpose
AI Coach v0 is the first deterministic observation layer that consumes Decision Quality results and turns them into structured, read-only observations.

It is not an LLM, not machine learning, and not an optimization system.

## Inputs
The coach consumes a Decision Quality Result with fields such as:
- quality_score
- decision_reproduced
- action_matches_original
- confidence_matches_original
- replay_duration_ms
- evaluation_timestamp

## Outputs
The coach returns an AI Coach Observation with:
- observation_id
- evaluation_timestamp
- summary
- strengths
- weaknesses
- confidence_note
- reproducibility_note
- suggested_follow_up

## Repository Boundaries
AI Coach belongs to the observational layer only.

Allowed:
- Read Decision Quality results
- Generate deterministic observations
- Surface observations in the Strategy Arena

Not allowed:
- LLM calls
- Production writes
- Execution changes
- Strategy optimization
- Learning systems
- Scheduler work
- Worker loops

## Future Evolution
Future versions may add richer rule sets and more nuanced observation categories while staying deterministic and read-only.

Any future change must preserve the v0 contract and must not introduce AI model calls or production side effects.
