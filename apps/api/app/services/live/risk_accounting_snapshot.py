from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capital_campaign import CapitalCampaign
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent


class RiskAccountingUnavailableError(RuntimeError):
    def __init__(self, reason_code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = details or {}


@dataclass(frozen=True)
class RiskAccountingSnapshot:
    current_open_exposure_usd: Decimal
    daily_deployed_usd: Decimal
    daily_realized_loss_usd: Decimal
    campaign_drawdown_usd: Decimal
    current_position_count: int
    as_of: datetime
    evidence_ids: dict[str, list[str]]
    campaign_id: UUID
    campaign_version: int
    account_id: UUID
    provider: str
    environment: str
    product: str


def _d(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def build_risk_accounting_snapshot(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    campaign_version: int,
    account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product: str,
    as_of: datetime | None = None,
) -> RiskAccountingSnapshot:
    """Build deterministic pre-submission mandate evidence from persisted live records.

    An absence of accounting rows is a proven zero only when the campaign exists and
    there are no unresolved submitted orders in the same canonical campaign scope.
    """
    observed_at = as_of or datetime.now(timezone.utc)
    campaign = await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == campaign_id)
        .where(CapitalCampaign.definition_version == campaign_version)
        .where(CapitalCampaign.paper_account_id == account_id)
        .limit(1)
    )
    if campaign is None or campaign.id is None:
        raise RiskAccountingUnavailableError("risk_accounting_incomplete", details={"missing": "campaign_accounting"})

    scoped_orders = list(
        (
            await db.scalars(
                select(LiveCryptoOrder)
                .join(CryptoOrderPreview, CryptoOrderPreview.crypto_order_preview_id == LiveCryptoOrder.crypto_order_preview_id)
                .join(CanonicalPreviewPackage, CanonicalPreviewPackage.crypto_order_preview_id == CryptoOrderPreview.crypto_order_preview_id)
                .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                .where(CanonicalPreviewPackage.campaign_version == campaign_version)
                .where(CanonicalPreviewPackage.paper_account_id == account_id)
                .where(CanonicalPreviewPackage.live_trading_profile_id == live_trading_profile_id)
                .where(LiveCryptoOrder.provider == provider)
                .where(LiveCryptoOrder.environment == environment)
                .order_by(LiveCryptoOrder.created_at.asc(), LiveCryptoOrder.live_crypto_order_id.asc())
            )
        ).all()
    )
    order_ids = [row.live_crypto_order_id for row in scoped_orders]
    reconciliations = []
    if order_ids:
        reconciliations = list(
            (
                await db.scalars(
                    select(LiveReconciliationEvent)
                    .where(LiveReconciliationEvent.live_crypto_order_id.in_(order_ids))
                    .order_by(LiveReconciliationEvent.recorded_at.asc(), LiveReconciliationEvent.id.asc())
                )
            ).all()
        )
    latest_reconciliation: dict[UUID, LiveReconciliationEvent] = {}
    for row in reconciliations:
        if row.live_crypto_order_id is not None:
            latest_reconciliation[row.live_crypto_order_id] = row

    unresolved_statuses = {"RECONCILIATION_REQUIRED", "UNKNOWN"}
    for order in scoped_orders:
        latest = latest_reconciliation.get(order.live_crypto_order_id)
        if order.status in unresolved_statuses or (
            order.submitted_at is not None
            and (latest is None or latest.reconciliation_status in {"open", "reconciliation_required", "unknown", "conflict", "balance_mismatch"})
        ):
            raise RiskAccountingUnavailableError(
                "unresolved_provider_exposure",
                details={"live_crypto_order_id": str(order.live_crypto_order_id), "status": order.status},
            )

    accounting = list(
        (
            await db.scalars(
                select(LiveAccountingRecord)
                .where(LiveAccountingRecord.capital_campaign_id == campaign.id)
                .where(LiveAccountingRecord.live_trading_profile_id == live_trading_profile_id)
                .where(LiveAccountingRecord.record_type.in_(["fill_accounting", "partial_fill_accounting"]))
                .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.id.asc())
            )
        ).all()
    )

    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    daily_deployed = Decimal("0")
    daily_realized_loss = Decimal("0")
    for row in accounting:
        qty = _d(row.filled_quantity)
        gross = _d(row.gross_notional)
        fee = _d(row.fee_amount)
        quantity, cost_basis, _last_price = positions.get(
            row.symbol, (Decimal("0"), Decimal("0"), Decimal("0"))
        )
        last_price = _d(row.fill_price)
        if row.side == "buy":
            quantity += qty
            cost_basis += gross + fee
            positions[row.symbol] = (quantity, cost_basis, last_price)
            if row.recorded_at.astimezone(timezone.utc).date() == observed_at.astimezone(timezone.utc).date():
                daily_deployed += gross + fee
            continue
        if qty > quantity or quantity <= 0:
            raise RiskAccountingUnavailableError(
                "position_evidence_inconsistent", details={"accounting_record_id": str(row.id)}
            )
        average_cost = cost_basis / quantity
        realized = gross - fee - (average_cost * qty)
        if row.recorded_at.astimezone(timezone.utc).date() == observed_at.astimezone(timezone.utc).date() and realized < 0:
            daily_realized_loss += -realized
        quantity -= qty
        cost_basis -= average_cost * qty
        positions[row.symbol] = (quantity, cost_basis, last_price)

    if any(quantity < 0 or cost_basis < 0 for quantity, cost_basis, _price in positions.values()):
        raise RiskAccountingUnavailableError("position_evidence_inconsistent")
    open_positions = [row for row in positions.values() if row[0] > 0]
    open_exposure = sum((quantity * last_price for quantity, _cost, last_price in open_positions), Decimal("0"))
    drawdown = max(Decimal("0"), _d(campaign.starting_capital) - _d(campaign.current_equity))
    return RiskAccountingSnapshot(
        current_open_exposure_usd=open_exposure,
        daily_deployed_usd=daily_deployed,
        daily_realized_loss_usd=daily_realized_loss,
        campaign_drawdown_usd=drawdown,
        current_position_count=len(open_positions),
        as_of=observed_at,
        evidence_ids={
            "accounting_record_ids": [str(row.id) for row in accounting],
            "live_crypto_order_ids": [str(row.live_crypto_order_id) for row in scoped_orders],
            "reconciliation_event_ids": [str(row.id) for row in reconciliations],
            "capital_campaign_ids": [str(campaign.id)],
        },
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        account_id=account_id,
        provider=provider,
        environment=environment,
        product=product,
    )
