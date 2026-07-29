from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.services.live.risk_accounting_snapshot import RiskAccountingUnavailableError


def _d(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def compute_controlled_proof_open_exposure_usd(
    *, db: AsyncSession, live_trading_profile_id: uuid.UUID,
) -> Decimal:
    """Current open exposure attributable to Controlled Proof executions
    only -- never ordinary autonomous production trades, even though both
    currently share the same campaign/profile scope
    (controlled_proof.service.ALLOWED_CAMPAIGN_ID).

    Mirrors build_risk_accounting_snapshot's per-symbol quantity/cost-basis
    tracking (services/live/risk_accounting_snapshot.py:148-179), but scopes
    the source accounting rows to only those tied to a live_crypto_order_id
    some ControlledProofRun itself references (buy_live_crypto_order_id /
    sell_live_crypto_order_id) -- across every Controlled Proof ever run for
    this profile, not just "today" -- so exposure correctly returns to zero
    the moment a proof's SELL fully reconciles, regardless of calendar day,
    unlike the ordinary daily_deployed_usd metric it replaces for
    CONTROLLED_PROOF-purpose mandates (see eligibility.py).
    """
    order_id_pairs = (
        await db.execute(
            select(
                ControlledProofRun.buy_live_crypto_order_id,
                ControlledProofRun.sell_live_crypto_order_id,
            )
        )
    ).all()
    order_ids = {oid for pair in order_id_pairs for oid in pair if oid is not None}
    if not order_ids:
        return Decimal("0")

    accounting = list(
        (
            await db.scalars(
                select(LiveAccountingRecord)
                .where(LiveAccountingRecord.live_trading_profile_id == live_trading_profile_id)
                .where(LiveAccountingRecord.live_crypto_order_id.in_(order_ids))
                .where(LiveAccountingRecord.record_type.in_(["fill_accounting", "partial_fill_accounting"]))
                .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.id.asc())
            )
        ).all()
    )

    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for row in accounting:
        qty = _d(row.filled_quantity)
        gross = _d(row.gross_notional)
        fee = _d(row.fee_amount)
        quantity, cost_basis, _last_price = positions.get(row.symbol, (Decimal("0"), Decimal("0"), Decimal("0")))
        last_price = _d(row.fill_price)
        if row.side == "buy":
            quantity += qty
            cost_basis += gross + fee
            positions[row.symbol] = (quantity, cost_basis, last_price)
            continue
        if qty > quantity or quantity <= 0:
            raise RiskAccountingUnavailableError(
                "controlled_proof_position_evidence_inconsistent",
                details={"accounting_record_id": str(row.id)},
            )
        average_cost = cost_basis / quantity
        quantity -= qty
        cost_basis -= average_cost * qty
        positions[row.symbol] = (quantity, cost_basis, last_price)

    if any(quantity < 0 or cost_basis < 0 for quantity, cost_basis, _price in positions.values()):
        raise RiskAccountingUnavailableError("controlled_proof_position_evidence_inconsistent")

    return sum(
        (quantity * last_price for quantity, _cost, last_price in positions.values() if quantity > 0),
        Decimal("0"),
    )
