from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.schemas.operator_action import OperatorActionCreateRequest, OperatorActionResponse
from app.services.operator_actions import get_operator_action, list_operator_actions, submit_operator_action
from app.services.operator_actions.service import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT
from app.services.orchestration.continuous_pipeline_worker import schedule_controlled_proof_immediate_dispatch

router = APIRouter(prefix="/api/v1/operator/actions", tags=["operator-actions"])

_DESCRIPTION = """
Generic, auditable control-plane for repeatable operator actions.

Supported `action_type`:
- **RUN_CONTROLLED_PROOF** -- delegates to the existing Controlled Proof
  service (`app.services.controlled_proof`). Allowed `parameters`:
  `product_id` (str, required), `expires_in_minutes` (int, 1-180, default
  60), and `replace_active` (bool, default `false`). No other parameters
  are accepted; unknown fields are rejected. All Controlled Proof
  server-enforced constraints (Kraken production only, the pinned
  campaign, the $5 maximum notional, one proof at a time) remain
  authoritative and unchanged by this API.

  By default, a request while another proof is already active fails
  closed with "Another controlled proof is already active". Setting
  `replace_active: true` instead atomically cancels the existing active
  proof and creates this one in its place -- but *only* when the existing
  proof has not crossed a live-capital boundary (no live BUY/SELL order,
  no open position). If it has, replacement is refused with a precise
  error naming the blocking artifact
  (`live_buy_order_exists` / `live_sell_order_exists` / `open_position_exists`)
  and the existing proof is left completely untouched -- a proof that may
  control real funds is never cancelled or superseded. On a genuine
  replacement, the cancelled proof is preserved (status `CANCELLED`,
  `cancelled_at`/`cancelled_by` set, reason `replaced_by_operator_request`)
  with a durable audit link to its replacement; nothing is ever deleted.
  `result` includes `replaced_proof_id`, `replacement_performed`, and
  `replacement_reason` reflecting whether this action's own request caused
  a replacement. Automatic expiration reaping still runs independently of
  `replace_active` and remains the fallback path for stalled proofs.

  Example:
  ```json
  {
    "action_type": "RUN_CONTROLLED_PROOF",
    "idempotency_key": "operator-2026-07-26-btc-retry",
    "parameters": {"product_id": "BTC-USD", "expires_in_minutes": 60, "replace_active": true}
  }
  ```

Status lifecycle: `REQUESTED` -> `ACCEPTED` -> `IN_PROGRESS` -> `SUCCEEDED`,
with `BLOCKED` / `FAILED` / `CANCELLED` / `EXPIRED` as additional terminal
outcomes. For RUN_CONTROLLED_PROOF, status is a live projection of the
linked ControlledProofRun's real state -- a completed lifecycle that lost
money is still SUCCEEDED as an action (the requested operation completed),
with the actual verdict and net P&L preserved in `result` and never
reported as profit.

`linked_resource_type`/`linked_resource_id` identify the delegated domain
resource (e.g. `controlled_proof_run` / the proof's `proof_id`).

Idempotency: `idempotency_key` is unique per action at the database level.
Replaying the same key -- even across a process restart -- returns the
original action and never re-invokes the underlying domain service a
second time.
"""


@router.post("", response_model=OperatorActionResponse, status_code=201, description=_DESCRIPTION)
async def post_operator_action(
    payload: OperatorActionCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await submit_operator_action(
        db=db, action_type=payload.action_type, idempotency_key=payload.idempotency_key,
        parameters=payload.parameters, actor=current_user["id"],
    )
    # Scheduled here, deliberately after submit_operator_action has already
    # returned (and therefore already committed the transaction that
    # created/accepted the proof) -- scheduling any earlier, e.g. inside
    # the RUN_CONTROLLED_PROOF handler itself, would risk the dispatch's
    # own fresh session racing the still-uncommitted proof row under READ
    # COMMITTED isolation. Fire-and-forget: does not delay this response,
    # and its own failure is independently logged and falls back to the
    # regular candle-driven poll -- never surfaced as a failure of this
    # request, which already succeeded.
    if result.get("action_type") == "RUN_CONTROLLED_PROOF" and result.get("linked_resource_type") == "controlled_proof_run":
        linked_resource_id = result.get("linked_resource_id")
        if linked_resource_id is not None:
            schedule_controlled_proof_immediate_dispatch(proof_id=linked_resource_id)
    return result


@router.get("/{action_id}", response_model=OperatorActionResponse)
async def get_operator_action_route(
    action_id: uuid.UUID,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await get_operator_action(db=db, action_id=action_id)


@router.get("", response_model=list[OperatorActionResponse])
async def list_operator_actions_route(
    action_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_operator_actions(db=db, action_type=action_type, status=status, limit=limit)
