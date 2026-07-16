from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.trade import Trade


_LEGACY_ARCHIVED_CAMPAIGN_ID = "f1e8a655-70ee-47f3-8e9e-89c8735b6542"
_RECONSTRUCTION_TOLERANCE = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class CanonicalPaperCashCausalityAuditRequest:
    campaign_id: UUID
    campaign_version: int
    runtime_campaign_id: int
    paper_account_id: UUID
    live_trading_profile_id: UUID
    provider: str
    environment: str
    product: str


def _normalize_environment(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"production", "sandbox"}:
        raise ValueError(f"unsupported exchange environment: {value}")
    return normalized


def _decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _product_symbol(value: str) -> str:
    return value.strip().upper().replace("/", "-").split("-", 1)[0]


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_linkage_candidate(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            return text_value
    return None


def _extract_execution_event_linkage(*, event_payload: dict[str, Any], provenance: dict[str, Any]) -> dict[str, str | None]:
    return {
        "product": _extract_linkage_candidate(event_payload, "product_id", "product")
        or _extract_linkage_candidate(provenance, "product_id", "product"),
        "campaign_uuid": _extract_linkage_candidate(
            event_payload,
            "campaign_uuid",
            "campaign_id",
            "capital_campaign_uuid",
        )
        or _extract_linkage_candidate(provenance, "campaign_uuid", "campaign_id", "capital_campaign_uuid"),
        "runtime_campaign_id": _extract_linkage_candidate(event_payload, "capital_campaign_id", "runtime_campaign_id")
        or _extract_linkage_candidate(provenance, "capital_campaign_id", "runtime_campaign_id"),
        "live_crypto_order_id": _extract_linkage_candidate(
            event_payload,
            "live_crypto_order_id",
            "order_id",
            "order_uuid",
        )
        or _extract_linkage_candidate(provenance, "live_crypto_order_id", "order_id", "order_uuid"),
        "provider_order_id": _extract_linkage_candidate(event_payload, "provider_order_id")
        or _extract_linkage_candidate(provenance, "provider_order_id"),
    }


async def _latest_close_by_asset(*, db: AsyncSession, asset_id: UUID) -> Decimal | None:
    close = await db.scalar(
        select(Candle.close)
        .where(Candle.asset_id == asset_id)
        .order_by(desc(Candle.open_time), desc(Candle.id))
        .limit(1)
    )
    return None if close is None else Decimal(str(close))


async def run_canonical_paper_cash_causality_audit(
    *,
    db: AsyncSession,
    request: CanonicalPaperCashCausalityAuditRequest,
) -> dict[str, Any]:
    provider = request.provider.strip().lower()
    environment = _normalize_environment(request.environment)
    product = request.product.strip().upper().replace("/", "-")
    product_symbol = _product_symbol(product)

    definition = await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == request.campaign_id)
        .where(CapitalCampaignDefinition.version == request.campaign_version)
        .limit(1)
    )
    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.id == request.runtime_campaign_id).limit(1))
    if runtime is None:
        raise LookupError(f"runtime campaign not found: {request.runtime_campaign_id}")

    paper_account = await db.get(PaperAccount, request.paper_account_id)
    if paper_account is None:
        raise LookupError(f"paper account not found: {request.paper_account_id}")

    live_profile = await db.get(LiveTradingProfile, request.live_trading_profile_id)
    if live_profile is None:
        raise LookupError(f"live trading profile not found: {request.live_trading_profile_id}")

    trades = list(
        (
            await db.execute(
                select(Trade)
                .where(Trade.paper_account_id == request.paper_account_id)
                .order_by(Trade.executed_at.asc(), Trade.id.asc())
            )
        )
        .scalars()
        .all()
    )

    asset_ids = sorted({item.asset_id for item in trades}, key=str)
    asset_rows = list((await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars().all()) if asset_ids else []
    assets_by_id = {item.id: item for item in asset_rows}

    position_qty: dict[UUID, Decimal] = {}
    position_cost: dict[UUID, Decimal] = {}
    realized_gains = Decimal("0")
    buy_notional_total = Decimal("0")
    sell_notional_total = Decimal("0")
    trade_fees_total = Decimal("0")
    cash_cursor = Decimal(str(paper_account.starting_balance))
    trade_timeline: list[dict[str, Any]] = []
    missing_evidence: list[str] = []

    for trade in trades:
        quantity = Decimal(str(trade.quantity))
        price = Decimal(str(trade.price))
        fee = Decimal(str(trade.fee or 0))
        notional = quantity * price
        delta = Decimal("0")

        current_qty = position_qty.get(trade.asset_id, Decimal("0"))
        current_cost = position_cost.get(trade.asset_id, Decimal("0"))

        if trade.side == "buy":
            buy_notional_total += notional
            trade_fees_total += fee
            delta = -(notional + fee)
            new_qty = current_qty + quantity
            new_cost = current_cost + notional + fee
            position_qty[trade.asset_id] = new_qty
            position_cost[trade.asset_id] = new_cost
        elif trade.side == "sell":
            sell_notional_total += notional
            trade_fees_total += fee
            delta = notional - fee
            sell_qty = quantity if quantity <= current_qty else current_qty
            avg_cost = (current_cost / current_qty) if current_qty > 0 else Decimal("0")
            remaining_qty = current_qty - sell_qty
            remaining_cost = (avg_cost * remaining_qty) if remaining_qty > 0 else Decimal("0")
            sold_cost = avg_cost * sell_qty if sell_qty > 0 else Decimal("0")
            realized_gains += (notional - fee) - sold_cost
            position_qty[trade.asset_id] = max(Decimal("0"), remaining_qty)
            position_cost[trade.asset_id] = max(Decimal("0"), remaining_cost)
            if quantity > current_qty:
                missing_evidence.append("unsupported_historical_mutation:sell_exceeds_known_position")
        else:
            missing_evidence.append(f"unsupported_historical_mutation:unknown_trade_side:{trade.side}")
            delta = Decimal("0")

        cash_cursor += delta
        asset = assets_by_id.get(trade.asset_id)
        trade_timeline.append(
            {
                "event_type": "paper_trade",
                "trade_id": str(trade.id),
                "executed_at": _iso(trade.executed_at),
                "asset_id": str(trade.asset_id),
                "symbol": None if asset is None else asset.symbol,
                "side": trade.side,
                "quantity": _decimal(quantity),
                "price": _decimal(price),
                "notional": _decimal(notional),
                "fee": _decimal(fee),
                "cash_delta": _decimal(delta),
                "cumulative_cash_after": _decimal(cash_cursor),
                "signal_id": None if trade.signal_id is None else str(trade.signal_id),
            }
        )

    open_positions: list[dict[str, Any]] = []
    total_position_market_value = Decimal("0")

    for asset_id in sorted(position_qty.keys(), key=str):
        quantity = position_qty.get(asset_id, Decimal("0"))
        if quantity <= Decimal("0"):
            continue
        asset = assets_by_id.get(asset_id)
        latest_close = await _latest_close_by_asset(db=db, asset_id=asset_id)
        market_value = (latest_close or Decimal("0")) * quantity
        total_position_market_value += market_value
        avg_entry_price = Decimal("0")
        total_cost = position_cost.get(asset_id, Decimal("0"))
        if quantity > 0:
            avg_entry_price = total_cost / quantity
        open_positions.append(
            {
                "asset_id": str(asset_id),
                "symbol": None if asset is None else asset.symbol,
                "quantity": _decimal(quantity),
                "avg_entry_price": _decimal(avg_entry_price),
                "cost_basis": _decimal(total_cost),
                "latest_price": _decimal(latest_close),
                "market_value": _decimal(market_value),
            }
        )

    live_orders = list(
        (
            await db.execute(
                select(LiveCryptoOrder)
                .where(LiveCryptoOrder.environment == environment)
                .where(LiveCryptoOrder.provider == provider)
                .where(LiveCryptoOrder.product_id == product)
                .where(
                    LiveCryptoOrder.status.in_(
                        (
                            "PENDING",
                            "SUBMITTED",
                            "ACKNOWLEDGED",
                            "OPEN",
                            "QUEUED",
                            "CANCEL_QUEUED",
                            "EDIT_QUEUED",
                            "RECONCILIATION_REQUIRED",
                        )
                    )
                )
                .order_by(desc(LiveCryptoOrder.created_at))
            )
        )
        .scalars()
        .all()
    )

    pending_orders = [
        {
            "live_crypto_order_id": str(item.live_crypto_order_id),
            "status": item.status,
            "product_id": item.product_id,
            "side": item.side,
            "requested_quote_size": _decimal(item.requested_quote_size),
            "created_at": _iso(item.created_at),
            "decision_record_id": None if item.decision_record_id is None else str(item.decision_record_id),
            "risk_event_id": None if item.risk_event_id is None else str(item.risk_event_id),
        }
        for item in live_orders
        if item.status in {
            "PENDING",
            "SUBMITTED",
            "ACKNOWLEDGED",
            "OPEN",
            "QUEUED",
            "CANCEL_QUEUED",
            "EDIT_QUEUED",
        }
    ]

    unresolved_reconciliation = list(
        (
            await db.execute(
                select(LiveReconciliationEvent)
                .where(LiveReconciliationEvent.live_trading_profile_id == request.live_trading_profile_id)
                .where(
                    LiveReconciliationEvent.reconciliation_status.in_(
                        (
                            "reconciliation_required",
                            "unknown",
                            "conflict",
                            "balance_mismatch",
                        )
                    )
                )
                .order_by(desc(LiveReconciliationEvent.recorded_at), desc(LiveReconciliationEvent.id))
            )
        )
        .scalars()
        .all()
    )

    accounting_records = list(
        (
            await db.execute(
                select(LiveAccountingRecord)
                .where(LiveAccountingRecord.live_trading_profile_id == request.live_trading_profile_id)
                .order_by(desc(LiveAccountingRecord.recorded_at), desc(LiveAccountingRecord.id))
            )
        )
        .scalars()
        .all()
    )

    net_live_cash_impact = Decimal("0")
    for record in accounting_records:
        net_live_cash_impact += Decimal(str(record.net_cash_impact))

    execution_events_raw = list(
        (
            await db.execute(
                select(LiveExecutionEvent)
                .where(LiveExecutionEvent.live_trading_profile_id == request.live_trading_profile_id)
                .where(LiveExecutionEvent.provider_name == provider)
                .order_by(desc(LiveExecutionEvent.recorded_at), desc(LiveExecutionEvent.id))
            )
        )
        .scalars()
        .all()
    )

    related_campaigns = list(
        (
            await db.execute(
                select(CapitalCampaign)
                .where(CapitalCampaign.paper_account_id == request.paper_account_id)
                .order_by(CapitalCampaign.created_at.asc(), CapitalCampaign.id.asc())
            )
        )
        .scalars()
        .all()
    )

    campaign_uuids = [item.uuid for item in related_campaigns]
    campaign_cycles = list(
        (
            await db.execute(
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.cycle_kind == "campaign")
                .where(AutonomousCycleRun.capital_campaign_id.in_(campaign_uuids) if campaign_uuids else False)
                .where(AutonomousCycleRun.decision_record_id.is_not(None))
                .order_by(AutonomousCycleRun.started_at.asc(), AutonomousCycleRun.cycle_id.asc())
            )
        )
        .scalars()
        .all()
    ) if campaign_uuids else []

    autonomous_cycles = list(
        (
            await db.execute(
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.cycle_kind == "autonomous")
                .where(AutonomousCycleRun.decision_record_id.is_not(None))
                .order_by(AutonomousCycleRun.started_at.asc(), AutonomousCycleRun.cycle_id.asc())
            )
        )
        .scalars()
        .all()
    )

    decision_ids = sorted(
        {
            *(item.decision_record_id for item in campaign_cycles if item.decision_record_id is not None),
            *(item.decision_record_id for item in autonomous_cycles if item.decision_record_id is not None),
        },
        key=str,
    )
    decision_rows = list((await db.execute(select(DecisionRecord).where(DecisionRecord.decision_id.in_(decision_ids)))).scalars().all()) if decision_ids else []
    decisions_by_id = {item.decision_id: item for item in decision_rows}

    signal_to_owner: dict[str, dict[str, Any]] = {}
    for cycle in campaign_cycles:
        decision = decisions_by_id.get(cycle.decision_record_id)
        lineage = _safe_dict(decision.source_lineage if decision is not None else None)
        for signal in _safe_list(lineage.get("signals")):
            signal_to_owner[str(signal)] = {
                "owner_type": "campaign",
                "campaign_id": None if cycle.capital_campaign_id is None else str(cycle.capital_campaign_id),
                "cycle_id": str(cycle.cycle_id),
            }

    for cycle in autonomous_cycles:
        decision = decisions_by_id.get(cycle.decision_record_id)
        lineage = _safe_dict(decision.source_lineage if decision is not None else None)
        for signal in _safe_list(lineage.get("signals")):
            signal_to_owner.setdefault(
                str(signal),
                {
                    "owner_type": "autonomous_cycle",
                    "campaign_id": None,
                    "cycle_id": str(cycle.cycle_id),
                },
            )

    ownership_summary = {
        "canonical_campaign_trade_count": 0,
        "archived_legacy_trade_count": 0,
        "unrelated_campaign_trade_count": 0,
        "other_campaign_trade_count": 0,
        "autonomous_cycle_trade_count": 0,
        "manual_test_trade_count": 0,
        "unattributed_trade_count": 0,
        "canonical_campaign_trade_notional": "0",
        "other_campaign_trade_notional": "0",
    }

    canonical_trade_notional = Decimal("0")
    other_trade_notional = Decimal("0")
    ownership_details = {
        "canonical_campaign_usage": [],
        "archived_legacy_campaign_usage": [],
        "unrelated_paper_campaign_usage": [],
        "autonomous_cycle_usage": [],
        "manual_test_activity": [],
        "unknown_ownership": [],
    }
    for event in trade_timeline:
        signal_id = event.get("signal_id")
        owner = signal_to_owner.get(str(signal_id)) if signal_id else None
        notional = Decimal(str(event.get("notional") or "0"))
        detail = {
            "trade_id": event.get("trade_id"),
            "signal_id": signal_id,
            "notional": _decimal(notional),
            "executed_at": event.get("executed_at"),
            "campaign_id": None if owner is None else owner.get("campaign_id"),
            "cycle_id": None if owner is None else owner.get("cycle_id"),
        }
        if owner is None:
            if signal_id is None:
                ownership_summary["manual_test_trade_count"] += 1
                ownership_details["manual_test_activity"].append(detail)
            else:
                ownership_summary["unattributed_trade_count"] += 1
                ownership_details["unknown_ownership"].append(detail)
            continue
        if owner.get("owner_type") == "autonomous_cycle":
            ownership_summary["autonomous_cycle_trade_count"] += 1
            ownership_details["autonomous_cycle_usage"].append(detail)
            continue
        owner_campaign = owner.get("campaign_id")
        if owner_campaign == str(request.campaign_id):
            ownership_summary["canonical_campaign_trade_count"] += 1
            canonical_trade_notional += notional
            ownership_details["canonical_campaign_usage"].append(detail)
        elif owner_campaign == _LEGACY_ARCHIVED_CAMPAIGN_ID:
            ownership_summary["archived_legacy_trade_count"] += 1
            other_trade_notional += notional
            ownership_details["archived_legacy_campaign_usage"].append(detail)
        else:
            ownership_summary["unrelated_campaign_trade_count"] += 1
            ownership_summary["other_campaign_trade_count"] += 1
            other_trade_notional += notional
            ownership_details["unrelated_paper_campaign_usage"].append(detail)

    ownership_summary["canonical_campaign_trade_notional"] = _decimal(canonical_trade_notional)
    ownership_summary["other_campaign_trade_notional"] = _decimal(other_trade_notional)

    pending_order_reserved = sum((Decimal(str(item.get("requested_quote_size") or "0")) for item in pending_orders), Decimal("0"))
    open_position_reserved = sum((Decimal(str(item.get("cost_basis") or "0")) for item in open_positions), Decimal("0"))
    campaign_reserved = Decimal("0") if definition is None else Decimal(str(getattr(definition, "reserved_capital", 0) or 0))

    unquantified_reservation = False
    if any(item.get("requested_quote_size") is None for item in pending_orders):
        unquantified_reservation = True
        missing_evidence.append("reserved_capital_unquantified:pending_order_missing_quote_size")
    if any(item.reconciliation_status in {"unknown", "conflict", "balance_mismatch"} for item in unresolved_reconciliation):
        unquantified_reservation = True
        missing_evidence.append("reserved_capital_unquantified:unresolved_reconciliation")

    reserved_total = pending_order_reserved + open_position_reserved + campaign_reserved
    reserved_capital = {
        "total_reserved_amount": _decimal(reserved_total),
        "pending_order_reservations": _decimal(pending_order_reserved),
        "open_position_cost_basis_reserved": _decimal(open_position_reserved),
        "campaign_allocation_reservations": _decimal(campaign_reserved),
        "source_records": {
            "pending_order_ids": [item.get("live_crypto_order_id") for item in pending_orders],
            "open_position_asset_ids": [item.get("asset_id") for item in open_positions],
            "campaign_definition_id": None if definition is None else str(definition.campaign_id),
            "campaign_definition_version": None if definition is None else definition.version,
        },
        "unknown_unquantified_reservation": unquantified_reservation,
        "fully_proven": not unquantified_reservation,
    }

    linked_live_order_ids = {str(item.live_crypto_order_id) for item in live_orders}
    linked_provider_order_ids = {
        str(item.provider_order_id)
        for item in live_orders
        if getattr(item, "provider_order_id", None) not in {None, ""}
    }
    linked_provider_order_ids.update(
        str(item.provider_order_id)
        for item in unresolved_reconciliation
        if getattr(item, "provider_order_id", None) not in {None, ""}
    )
    linked_provider_order_ids.update(
        str(item.provider_order_id)
        for item in accounting_records
        if getattr(item, "provider_order_id", None) not in {None, ""}
    )

    execution_events: list[dict[str, Any]] = []
    for event in execution_events_raw:
        payload = _safe_dict(event.event_payload)
        provenance = _safe_dict(event.provenance)
        linkage = _extract_execution_event_linkage(event_payload=payload, provenance=provenance)
        linkage_sources: list[str] = []

        linked = False
        linked_order = linkage.get("live_crypto_order_id")
        linked_provider_order = linkage.get("provider_order_id")
        linked_campaign = linkage.get("campaign_uuid")
        linked_runtime_campaign = linkage.get("runtime_campaign_id")
        linked_product = linkage.get("product")

        if linked_order and linked_order in linked_live_order_ids:
            linked = True
            linkage_sources.append("live_crypto_order_id")
        if linked_provider_order and linked_provider_order in linked_provider_order_ids:
            linked = True
            linkage_sources.append("provider_order_id")
        if linked_campaign and linked_campaign in {str(request.campaign_id), _LEGACY_ARCHIVED_CAMPAIGN_ID}:
            linked = True
            linkage_sources.append("campaign_uuid")
        if linked_runtime_campaign and linked_runtime_campaign == str(request.runtime_campaign_id):
            linked = True
            linkage_sources.append("runtime_campaign_id")
        if linked_product and linked_product.strip().upper().replace("/", "-") == product:
            linked = True
            linkage_sources.append("product")

        if not linked:
            continue

        execution_events.append(
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "provider": event.provider_name,
                "environment": environment,
                "product": linked_product,
                "live_crypto_order_id": linked_order,
                "provider_order_id": linked_provider_order,
                "campaign_uuid": linked_campaign,
                "runtime_campaign_id": linked_runtime_campaign,
                "quantity": _decimal(payload.get("filled_quantity") or payload.get("quantity") or payload.get("size")),
                "amount": _decimal(payload.get("gross_notional") or payload.get("quote_size") or payload.get("amount")),
                "fee": _decimal(payload.get("fee_amount") or payload.get("fee")),
                "timestamp": _iso(event.recorded_at),
                "linkage_source": sorted(set(linkage_sources)),
            }
        )

    unknown_provider_orders = [
        {
            "live_crypto_order_id": str(item.live_crypto_order_id),
            "status": item.status,
            "provider_order_id": getattr(item, "provider_order_id", None),
            "reason": "missing_provider_order_id_or_reconciliation_required",
        }
        for item in live_orders
        if getattr(item, "provider_order_id", None) in {None, ""} or item.status in {"RECONCILIATION_REQUIRED"}
    ]
    unknown_provider_orders.extend(
        {
            "live_crypto_order_id": None,
            "status": item.reconciliation_status,
            "provider_order_id": item.provider_order_id,
            "reason": "unresolved_reconciliation",
        }
        for item in unresolved_reconciliation
        if item.reconciliation_status in {"unknown", "conflict", "balance_mismatch"}
    )

    known_provider_orders = [
        {
            "live_crypto_order_id": str(item.live_crypto_order_id),
            "provider_order_id": item.provider_order_id,
            "status": item.status,
        }
        for item in live_orders
        if item.provider_order_id not in {None, ""}
    ]

    latest_reconciliation_observation = unresolved_reconciliation[0] if unresolved_reconciliation else None
    unknown_provider_state = len(unknown_provider_orders) > 0
    if unknown_provider_state:
        provider_state_class = "unresolved"
        provider_reason_code = "unknown_provider_order_state"
    elif live_orders or accounting_records or execution_events:
        provider_state_class = "authoritative"
        provider_reason_code = "provider_state_derived_from_persisted_live_records"
    else:
        provider_state_class = "stale"
        provider_reason_code = "no_provider_observations_for_interval"

    provider_state = {
        "known_provider_orders": known_provider_orders,
        "unknown_provider_orders": unknown_provider_orders,
        "provider_balance_observation": None,
        "observation_timestamp": _iso(None if latest_reconciliation_observation is None else latest_reconciliation_observation.recorded_at),
        "reconciliation_status": "unresolved" if unresolved_reconciliation else "none",
        "state": provider_state_class,
        "unknown_provider_state": unknown_provider_state,
        "reason_code": provider_reason_code,
    }

    credits = Decimal("0")
    transfer_in = Decimal("0")
    transfer_out = Decimal("0")
    withdrawals = Decimal("0")
    reconstructed_cash_after_trades = cash_cursor
    reconstructed_available_cash = reconstructed_cash_after_trades - reserved_total

    reconstructed_cash = reconstructed_cash_after_trades
    persisted_cash = Decimal(str(paper_account.current_cash_balance))
    difference = Decimal(str(paper_account.starting_balance)) - persisted_cash
    reconstruction_delta = persisted_cash - reconstructed_available_cash

    if definition is None:
        missing_evidence.append("missing_campaign_definition")
    if not trades and abs(difference) > _RECONSTRUCTION_TOLERANCE:
        missing_evidence.append("missing_ledger_history:nonzero_cash_delta_without_trade_events")
    if ownership_summary["unattributed_trade_count"] > 0:
        missing_evidence.append("incomplete_ownership_linkage")

    supports_five = persisted_cash >= Decimal("5")
    risk_sizing_authority = {
        "authoritative_source": "paper_account_liquid_cash_hard_cap",
        "supporting_caps": {
            "campaign_remaining_unallocated_capital": None if definition is None else _decimal(definition.remaining_unallocated_capital),
            "runtime_current_equity": _decimal(runtime.current_equity),
        },
        "liquid_cash": _decimal(persisted_cash),
        "minimum_order_notional": "5",
        "can_support_exact_5": supports_five,
    }

    open_position_count = len(open_positions)
    pending_order_count = len(pending_orders)
    unresolved_reconciliation_count = len(unresolved_reconciliation)

    ownership_ambiguity = (
        ownership_summary["archived_legacy_trade_count"] > 0
        or ownership_summary["unrelated_campaign_trade_count"] > 0
        or ownership_summary["autonomous_cycle_trade_count"] > 0
        or ownership_summary["manual_test_trade_count"] > 0
        or ownership_summary["unattributed_trade_count"] > 0
    )
    reconstruction_complete = len(missing_evidence) == 0 and reserved_capital["fully_proven"]

    # Deterministic precedence: UNPROVEN > RESERVED_OR_OPEN_EXPOSURE > ACCOUNTING_IS_STALE > WRONG_ACCOUNT_BOUND > BALANCE_IS_CORRECT.
    if unknown_provider_state or not reconstruction_complete:
        outcome = "UNPROVEN"
        outcome_detail = "critical evidence is incomplete or provider state is unresolved"
    elif open_position_count > 0 or pending_order_count > 0 or unresolved_reconciliation_count > 0:
        outcome = "RESERVED_OR_OPEN_EXPOSURE"
        outcome_detail = "capital tied to open exposure, pending order state, or unresolved reconciliation"
    elif abs(reconstruction_delta) > _RECONSTRUCTION_TOLERANCE:
        outcome = "ACCOUNTING_IS_STALE"
        outcome_detail = "persisted paper cash differs from reconstructed available cash"
    elif ownership_ambiguity and ownership_summary["canonical_campaign_trade_count"] == 0:
        outcome = "WRONG_ACCOUNT_BOUND"
        outcome_detail = "non-canonical ownership history explains current account usage ambiguity"
    else:
        outcome = "BALANCE_IS_CORRECT"
        outcome_detail = "complete reconstruction and ownership evidence match persisted cash"

    return {
        "command": "canonical-paper-cash-causality-audit",
        "inputs": {
            "campaign_id": str(request.campaign_id),
            "campaign_version": request.campaign_version,
            "runtime_campaign_id": request.runtime_campaign_id,
            "paper_account_id": str(request.paper_account_id),
            "live_trading_profile_id": str(request.live_trading_profile_id),
            "provider": provider,
            "environment": environment,
            "product": product,
        },
        "runtime_campaign": {
            "id": runtime.id,
            "uuid": str(runtime.uuid),
            "status": runtime.status,
            "paper_account_id": None if runtime.paper_account_id is None else str(runtime.paper_account_id),
            "starting_capital": _decimal(runtime.starting_capital),
            "current_equity": _decimal(runtime.current_equity),
            "realized_profit": _decimal(runtime.realized_profit),
            "fees": _decimal(runtime.fees),
            "definition_campaign_id": None if runtime.definition_campaign_id is None else str(runtime.definition_campaign_id),
            "definition_version": runtime.definition_version,
        },
        "campaign_definition": None
        if definition is None
        else {
            "campaign_id": str(definition.campaign_id),
            "version": definition.version,
            "capital_budget": _decimal(definition.capital_budget),
            "remaining_unallocated_capital": _decimal(definition.remaining_unallocated_capital),
            "minimum_position_size": _decimal(definition.minimum_position_size),
            "maximum_position_size": _decimal(definition.maximum_position_size),
            "maximum_total_exposure": _decimal(definition.maximum_total_exposure),
            "allowed_instruments": list(definition.allowed_instruments or []),
        },
        "paper_account": {
            "paper_account_id": str(paper_account.id),
            "starting_balance": _decimal(paper_account.starting_balance),
            "current_cash_balance": _decimal(paper_account.current_cash_balance),
            "difference_from_starting_balance": _decimal(difference),
            "asset_class": paper_account.asset_class,
            "is_active": bool(paper_account.is_active),
        },
        "cash_reconstruction": {
            "starting_balance": _decimal(paper_account.starting_balance),
            "credits": _decimal(credits),
            "sell_proceeds": _decimal(sell_notional_total),
            "realized_gains": _decimal(realized_gains),
            "transfers_in": _decimal(transfer_in),
            "buys": _decimal(buy_notional_total),
            "fees": _decimal(trade_fees_total),
            "withdrawals_or_transfers_out": _decimal(withdrawals + transfer_out),
            "reserved_capital": _decimal(reserved_total),
            "reconstructed_available_cash": _decimal(reconstructed_available_cash),
            "trade_event_count": len(trade_timeline),
            "events": trade_timeline,
            "reconstructed_cash_after_trades": _decimal(reconstructed_cash),
            "persisted_current_cash_balance": _decimal(persisted_cash),
            "reconstruction_delta": _decimal(reconstruction_delta),
            "tolerance_used": _decimal(_RECONSTRUCTION_TOLERANCE),
            "reconstruction_completeness": reconstruction_complete,
            "missing_evidence": sorted(set(missing_evidence)),
            "equation": "starting+credits+sell_proceeds+realized_gains+transfers_in-buys-fees-withdrawals_or_transfers_out-reserved_capital=reconstructed_available_cash",
        },
        "exposure": {
            "open_position_count": open_position_count,
            "open_positions": open_positions,
            "total_open_position_market_value": _decimal(total_position_market_value),
            "pending_order_count": pending_order_count,
            "pending_orders": pending_orders,
            "unresolved_reconciliation_count": unresolved_reconciliation_count,
            "unresolved_reconciliation_events": [
                {
                    "reconciliation_event_id": str(item.id),
                    "reconciliation_status": item.reconciliation_status,
                    "provider_order_id": item.provider_order_id,
                    "recorded_at": _iso(item.recorded_at),
                }
                for item in unresolved_reconciliation
            ],
        },
        "execution_events": {
            "count": len(execution_events),
            "completeness": "complete" if execution_events else "no_exact_linked_events",
            "events": execution_events,
        },
        "reserved_capital": reserved_capital,
        "provider_state": provider_state,
        "ownership": {
            "related_campaigns": [
                {
                    "runtime_campaign_id": item.id,
                    "campaign_uuid": str(item.uuid),
                    "status": item.status,
                    "created_at": _iso(item.created_at),
                }
                for item in related_campaigns
            ],
            "summary": ownership_summary,
            "classifications": ownership_details,
        },
        "live_accounting": {
            "record_count": len(accounting_records),
            "net_cash_impact": _decimal(net_live_cash_impact),
            "latest_records": [
                {
                    "accounting_record_id": str(item.id),
                    "record_type": item.record_type,
                    "provider_order_id": item.provider_order_id,
                    "symbol": item.symbol,
                    "side": item.side,
                    "gross_notional": _decimal(item.gross_notional),
                    "fee_amount": _decimal(item.fee_amount),
                    "net_cash_impact": _decimal(item.net_cash_impact),
                    "recorded_at": _iso(item.recorded_at),
                }
                for item in accounting_records[:50]
            ],
        },
        "risk_sizing_authority": risk_sizing_authority,
        "outcome": {
            "code": outcome,
            "detail": outcome_detail,
            "precedence": [
                "UNPROVEN",
                "RESERVED_OR_OPEN_EXPOSURE",
                "ACCOUNTING_IS_STALE",
                "WRONG_ACCOUNT_BOUND",
                "BALANCE_IS_CORRECT",
            ],
        },
    }
