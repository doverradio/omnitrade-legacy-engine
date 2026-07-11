from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.audit_log import AuditLog


@dataclass(frozen=True, slots=True)
class EnsureCoinbaseAssetRequest:
    symbol: str
    base_currency: str
    actor: str


@dataclass(frozen=True, slots=True)
class EnsureCoinbaseAssetResult:
    asset: Asset
    created: bool


async def ensure_coinbase_crypto_asset(*, db: AsyncSession, request: EnsureCoinbaseAssetRequest) -> EnsureCoinbaseAssetResult:
    normalized_symbol = request.symbol.strip().upper()
    existing = await db.scalar(
        select(Asset)
        .where(Asset.symbol == normalized_symbol)
        .where(Asset.asset_class == "crypto")
        .where(Asset.exchange == "coinbase_advanced")
        .order_by(Asset.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return EnsureCoinbaseAssetResult(asset=existing, created=False)

    asset = Asset(
        symbol=normalized_symbol,
        asset_class="crypto",
        exchange="coinbase_advanced",
        base_currency=request.base_currency.strip().upper(),
        supports_fractional=True,
        min_order_notional=Decimal("5"),
        qty_step_size=None,
        is_active=True,
    )
    db.add(asset)
    if hasattr(db, "flush"):
        await db.flush()

    db.add(
        AuditLog(
            actor=request.actor,
            action="asset_created",
            entity_type="asset",
            entity_id=asset.id,
            before_state=None,
            after_state={
                "symbol": asset.symbol,
                "asset_class": asset.asset_class,
                "exchange": asset.exchange,
                "base_currency": asset.base_currency,
                "supports_fractional": asset.supports_fractional,
                "min_order_notional": format(Decimal("5"), "f"),
            },
        )
    )

    await db.commit()
    if hasattr(db, "refresh"):
        await db.refresh(asset)
    return EnsureCoinbaseAssetResult(asset=asset, created=True)
