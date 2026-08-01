"""Pure, non-authoritative BTC/Kraken stage-contract vocabulary.

These v1 models describe data or references at verified commissioned-pipeline
seams. Constructing one performs no work and grants no Risk, Governance,
custody, activation, submission, reconciliation, or accounting authority.
They are not compatibility adapters and are not connected to production.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from app.services.pipeline_contracts.envelope import CanonicalEnvelopeV1
from app.services.pipeline_contracts.identifiers import (
    AccountingId,
    AssetIdentity,
    CampaignId,
    ExecutionClaimId,
    InstrumentIdentity,
    LineageAuthority,
    LineageKind,
    LineageReference,
    PackageId,
    ProofId,
    ProviderOrderId,
    ReconciliationId,
)
from app.services.pipeline_contracts.serialization import canonical_json_bytes, integrity_sha256


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

BTC_KRAKEN_INSTRUMENT_VERSION = "btc-kraken-instrument/v1"
CANDLE_OBSERVATION_VERSION = "btc-kraken-candle-observation/v1"
STRATEGY_EVALUATION_VERSION = "btc-kraken-strategy-evaluation/v1"
RISK_DECISION_REFERENCE_VERSION = "btc-kraken-risk-decision-reference/v1"
GOVERNANCE_AUTHORIZATION_REFERENCE_VERSION = "btc-kraken-governance-authorization-reference/v1"
EXECUTION_INTENT_VERSION = "btc-kraken-execution-intent/v1"
PROVIDER_SUBMISSION_RESULT_VERSION = "btc-kraken-provider-submission-result/v1"
PROVIDER_FILL_REFERENCE_VERSION = "btc-kraken-provider-fill-reference/v1"
RECONCILIATION_RESULT_REFERENCE_VERSION = "btc-kraken-reconciliation-result-reference/v1"
ACCOUNTING_RESULT_REFERENCE_VERSION = "btc-kraken-accounting-result-reference/v1"


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def integrity_hash(self) -> str:
        return integrity_sha256(self)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("stage-contract datetimes must be timezone-aware")
    return value


def _decimal(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("binary floating-point is not accepted; supply Decimal or an exact string")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("stage-contract Decimal values must be finite")
    return value


class BtcKrakenInstrumentV1(_Contract):
    schema_version: Literal[BTC_KRAKEN_INSTRUMENT_VERSION]
    asset_identity: AssetIdentity
    instrument_identity: InstrumentIdentity
    internal_product: Literal["BTC-USD"]
    canonical_base_asset: Literal["BTC"]
    quote_asset: Literal["USD"]
    provider: Literal["kraken_spot"]
    provider_asset_code: NonEmptyText
    provider_pair: NonEmptyText


class CandleObservationV1(_Contract):
    schema_version: Literal[CANDLE_OBSERVATION_VERSION]
    envelope: CanonicalEnvelopeV1
    instrument: BtcKrakenInstrumentV1
    interval: NonEmptyText
    open_time: datetime
    close_time: datetime | None = None
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    _validate_times = field_validator("open_time", "close_time")(_aware)
    _validate_decimals = field_validator("open", "high", "low", "close", "volume", mode="before")(_decimal)

    @model_validator(mode="after")
    def validate_candle(self) -> "CandleObservationV1":
        if self.close_time is not None and self.close_time < self.open_time:
            raise ValueError("close_time cannot precede open_time")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("OHLC bounds are contradictory")
        return self


class StrategyAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyEvaluationResultV1(_Contract):
    schema_version: Literal[STRATEGY_EVALUATION_VERSION]
    envelope: CanonicalEnvelopeV1
    instrument: BtcKrakenInstrumentV1
    action: StrategyAction
    strength: Decimal | None = None
    reason: NonEmptyText
    explanation: tuple[NonEmptyText, ...] = ()
    strategy_identity: NonEmptyText
    strategy_version: NonEmptyText
    source_signal_ref: LineageReference | None = None
    evaluated_at: datetime

    _validate_strength = field_validator("strength", mode="before")(_decimal)
    _validate_time = field_validator("evaluated_at")(_aware)

    @model_validator(mode="after")
    def validate_strength_range(self) -> "StrategyEvaluationResultV1":
        if self.strength is not None and not Decimal("0") <= self.strength <= Decimal("1"):
            raise ValueError("strength must be between zero and one")
        return self


class RiskDisposition(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESIZED = "RESIZED"


class RiskDecisionReferenceV1(_Contract):
    schema_version: Literal[RISK_DECISION_REFERENCE_VERSION]
    envelope: CanonicalEnvelopeV1
    risk_event: LineageReference
    disposition: RiskDisposition
    requested_notional: Decimal | None = None
    approved_notional: Decimal | None = None
    requested_quantity: Decimal | None = None
    approved_quantity: Decimal | None = None
    reason_code: NonEmptyText | None = None
    first_failing_rule: NonEmptyText | None = None
    recorded_at: datetime

    _validate_amounts = field_validator(
        "requested_notional", "approved_notional", "requested_quantity", "approved_quantity", mode="before"
    )(_decimal)
    _validate_time = field_validator("recorded_at")(_aware)

    @model_validator(mode="after")
    def validate_risk_evidence(self) -> "RiskDecisionReferenceV1":
        if self.risk_event.kind is not LineageKind.RISK:
            raise ValueError("risk_event must be RISK lineage")
        sizes = (self.requested_notional, self.approved_notional, self.requested_quantity, self.approved_quantity)
        if any(value is not None and value < 0 for value in sizes):
            raise ValueError("Risk sizing cannot be negative")
        if self.disposition in {RiskDisposition.APPROVED, RiskDisposition.RESIZED}:
            if self.risk_event.authority is not LineageAuthority.VERIFIED:
                raise ValueError("authoritative Risk approval requires a VERIFIED Risk event")
            if self.approved_notional is None and self.approved_quantity is None:
                raise ValueError("Risk approval requires approved sizing")
            if not any(value is not None and value > 0 for value in (self.approved_notional, self.approved_quantity)):
                raise ValueError("Risk approval requires positive approved sizing")
        if self.disposition is RiskDisposition.REJECTED:
            if self.approved_notional not in {None, Decimal("0")} or self.approved_quantity not in {None, Decimal("0")}:
                raise ValueError("Risk rejection cannot carry approved execution sizing")
        return self


class GovernanceDisposition(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"


class GovernanceAuthorizationReferenceV1(_Contract):
    schema_version: Literal[GOVERNANCE_AUTHORIZATION_REFERENCE_VERSION]
    envelope: CanonicalEnvelopeV1
    disposition: GovernanceDisposition
    campaign_id: CampaignId
    campaign_version: int
    runtime_campaign_id: int | None = None
    mandate_id: UUID
    mandate_version_id: UUID
    mandate_version_number: int
    authorization_evidence: LineageReference
    commissioning_evidence: LineageReference | None = None
    package_authorization_evidence: LineageReference | None = None
    reason_code: NonEmptyText | None = None
    recorded_at: datetime

    _validate_time = field_validator("recorded_at")(_aware)

    @model_validator(mode="after")
    def validate_governance_evidence(self) -> "GovernanceAuthorizationReferenceV1":
        if self.campaign_version < 1 or self.mandate_version_number < 1:
            raise ValueError("campaign and mandate version numbers must be positive")
        if self.disposition is GovernanceDisposition.AUTHORIZED:
            if self.authorization_evidence.kind is not LineageKind.APPROVAL:
                raise ValueError("authorization_evidence must be APPROVAL lineage")
            if self.authorization_evidence.authority is not LineageAuthority.VERIFIED:
                raise ValueError("Governance authorization requires VERIFIED evidence")
        return self


class ExecutionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"


class ExecutionIntentV1(_Contract):
    schema_version: Literal[EXECUTION_INTENT_VERSION]
    envelope: CanonicalEnvelopeV1
    instrument: BtcKrakenInstrumentV1
    side: ExecutionSide
    order_type: OrderType
    quote_notional: Decimal | None = None
    base_quantity: Decimal | None = None
    limit_price: Decimal | None = None
    package_id: PackageId
    claim_id: ExecutionClaimId
    proof_id: ProofId | None = None
    internal_client_order_id: NonEmptyText
    authorization_evidence: LineageReference
    custody_evidence: LineageReference

    _validate_amounts = field_validator("quote_notional", "base_quantity", "limit_price", mode="before")(_decimal)

    @model_validator(mode="after")
    def validate_intent_shape(self) -> "ExecutionIntentV1":
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("MARKET intent cannot contain a limit price")
        if self.side is ExecutionSide.BUY and (self.quote_notional is None or self.base_quantity is not None):
            raise ValueError("BTC/Kraken BUY intent requires quote_notional only")
        if self.side is ExecutionSide.SELL and (self.base_quantity is None or self.quote_notional is not None):
            raise ValueError("BTC/Kraken SELL intent requires base_quantity only")
        sizing = self.quote_notional if self.side is ExecutionSide.BUY else self.base_quantity
        if sizing is None or sizing <= 0:
            raise ValueError("execution intent sizing must be positive")
        if self.authorization_evidence.kind is not LineageKind.APPROVAL:
            raise ValueError("authorization_evidence must be APPROVAL lineage")
        if self.custody_evidence.kind is not LineageKind.EXECUTION_CLAIM:
            raise ValueError("custody_evidence must be EXECUTION_CLAIM lineage")
        return self

    @property
    def grants_authority(self) -> bool:
        return False


class ProviderOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PRE_SUBMISSION_FAILURE = "PRE_SUBMISSION_FAILURE"
    AMBIGUOUS = "AMBIGUOUS"


class ProviderSubmissionResultV1(_Contract):
    """Normalized provider result.

    ``safe_error_fields=None`` means no error collection was supplied, while
    ``()`` means one was supplied and was explicitly empty. Non-empty input is
    normalized to immutable key-sorted pairs for stable canonical JSON.
    """

    schema_version: Literal[PROVIDER_SUBMISSION_RESULT_VERSION]
    envelope: CanonicalEnvelopeV1
    outcome: ProviderOutcome
    provider: Literal["kraken_spot"]
    internal_client_order_id: NonEmptyText
    provider_order_id: ProviderOrderId | None = None
    provider_status: NonEmptyText | None = None
    reason_code: NonEmptyText | None = None
    safe_error_fields: tuple[tuple[NonEmptyText, NonEmptyText], ...] | None = None
    provider_timestamp: datetime | None = None
    reconciliation_required: bool
    blind_resubmission_permitted: Literal[False] = False

    _validate_time = field_validator("provider_timestamp")(_aware)

    @field_validator("safe_error_fields", mode="before")
    @classmethod
    def normalize_safe_error_fields(cls, value: object) -> object:
        if value is None:
            return None
        entries = value.items() if isinstance(value, Mapping) else value
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            for entry in entries:  # type: ignore[union-attr]
                key, item = entry
                if not isinstance(key, str) or not isinstance(item, str):
                    raise ValueError("safe error keys and values must be strings")
                key = key.strip()
                item = item.strip()
                if not key or not item:
                    raise ValueError("safe error keys and values must be non-empty")
                if key in seen:
                    raise ValueError("safe error keys must be unique")
                seen.add(key)
                normalized.append((key, item))
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("safe error"):
                raise
            raise ValueError("safe_error_fields must be a mapping or key/value pairs") from exc
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def validate_provider_result(self) -> "ProviderSubmissionResultV1":
        if self.outcome is ProviderOutcome.ACCEPTED and self.provider_order_id is None:
            raise ValueError("accepted provider result requires provider_order_id")
        if self.outcome is ProviderOutcome.AMBIGUOUS and not self.reconciliation_required:
            raise ValueError("ambiguous provider result requires reconciliation")
        if self.outcome is ProviderOutcome.PRE_SUBMISSION_FAILURE and self.provider_order_id is not None:
            raise ValueError("pre-submission failure cannot contain provider_order_id")
        return self


class ReconciliationStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"
    BALANCE_MISMATCH = "balance_mismatch"


class ProviderFillReferenceV1(_Contract):
    schema_version: Literal[PROVIDER_FILL_REFERENCE_VERSION]
    provider_fill_id: NonEmptyText
    quantity: Decimal
    price: Decimal
    fee_amount: Decimal | None = None
    fee_asset: NonEmptyText | None = None
    occurred_at: datetime | None = None

    _validate_amounts = field_validator("quantity", "price", "fee_amount", mode="before")(_decimal)
    _validate_time = field_validator("occurred_at")(_aware)


class ReconciliationResultReferenceV1(_Contract):
    schema_version: Literal[RECONCILIATION_RESULT_REFERENCE_VERSION]
    envelope: CanonicalEnvelopeV1
    live_order_id: UUID
    provider_order_id: ProviderOrderId | None = None
    reconciliation_id: ReconciliationId
    status: ReconciliationStatus
    filled_quantity: Decimal
    remaining_quantity: Decimal | None = None
    fills: tuple[ProviderFillReferenceV1, ...] = ()
    provider_truth_at: datetime | None = None
    idempotency_key: NonEmptyText
    evidence: LineageReference

    _validate_amounts = field_validator("filled_quantity", "remaining_quantity", mode="before")(_decimal)
    _validate_time = field_validator("provider_truth_at")(_aware)

    @model_validator(mode="after")
    def validate_reconciliation(self) -> "ReconciliationResultReferenceV1":
        if self.evidence.kind is not LineageKind.RECONCILIATION:
            raise ValueError("evidence must be RECONCILIATION lineage")
        if self.status is ReconciliationStatus.FILLED and self.remaining_quantity not in {None, Decimal("0")}:
            raise ValueError("filled reconciliation cannot have remaining quantity")
        if self.status is ReconciliationStatus.PARTIALLY_FILLED:
            if self.filled_quantity <= 0 or self.remaining_quantity is None or self.remaining_quantity <= 0:
                raise ValueError("partial reconciliation requires positive filled and remaining quantities")
        return self


class AccountingResultReferenceV1(_Contract):
    schema_version: Literal[ACCOUNTING_RESULT_REFERENCE_VERSION]
    envelope: CanonicalEnvelopeV1
    accounting_id: AccountingId
    reconciliation_id: ReconciliationId
    source_fill_ids: tuple[NonEmptyText, ...]
    gross_amount: Decimal
    fee_amount: Decimal
    fee_asset: NonEmptyText
    net_amount: Decimal
    currency: NonEmptyText
    realized_pnl: Decimal | None = None
    position_reference: NonEmptyText | None = None
    evidence: LineageReference
    recorded_at: datetime

    _validate_amounts = field_validator(
        "gross_amount", "fee_amount", "net_amount", "realized_pnl", mode="before"
    )(_decimal)
    _validate_time = field_validator("recorded_at")(_aware)

    @model_validator(mode="after")
    def validate_accounting(self) -> "AccountingResultReferenceV1":
        if self.evidence.kind is not LineageKind.ACCOUNTING:
            raise ValueError("evidence must be ACCOUNTING lineage")
        if not self.source_fill_ids:
            raise ValueError("accounting reference requires at least one source fill")
        if self.fee_amount < 0:
            raise ValueError("fee_amount cannot be negative")
        return self
