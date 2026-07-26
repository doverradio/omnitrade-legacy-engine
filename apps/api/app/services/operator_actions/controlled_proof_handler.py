from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator_action import OperatorAction
from app.services.controlled_proof import create_controlled_proof, get_controlled_proof_view
from app.services.operator_actions.registry import (
    OperatorActionHandler,
    OperatorActionProjection,
    OperatorActionSubmission,
    register_action_handler,
)

ACTION_TYPE = "RUN_CONTROLLED_PROOF"

# Every controlled-proof-active state maps to IN_PROGRESS. REQUESTED is
# deliberately absent: by the time an Operator Action is linked, it has
# already advanced to ACCEPTED, and a proof cannot regress to REQUESTED
# once linked.
_PROOF_ACTIVE_STATES = {
    "CLAIMED", "ENTRY_PROPOSED", "PACKAGE_CREATED", "POSITION_OPEN",
    "WAITING_FOR_PROFITABLE_EXIT", "EXITED",
}
_PROOF_RECONCILED_STATES = {"RECONCILED", "PROFIT_CONFIRMED"}
_PROVEN_VERDICTS = {"LIFECYCLE_PROVEN_PROFIT", "LIFECYCLE_PROVEN_LOSS", "LIFECYCLE_PROVEN_FLAT"}


class RunControlledProofParameters(BaseModel):
    """The only caller-controlled surface for RUN_CONTROLLED_PROOF. No
    provider, campaign, environment, notional, mandate, strategy, actor, or
    execution setting is accepted here -- those remain server-enforced
    constants inside app.services.controlled_proof, unchanged by this API."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    expires_in_minutes: int = Field(default=60, ge=1, le=180)


def _derive_proof_idempotency_key(action_idempotency_key: str) -> str:
    """Deterministic, namespaced from the Operator Action's own idempotency
    key -- so a replay (even after a process restart) always resolves to
    the SAME ControlledProofRun via that row's own DB-level unique
    constraint, never a second one, without relying on any in-memory state."""
    return f"operator-action:{action_idempotency_key}"


async def _submit(db: AsyncSession, action: OperatorAction, parameters: BaseModel) -> OperatorActionSubmission:
    assert isinstance(parameters, RunControlledProofParameters)
    proof = await create_controlled_proof(
        db=db,
        product_id=parameters.product_id,
        idempotency_key=_derive_proof_idempotency_key(action.idempotency_key),
        expires_in_minutes=parameters.expires_in_minutes,
        actor=action.actor,
    )
    return OperatorActionSubmission(linked_resource_type="controlled_proof_run", linked_resource_id=proof.proof_id)


async def _project(db: AsyncSession, action: OperatorAction) -> OperatorActionProjection:
    if action.linked_resource_id is None:
        return OperatorActionProjection(status=action.status, result=action.result)

    view = await get_controlled_proof_view(db=db, proof_id=uuid.UUID(str(action.linked_resource_id)))
    proof_status = str(view["status"])
    terminal_verdict = view.get("terminal_verdict")
    result: dict[str, Any] = {
        "controlled_proof_id": str(action.linked_resource_id),
        "controlled_proof_status": proof_status,
        "terminal_verdict": terminal_verdict,
        "net_pnl_usd": None if view.get("net_pnl_usd") is None else str(view["net_pnl_usd"]),
        "fees_usd": None if view.get("fees_usd") is None else str(view["fees_usd"]),
    }

    if proof_status == "REQUESTED":
        status = "ACCEPTED"
    elif proof_status in _PROOF_ACTIVE_STATES:
        status = "IN_PROGRESS"
    elif proof_status in _PROOF_RECONCILED_STATES:
        # Never label a loss (or an unresolved verdict) a success: only a
        # real, already-computed LIFECYCLE_PROVEN_* verdict counts.
        status = "SUCCEEDED" if terminal_verdict in _PROVEN_VERDICTS else "IN_PROGRESS"
    elif proof_status == "BLOCKED":
        status = "BLOCKED"
    elif proof_status == "FAILED":
        status = "FAILED"
    elif proof_status == "CANCELLED":
        status = "CANCELLED"
    elif proof_status == "EXPIRED":
        status = "EXPIRED"
    else:
        status = action.status

    return OperatorActionProjection(
        status=status,
        result=result,
        blocked_reason=view.get("blocked_reason") if status == "BLOCKED" else None,
        failure_reason=view.get("failure_reason") if status == "FAILED" else None,
    )


register_action_handler(
    ACTION_TYPE,
    OperatorActionHandler(parameters_model=RunControlledProofParameters, submit=_submit, project=_project),
)
