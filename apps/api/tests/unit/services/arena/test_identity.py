from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.arena.identity import (
    build_arena_competition_idempotency_key,
    build_arena_cycle_idempotency_key,
    build_arena_lifecycle_identity,
    build_arena_participating_agent_idempotency_key,
    build_arena_tournament_idempotency_key,
)


def test_competition_idempotency_key_is_deterministic() -> None:
    master_account_id = uuid.uuid4()
    paper_portfolio_id = uuid.uuid4()

    key_a = build_arena_competition_idempotency_key(
        competition_identity="comp-1",
        master_account_id=master_account_id,
        paper_portfolio_id=paper_portfolio_id,
    )
    key_b = build_arena_competition_idempotency_key(
        competition_identity="comp-1",
        master_account_id=master_account_id,
        paper_portfolio_id=paper_portfolio_id,
    )

    assert key_a == key_b


def test_tournament_and_cycle_keys_change_with_identity_inputs() -> None:
    tournament_key = build_arena_tournament_idempotency_key(
        tournament_identity="tour-1",
        competition_identity="comp-1",
        sequence_number=1,
    )
    tournament_key_changed = build_arena_tournament_idempotency_key(
        tournament_identity="tour-2",
        competition_identity="comp-1",
        sequence_number=1,
    )
    cycle_key = build_arena_cycle_idempotency_key(
        cycle_identity="cycle-1",
        tournament_identity="tour-1",
        cycle_number=1,
    )
    cycle_key_changed = build_arena_cycle_idempotency_key(
        cycle_identity="cycle-1",
        tournament_identity="tour-1",
        cycle_number=2,
    )

    assert tournament_key != tournament_key_changed
    assert cycle_key != cycle_key_changed


def test_participating_agent_key_binds_strategy_version() -> None:
    key_v1 = build_arena_participating_agent_idempotency_key(
        agent_identity="agent-1",
        competition_identity="comp-1",
        strategy_id="momentum",
        strategy_version="v1",
    )
    key_v2 = build_arena_participating_agent_idempotency_key(
        agent_identity="agent-1",
        competition_identity="comp-1",
        strategy_id="momentum",
        strategy_version="v2",
    )

    assert key_v1 != key_v2


def test_lifecycle_identity_is_stable_and_time_sensitive() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    identity_a = build_arena_lifecycle_identity(
        namespace="tournament",
        competition_identity="comp-1",
        ordinal=1,
        as_of=as_of,
    )
    identity_b = build_arena_lifecycle_identity(
        namespace="tournament",
        competition_identity="comp-1",
        ordinal=1,
        as_of=as_of,
    )
    identity_c = build_arena_lifecycle_identity(
        namespace="tournament",
        competition_identity="comp-1",
        ordinal=2,
        as_of=as_of,
    )

    assert identity_a == identity_b
    assert identity_a != identity_c