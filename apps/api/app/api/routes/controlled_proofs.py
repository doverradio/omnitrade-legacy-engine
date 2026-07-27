from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_authorized_operator
from app.db.session import get_db
from app.schemas.controlled_proof import (
    ControlledProofCancelRequest,
    ControlledProofCreateRequest,
    ControlledProofResponse,
)
from app.services.controlled_proof import (
    cancel_controlled_proof,
    create_controlled_proof,
    get_controlled_proof_view,
)

router = APIRouter(prefix="/api/v1/operator/controlled-proofs", tags=["controlled-proofs"])


@router.post("", response_model=ControlledProofResponse, status_code=201)
async def post_controlled_proof(
    payload: ControlledProofCreateRequest,
    current_user: dict[str, str] = Depends(get_authorized_operator),
    db: AsyncSession = Depends(get_db),
) -> ControlledProofResponse:
    proof, _replaced_proof = await create_controlled_proof(
        db=db, product_id=payload.product_id, idempotency_key=payload.idempotency_key,
        expires_in_minutes=payload.expires_in_minutes, actor=current_user["id"],
        replace_active=payload.replace_active,
    )
    view = await get_controlled_proof_view(db=db, proof_id=proof.proof_id)
    return ControlledProofResponse(**view)


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
