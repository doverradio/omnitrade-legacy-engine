from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


UNKNOWN = "UNKNOWN"
REPLAY_CONTEXT_SCHEMA_VERSION = "v1"

REPLAY_CONTEXT_KEYS: tuple[str, ...] = (
    "schema_version",
    "autonomous_mandate_id",
    "autonomous_mandate_version",
    "mandate_capital_campaign_row_id",
    "mandate_paper_account_id",
    "mandate_live_trading_profile_id",
    "canonical_campaign_id",
    "canonical_campaign_version",
    "canonical_paper_account_id",
    "canonical_live_trading_profile_id",
    "identity_source",
    "canonical_identity_present",
    "mandate_identity_present",
    "identity_mismatches",
    "strategy_identity",
    "strategy_version",
    "action",
    "confidence",
    "product",
    "timeframe",
    "provider",
    "environment",
    "paper_account_id",
    "live_trading_profile_id",
    "capital_campaign_id",
    "capital_campaign_version",
    "runtime_campaign_id",
    "position_lifecycle_id",
    "signal_ids",
    "risk_event_ids",
    "trade_ids",
    "candle_id",
    "candle_close_time",
    "decision_timestamp",
    "market_data_timestamp",
    "normalized_risk_verdict",
    "expected_gross_edge",
    "expected_fees",
    "expected_slippage",
    "expected_net_edge",
    "actual_execution_fee",
    "actual_execution_price",
    "actual_execution_quantity",
    "evidence_completeness",
    "unknown_fields",
)


@dataclass(frozen=True, slots=True)
class ReplayIdentityProvenance:
    autonomous_mandate_id: Any | None = None
    autonomous_mandate_version: Any | None = None
    mandate_capital_campaign_row_id: Any | None = None
    mandate_paper_account_id: Any | None = None
    mandate_live_trading_profile_id: Any | None = None
    canonical_campaign_id: Any | None = None
    canonical_campaign_version: Any | None = None
    runtime_campaign_id: Any | None = None
    canonical_paper_account_id: Any | None = None
    canonical_live_trading_profile_id: Any | None = None

_MINIMAL_REQUIRED_FIELDS: tuple[str, ...] = (
    "strategy_identity",
    "strategy_version",
    "action",
    "product",
    "timeframe",
    "decision_timestamp",
    "normalized_risk_verdict",
)


def normalize_risk_verdict(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"approved", "accept", "accepted", "allow"}:
        return "ALLOW"
    if normalized in {"resized", "resize", "allow_resized"}:
        return "ALLOW_RESIZED"
    if normalized in {"blocked", "block", "reject", "rejected", "veto"}:
        return "BLOCK"
    return UNKNOWN


