from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.schemas.controlled_proof import (
    ControlledProofCancelRequest,
    ControlledProofCreateRequest,
    ControlledProofStartResponse,
    ControlledProofExitRecoveryCreateRequest,
    ControlledProofExitRecoveryResponse,
    ControlledProofResponse,
)
from app.services.controlled_proof import (
    cancel_controlled_proof,
    start_live_controlled_proof,
    get_controlled_proof_view,
    authorize_controlled_proof_exit_recovery,
    get_exit_recovery_view,
)
from app.services.orchestration.continuous_pipeline_worker import (
    schedule_controlled_proof_exit_recovery_dispatch,
    schedule_controlled_proof_immediate_dispatch,
)

router = APIRouter(prefix="/api/v1/operator/controlled-proofs", tags=["controlled-proofs"])


@router.post("", response_model=ControlledProofStartResponse, status_code=201)
async def post_controlled_proof(
    payload: ControlledProofCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> ControlledProofStartResponse:
    result = await start_live_controlled_proof(
        db=db, product_id=payload.product, notional_usd=payload.notional_usd,
        idempotency_key=payload.idempotency_key, expires_in_minutes=payload.expires_in_minutes,
        actor=current_user["id"],
    )
    proof = result.proof
    schedule_controlled_proof_immediate_dispatch(proof_id=proof.proof_id)
    return ControlledProofStartResponse(
        proof_id=proof.proof_id, status=proof.status, product=proof.product_id,
        notional_usd=proof.max_notional_usd, live_execution=True,
        new_proof_created=result.created, idempotent_replay=not result.created,
        reused_historical_execution=False, audit_correlation_id=proof.audit_correlation_id,
    )


@router.get("/{proof_id}", response_model=ControlledProofResponse)
async def get_controlled_proof(
    proof_id: uuid.UUID,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> ControlledProofResponse:
    view = await get_controlled_proof_view(db=db, proof_id=proof_id)
    return ControlledProofResponse(**view)


@router.post("/{proof_id}/cancel", response_model=ControlledProofResponse)
async def post_controlled_proof_cancel(
    proof_id: uuid.UUID,
    payload: ControlledProofCancelRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> ControlledProofResponse:
    await cancel_controlled_proof(db=db, proof_id=proof_id, actor=current_user["id"], reason=payload.reason)
    view = await get_controlled_proof_view(db=db, proof_id=proof_id)
    return ControlledProofResponse(**view)


@router.post("/{proof_id}/exit-recovery", response_model=ControlledProofExitRecoveryResponse, status_code=201)
async def post_controlled_proof_exit_recovery(
    proof_id: uuid.UUID,
    payload: ControlledProofExitRecoveryCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> ControlledProofExitRecoveryResponse:
    await authorize_controlled_proof_exit_recovery(
        db=db, proof_id=proof_id, idempotency_key=payload.idempotency_key,
        expires_in_minutes=payload.expires_in_minutes, actor=current_user["id"],
    )
    schedule_controlled_proof_exit_recovery_dispatch(proof_id=proof_id)
    return ControlledProofExitRecoveryResponse(**await get_exit_recovery_view(db=db, proof_id=proof_id))


@router.get("/{proof_id}/exit-recovery", response_model=ControlledProofExitRecoveryResponse)
async def get_controlled_proof_exit_recovery(
    proof_id: uuid.UUID,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> ControlledProofExitRecoveryResponse:
    return ControlledProofExitRecoveryResponse(**await get_exit_recovery_view(db=db, proof_id=proof_id))
