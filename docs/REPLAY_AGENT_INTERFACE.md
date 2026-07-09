# Replay Agent Interface v1

## Purpose

Replay agents are read-only research components that consume immutable Decision Packages and produce Replay Results. They do not participate in production execution, scheduling, or writes.

## Lifecycle

Decision Package -> Replay Agent -> Replay Result -> Decision Quality Engine -> AI Coach -> Decision Intelligence

## Canonical Contract

A replay agent must:

- Accept a Decision Package identifier or payload.
- Produce a Replay Result.
- Remain read-only.
- Avoid production execution, state mutation, scheduling, and writes.

## Replay Result

Replay Result is the immutable output of a replay agent. It should contain:

- `replay_id`
- `replay_agent_id`
- `strategy_name`
- `decision_package_id`
- `replay_timestamp`
- `decision_outcome` with one of `BUY`, `SELL`, or `HOLD`
- `confidence`
- `supporting_evidence`
- `explanation`
- `simulated_execution_metrics`
- `risk_assessment`
- `quality_metrics`
- `metadata`

## Agent Interface

Replay agents implement a read-only contract with the following shape:

```text
ReplayAgent
  replay_agent_id
  name
  status
  replay(decision_package_id) -> ReplayResult
```

## Registration

The system begins with a single placeholder registration:

- `Default Replay Agent`
- `Registered`
- `Decision Package consumer`
- no execution logic
- no processing
- no scheduling
- no writes

## Non-Goals

- No replay implementation.
- No AI implementation.
- No scoring implementation.
- No production execution changes.
- No workers or scheduler.
