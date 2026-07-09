# Candidate Evaluation Pipeline v1

## Purpose
Candidate Evaluation Pipeline v1 connects research-generated strategy candidates to the existing deterministic replay and evaluation stack.

The pipeline is research-only and does not affect production execution.

## Candidate Lifecycle
Research Agent -> Candidate Strategy -> Replay -> Decision Quality -> AI Coach -> Decision Intelligence -> Tournament -> Candidate Evaluation -> Human Review.

CandidateEvaluation fields:
- evaluation_id
- candidate_id
- replay_status
- decision_quality_score
- ai_coach_summary
- decision_intelligence_summary
- tournament_rank
- promotion_eligible

v1 promotion_eligible is always false.

## Repository Boundaries
Allowed:
- Deterministic candidate replay/evaluation synthesis
- Read-only candidate evaluation endpoint
- UI presentation of research-only evaluation evidence

Not allowed:
- Production writes
- Live trading
- Execution path changes
- Automatic strategy promotion

## Future Evolution
Future versions may add richer replay lineage, cross-candidate cohort benchmarking, and explicit human-review workflow states.

Any future promotion decision remains gated behind explicit human approval and separate controls.
