# Persistent Research Layer v1

## Purpose

Persistent Research Layer v1 replaces transient in-memory research history with PostgreSQL-backed storage so research artifacts survive API restarts.

This phase is persistence-only:
- No public research API contract changes.
- No strategy-generation logic changes.
- No decision-quality algorithm changes.

## Scope

The following research artifacts are persisted:
- Laboratory runs
- Strategy candidates (baseline and evolved)
- Candidate lineage
- Candidate evaluations
- Memory events
- Campaign metadata
- Campaign statistics
- Agent activity

## Data Model

New SQLAlchemy models:
- `research_laboratory_runs`
- `research_campaigns`
- `research_candidates`
- `research_candidate_lineage`
- `research_candidate_evaluations`
- `research_memory_entries`
- `research_agent_activity`
- `research_campaign_statistics`

Migration:
- `apps/api/app/db/migrations/versions/20260709_0016_add_persistent_research_layer.py`

## Repository Boundary

`ResearchPersistenceRepository` centralizes DB read/write behavior for research state.

Primary write paths:
- `record_laboratory_run`
- `record_evolved_candidates`
- `create_campaign`
- `upsert_campaign_statistics`

Primary read paths:
- `get_summary`
- `list_runs`
- `list_candidates`
- `list_tournament_outcomes`
- `get_laboratory_status`
- `get_campaign`
- `list_campaigns`
- `list_strategy_candidates`

## Route Integration

Research routes now use async DB sessions (`get_db`) and repository methods for durable reads and writes:
- `/research/laboratory`
- `/research/laboratory/run`
- `/research/memory`
- `/research/memory/runs`
- `/research/memory/candidates`
- `/research/evolve`
- `/research/evolution-analytics`
- `/research/campaigns`
- `/research/campaigns/{campaign_id}`
- `/research/campaigns/{campaign_id}/run`
- `/research/candidates`
- `/research/llm-adapters/openai/generate-candidates` (read context only)

Backward compatibility details:
- Existing response schemas are preserved.
- Pagination support (`limit`, `offset`) was added with defaults to preserve prior behavior.

## Startup Flush Strategy

A startup flush bridges old singleton memory to persistent storage:
- Function: `flush_legacy_research_state`
- Startup hook: `create_app` in `app/main.py`

Behavior:
- If DB already contains research history, flush is skipped (idempotent gate).
- If DB is empty, in-memory research memory and campaign state are persisted.
- After successful flush, legacy in-memory stores are cleared.
- Startup does not fail if DB is unavailable; flush is skipped with warning logging.

## Limitations (v1)

- Legacy in-memory records do not carry full historical `strategy_name` and generation timestamps for all recovered entries. Recovery uses deterministic fallback labels where necessary.
- Campaign best candidate recovery during startup flush stores aggregate quality/champion state; exact candidate linkage is only guaranteed for post-migration writes.
- Evolution analytics in routes currently derives from repository list providers; aggregation-query optimization can be expanded in a follow-up iteration.

## Operational Notes

Recommended sequence:
1. Apply Alembic migration.
2. Start API once to perform startup flush (if legacy in-memory state exists).
3. Verify `/research/memory` and `/research/campaigns` return persisted data after restart.

## Future Evolution

Potential v2 improvements:
- Full SQL aggregation queries for analytics endpoints.
- Explicit migration watermark table for startup flush auditing.
- Backfill job for enriched metadata reconstruction.
- Hard deprecation path for legacy singleton registries.
