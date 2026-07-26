from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class OperatorActionCreateRequest(BaseModel):
    """The generic action envelope. `actor` is intentionally absent -- the
    authenticated operator identity always comes from get_authorized_operator,
    never from the request body. `parameters` accepts only the fields the
    named action_type's own strict schema allows (see
    app.services.operator_actions.controlled_proof_handler.RunControlledProofParameters
    for RUN_CONTROLLED_PROOF); unknown fields are rejected fail-closed."""

    action_type: str
    idempotency_key: str
    parameters: dict[str, Any] = {}


class OperatorActionResponse(BaseModel):
    action_id: uuid.UUID
    action_type: str
    status: str
    actor: str
    idempotency_key: str
    parameters: dict[str, Any]
    result: dict[str, Any] | None
    linked_resource_type: str | None
    linked_resource_id: uuid.UUID | None
    blocked_reason: str | None
    failure_reason: str | None
    requested_at: datetime
    accepted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None