def build_canonical_replay_context(*, evidence: dict[str, Any], identity: ReplayIdentityProvenance | None = None) -> dict[str, Any]:
    identity = identity or ReplayIdentityProvenance()
    canonical_present = any(
        value is not None
        for value in (
            identity.canonical_campaign_id,
            identity.canonical_campaign_version,
            identity.runtime_campaign_id,
            identity.canonical_paper_account_id,
            identity.canonical_live_trading_profile_id,
        )
    )
    mandate_present = any(
        value is not None
        for value in (
            identity.autonomous_mandate_id,
            identity.autonomous_mandate_version,
            identity.mandate_capital_campaign_row_id,
            identity.mandate_paper_account_id,
            identity.mandate_live_trading_profile_id,
        )
    )
    mismatches = _resolve_identity_mismatches(identity=identity) if canonical_present and mandate_present else []
    identity_source = _resolve_identity_source(canonical_present=canonical_present, mandate_present=mandate_present, mismatches=mismatches)

    values: dict[str, Any] = {
        "schema_version": REPLAY_CONTEXT_SCHEMA_VERSION,
        "autonomous_mandate_id": _normalize_identifier(identity.autonomous_mandate_id),
        "autonomous_mandate_version": _normalize_identifier(identity.autonomous_mandate_version),
        "mandate_capital_campaign_row_id": _normalize_identifier(identity.mandate_capital_campaign_row_id),
        "mandate_paper_account_id": _normalize_identifier(identity.mandate_paper_account_id),
        "mandate_live_trading_profile_id": _normalize_identifier(identity.mandate_live_trading_profile_id),
        "canonical_campaign_id": _normalize_identifier(identity.canonical_campaign_id),
        "canonical_campaign_version": _normalize_identifier(identity.canonical_campaign_version),
        "canonical_paper_account_id": _normalize_identifier(identity.canonical_paper_account_id),
        "canonical_live_trading_profile_id": _normalize_identifier(identity.canonical_live_trading_profile_id),
        "identity_source": identity_source,
        "canonical_identity_present": canonical_present,
        "mandate_identity_present": mandate_present,
        "identity_mismatches": mismatches,
        "strategy_identity": _normalize_string(evidence.get("strategy_identity")),
        "strategy_version": _normalize_string(evidence.get("strategy_version")),
        "action": _normalize_string(evidence.get("action"), upper=True),
        "confidence": _normalize_decimal(evidence.get("confidence")),
        "product": _normalize_string(evidence.get("product"), upper=True),
        "timeframe": _normalize_string(evidence.get("timeframe"), upper=False),
        "provider": _normalize_string(evidence.get("provider"), upper=False),
        "environment": _normalize_string(evidence.get("environment"), upper=False),
        "paper_account_id": _normalize_identifier(evidence.get("paper_account_id")),
        "live_trading_profile_id": _normalize_identifier(evidence.get("live_trading_profile_id")),
        "capital_campaign_id": _normalize_identifier(evidence.get("capital_campaign_id")),
        "capital_campaign_version": _normalize_identifier(evidence.get("capital_campaign_version")),
        "runtime_campaign_id": _normalize_identifier(evidence.get("runtime_campaign_id")),
        "position_lifecycle_id": _normalize_identifier(evidence.get("position_lifecycle_id")),
        "signal_ids": _normalize_identifier_list(evidence.get("signal_ids")),
        "risk_event_ids": _normalize_identifier_list(evidence.get("risk_event_ids")),
        "trade_ids": _normalize_identifier_list(evidence.get("trade_ids")),
        "candle_id": _normalize_identifier(evidence.get("candle_id")),
        "candle_close_time": _normalize_datetime(evidence.get("candle_close_time")),
        "decision_timestamp": _normalize_datetime(evidence.get("decision_timestamp")),
        "market_data_timestamp": _normalize_datetime(evidence.get("market_data_timestamp")),
        "normalized_risk_verdict": normalize_risk_verdict(evidence.get("normalized_risk_verdict")),
        "expected_gross_edge": _normalize_decimal(evidence.get("expected_gross_edge")),
        "expected_fees": _normalize_decimal(evidence.get("expected_fees")),
        "expected_slippage": _normalize_decimal(evidence.get("expected_slippage")),
        "expected_net_edge": _normalize_decimal(evidence.get("expected_net_edge")),
        "actual_execution_fee": _normalize_decimal(evidence.get("actual_execution_fee")),
        "actual_execution_price": _normalize_decimal(evidence.get("actual_execution_price")),
        "actual_execution_quantity": _normalize_decimal(evidence.get("actual_execution_quantity")),
    }
    if canonical_present:
        values["paper_account_id"] = values["canonical_paper_account_id"]
        values["live_trading_profile_id"] = values["canonical_live_trading_profile_id"]
        values["capital_campaign_id"] = values["canonical_campaign_id"]
        values["capital_campaign_version"] = values["canonical_campaign_version"]
        values["runtime_campaign_id"] = _normalize_identifier(identity.runtime_campaign_id)
    else:
        values["paper_account_id"] = UNKNOWN
        values["live_trading_profile_id"] = UNKNOWN
        values["capital_campaign_id"] = UNKNOWN
        values["capital_campaign_version"] = UNKNOWN
        values["runtime_campaign_id"] = UNKNOWN

    unknown_fields = sorted(
        key
        for key, value in values.items()
        if value == UNKNOWN and key not in {"schema_version"}
    )
    values["evidence_completeness"] = _resolve_evidence_completeness(values=values, unknown_fields=unknown_fields)
    values["unknown_fields"] = unknown_fields
    return values


def _resolve_evidence_completeness(*, values: dict[str, Any], unknown_fields: list[str]) -> str:
    if not unknown_fields:
        return "COMPLETE"

    if values.get("identity_source") == UNKNOWN:
        return "PARTIAL" if any(values.get(field) != UNKNOWN for field in _MINIMAL_REQUIRED_FIELDS) else "MINIMAL"

    minimal_known = any(values.get(field) != UNKNOWN for field in _MINIMAL_REQUIRED_FIELDS)
    if minimal_known:
        return "PARTIAL"
    return "MINIMAL"


def _normalize_string(value: Any, *, upper: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return UNKNOWN
    return raw.upper() if upper else raw


def _normalize_identifier(value: Any) -> str:
    if value is None:
        return UNKNOWN
    raw = str(value).strip()
    return raw if raw else UNKNOWN


def _normalize_identifier_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        raw = str(item or "").strip()
        if raw:
            normalized.append(raw)
    return sorted(set(normalized))


def _normalize_decimal(value: Any) -> str:
    if value is None:
        return UNKNOWN
    try:
        return format(Decimal(str(value)), "f")
    except Exception:
        return UNKNOWN


def _normalize_datetime(value: Any) -> str:
    if value is None:
        return UNKNOWN
    if isinstance(value, datetime):
        observed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return observed.astimezone(timezone.utc).isoformat()
    raw = str(value).strip()
    if not raw:
        return UNKNOWN
    return raw


def _resolve_identity_source(*, canonical_present: bool, mandate_present: bool, mismatches: list[str]) -> str:
    if canonical_present and mandate_present:
        return "BOTH_VERIFIED_MATCH" if not mismatches else UNKNOWN
    if canonical_present:
        return "CANONICAL_CAMPAIGN"
    if mandate_present:
        return "AUTONOMOUS_MANDATE"
    return UNKNOWN


def _resolve_identity_mismatches(*, identity: ReplayIdentityProvenance) -> list[str]:
    mismatches: list[str] = []
    comparisons = (
        ("canonical_paper_account_id", identity.canonical_paper_account_id, "mandate_paper_account_id", identity.mandate_paper_account_id),
        ("canonical_live_trading_profile_id", identity.canonical_live_trading_profile_id, "mandate_live_trading_profile_id", identity.mandate_live_trading_profile_id),
    )
    for canonical_field, canonical_value, mandate_field, mandate_value in comparisons:
        if canonical_value is None or mandate_value is None:
            continue
        if str(canonical_value).strip() != str(mandate_value).strip():
            mismatches.append(f"{canonical_field}!={mandate_field}")
    return sorted(mismatches)