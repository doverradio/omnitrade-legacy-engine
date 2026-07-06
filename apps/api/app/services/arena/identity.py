from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any


def _stable_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_payload(payload).encode("utf-8")).hexdigest()


def build_arena_competition_idempotency_key(
    *,
    competition_identity: str,
    master_account_id: uuid.UUID,
    paper_portfolio_id: uuid.UUID,
) -> str:
    return _hash_payload(
        {
            "kind": "arena_competition",
            "competition_identity": competition_identity,
            "master_account_id": str(master_account_id),
            "paper_portfolio_id": str(paper_portfolio_id),
        }
    )


def build_arena_tournament_idempotency_key(
    *,
    tournament_identity: str,
    competition_identity: str,
    sequence_number: int,
) -> str:
    return _hash_payload(
        {
            "kind": "arena_tournament",
            "tournament_identity": tournament_identity,
            "competition_identity": competition_identity,
            "sequence_number": sequence_number,
        }
    )


def build_arena_cycle_idempotency_key(
    *,
    cycle_identity: str,
    tournament_identity: str,
    cycle_number: int,
) -> str:
    return _hash_payload(
        {
            "kind": "arena_cycle",
            "cycle_identity": cycle_identity,
            "tournament_identity": tournament_identity,
            "cycle_number": cycle_number,
        }
    )


def build_arena_participating_agent_idempotency_key(
    *,
    agent_identity: str,
    competition_identity: str,
    strategy_id: str,
    strategy_version: str,
) -> str:
    return _hash_payload(
        {
            "kind": "arena_participating_agent",
            "agent_identity": agent_identity,
            "competition_identity": competition_identity,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
        }
    )


def build_arena_lifecycle_identity(
    *,
    namespace: str,
    competition_identity: str,
    ordinal: int,
    as_of: datetime,
) -> str:
    return _hash_payload(
        {
            "kind": "arena_lifecycle_identity",
            "namespace": namespace,
            "competition_identity": competition_identity,
            "ordinal": ordinal,
            "as_of": as_of.isoformat(),
        }
    )