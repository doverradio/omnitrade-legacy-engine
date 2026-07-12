from __future__ import annotations

from app.services.mandates.contracts import MANDATE_STATUSES


MANDATE_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_AUTHORIZATION"},
    "PENDING_AUTHORIZATION": {"AUTHORIZED", "REVOKED"},
    "AUTHORIZED": {"ACTIVE", "REVOKED", "EXPIRED"},
    "ACTIVE": {"PAUSED", "EXIT_ONLY", "REVOKED", "KILLED", "EXPIRED", "COMPLETED"},
    "PAUSED": {"ACTIVE", "EXIT_ONLY", "REVOKED", "KILLED", "EXPIRED"},
    "EXIT_ONLY": {"ACTIVE", "REVOKED", "KILLED", "COMPLETED", "EXPIRED"},
    "EXPIRED": set(),
    "REVOKED": set(),
    "KILLED": set(),
    "COMPLETED": set(),
}


def is_valid_state(status: str) -> bool:
    return status in MANDATE_STATUSES


def validate_transition(*, from_status: str, to_status: str) -> bool:
    if from_status not in MANDATE_ALLOWED_TRANSITIONS:
        return False
    return to_status in MANDATE_ALLOWED_TRANSITIONS[from_status]
