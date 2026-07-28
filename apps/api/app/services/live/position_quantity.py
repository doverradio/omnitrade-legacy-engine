from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.controlled_proof_run import ControlledProofRun

# The only LiveAccountingRecord.record_type values that represent a genuine
# change in base-asset quantity owned. record_live_fill_reconciliation
# (accounting_reconciliation.py) always writes a fee_attribution row
# alongside every fill_accounting/partial_fill_accounting row, carrying the
# SAME filled_quantity and side as pure fee/cash-audit metadata -- it is
# never itself evidence of an executed quantity. Every other quantity
# calculation in this codebase already scopes to exactly this pair
# (load_position_snapshots, risk_accounting_snapshot, capital_ledger's
# per-order projection, canonical_campaign_binding's open-position counters,
# commissioned_entry_execution's fill loader); this is that same canonical
# rule, centralized so profile+symbol ownership checks stop re-deriving it
# ad hoc.
QUANTITY_BEARING_RECORD_TYPES = ("fill_accounting", "partial_fill_accounting")


async def compute_signed_owned_quantity(
    *, db: AsyncSession, live_trading_profile_id: uuid.UUID, symbol: str,
) -> Decimal:
    """Authoritative signed base-asset quantity for one live trading profile
    + exact symbol: sum(BUY fill quantity) - sum(SELL fill quantity),
    scoped to QUANTITY_BEARING_RECORD_TYPES. Deliberately no
    capital_campaign_id filter -- a real owned position belongs to the live
    account, not to whichever internal campaign row happened to be
    governing when it was opened (capital_campaign_id can legitimately be
    NULL, see accounting_reconciliation._resolve_campaign_for_live_order's
    "uncategorized" outcome)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(case(
            (LiveAccountingRecord.side == "buy", LiveAccountingRecord.filled_quantity),
            else_=-LiveAccountingRecord.filled_quantity,
        )), Decimal("0"))).where(
            LiveAccountingRecord.live_trading_profile_id == live_trading_profile_id,
            LiveAccountingRecord.symbol == symbol,
            LiveAccountingRecord.record_type.in_(QUANTITY_BEARING_RECORD_TYPES),
        )
    )
    return Decimal(str(total or 0))


async def owned_position_exists(
    *, db: AsyncSession, live_trading_profile_id: uuid.UUID, symbol: str,
) -> bool:
    quantity = await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=live_trading_profile_id, symbol=symbol,
    )
    return quantity > Decimal("0")


async def compute_controlled_proof_owned_quantity(
    *, db: AsyncSession, proof_id: uuid.UUID,
) -> Decimal:
    """Quantity attributable only to one Controlled Proof's linked orders."""
    proof = await db.scalar(
        select(ControlledProofRun).where(ControlledProofRun.proof_id == proof_id).limit(1)
    )
    if proof is None or proof.buy_live_crypto_order_id is None:
        return Decimal("0")
    linked_order_ids = [proof.buy_live_crypto_order_id]
    if proof.sell_live_crypto_order_id is not None:
        linked_order_ids.append(proof.sell_live_crypto_order_id)
    total = await db.scalar(
        select(func.coalesce(func.sum(case(
            (LiveAccountingRecord.side == "buy", LiveAccountingRecord.filled_quantity),
            else_=-LiveAccountingRecord.filled_quantity,
        )), Decimal("0"))).where(
            LiveAccountingRecord.live_crypto_order_id.in_(linked_order_ids),
            LiveAccountingRecord.record_type.in_(QUANTITY_BEARING_RECORD_TYPES),
        )
    )
    return Decimal(str(total or 0))
