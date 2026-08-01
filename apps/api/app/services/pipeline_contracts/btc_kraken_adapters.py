"""Pure, non-authoritative adapters for verified legacy BTC/Kraken shapes.

The functions in this module perform validation and data conversion only. They
do not import provider implementations or ORM models, acquire authority, or
perform I/O. Required canonical context is always supplied explicitly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol

from app.services.pipeline_contracts.btc_kraken import (
    CANDLE_OBSERVATION_VERSION,
    EXECUTION_INTENT_VERSION,
    PROVIDER_FILL_REFERENCE_VERSION,
    PROVIDER_SUBMISSION_RESULT_VERSION,
    STRATEGY_EVALUATION_VERSION,
    BtcKrakenInstrumentV1,
    CandleObservationV1,
    ExecutionIntentV1,
    ExecutionSide,
    OrderType,
    ProviderFillReferenceV1,
    ProviderOutcome,
    ProviderSubmissionResultV1,
    StrategyAction,
    StrategyEvaluationResultV1,
)
from app.services.pipeline_contracts.envelope import CanonicalEnvelopeV1
from app.services.pipeline_contracts.identifiers import (
    ExecutionClaimId,
    LineageAuthority,
    LineageReference,
    PackageId,
    ProofId,
    ProviderFillId,
    ProviderOrderId,
)
from app.services.pipeline_contracts.serialization import canonical_json


class LegacyNormalizedCandle(Protocol):
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str


class LegacySignal(Protocol):
    action: str
    strength: Decimal
    reason: str
    timestamp: datetime


class LegacySubmissionRequest(Protocol):
    product_id: str
    side: str
    order_type: str
    quote_size: Decimal | None
    base_size: Decimal | None
    client_order_id: str


class LegacyProviderOrder(Protocol):
    provider_order_id: str | None
    client_order_id: str | None
    product_id: str | None
    side: str | None
    status: str | None
    submitted_at: datetime | None
    acknowledged_at: datetime | None


class LegacyProviderFailure(Protocol):
    reason: str
    safe_details: Mapping[str, object]


class LegacyProviderRejection(Protocol):
    code: str
    message: str
    retryable: bool
    provider_status: str | None
    safe_details: Mapping[str, object]


class LegacySubmissionResult(Protocol):
    classification: str
    order: LegacyProviderOrder | None
    rejection: LegacyProviderRejection | None
    ambiguous: LegacyProviderFailure | None


class LegacyProviderFee(Protocol):
    amount: Decimal
    currency: str


class LegacyProviderFill(Protocol):
    provider_fill_id: str | None
    size: Decimal
    price: Decimal
    fee: LegacyProviderFee | None
    occurred_at: datetime | None


def candle_observation_from_legacy(
    candle: LegacyNormalizedCandle,
    *,
    envelope: CanonicalEnvelopeV1,
    instrument: BtcKrakenInstrumentV1,
    interval: str,
) -> CandleObservationV1:
    """Adapt ``data.binance_client.NormalizedCandle`` as emitted by Kraken."""

    if candle.source != "kraken_spot":
        raise ValueError("candle source must be kraken_spot")
    return CandleObservationV1(
        schema_version=CANDLE_OBSERVATION_VERSION,
        envelope=envelope,
        instrument=instrument,
        interval=interval,
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def strategy_evaluation_from_legacy(
    signal: LegacySignal,
    *,
    envelope: CanonicalEnvelopeV1,
    instrument: BtcKrakenInstrumentV1,
    strategy_identity: str,
    strategy_version: str,
    source_signal_ref: LineageReference | None = None,
) -> StrategyEvaluationResultV1:
    """Adapt the established ``strategies.base.Signal`` output shape."""

    actions = {"buy": StrategyAction.BUY, "sell": StrategyAction.SELL, "hold": StrategyAction.HOLD}
    try:
        action = actions[signal.action]
    except KeyError as exc:
        raise ValueError("unsupported legacy strategy action") from exc
    return StrategyEvaluationResultV1(
        schema_version=STRATEGY_EVALUATION_VERSION,
        envelope=envelope,
        instrument=instrument,
        action=action,
        strength=signal.strength,
        reason=signal.reason,
        explanation=(),
        strategy_identity=strategy_identity,
        strategy_version=strategy_version,
        source_signal_ref=source_signal_ref,
        evaluated_at=signal.timestamp,
    )


def execution_intent_from_legacy_request(
    request: LegacySubmissionRequest,
    *,
    envelope: CanonicalEnvelopeV1,
    instrument: BtcKrakenInstrumentV1,
    package_id: PackageId,
    claim_id: ExecutionClaimId,
    authorization_evidence: LineageReference,
    custody_evidence: LineageReference,
    proof_id: ProofId | None = None,
) -> ExecutionIntentV1:
    """Adapt the verified ``ExchangeOrderSubmissionRequest`` shape."""

    if request.product_id != instrument.internal_product:
        raise ValueError("legacy product does not match canonical instrument")
    sides = {"BUY": ExecutionSide.BUY, "SELL": ExecutionSide.SELL}
    if request.side not in sides:
        raise ValueError("unsupported legacy execution side")
    if request.order_type != "MARKET":
        raise ValueError("only the verified MARKET request shape is supported")
    if authorization_evidence.authority is not LineageAuthority.VERIFIED:
        raise ValueError("execution adaptation requires VERIFIED authorization evidence")
    if custody_evidence.authority is not LineageAuthority.VERIFIED:
        raise ValueError("execution adaptation requires VERIFIED custody evidence")
    return ExecutionIntentV1(
        schema_version=EXECUTION_INTENT_VERSION,
        envelope=envelope,
        instrument=instrument,
        side=sides[request.side],
        order_type=OrderType.MARKET,
        quote_notional=request.quote_size,
        base_quantity=request.base_size,
        package_id=package_id,
        claim_id=claim_id,
        proof_id=proof_id,
        internal_client_order_id=request.client_order_id,
        authorization_evidence=authorization_evidence,
        custody_evidence=custody_evidence,
    )


def _safe_typed_fields(values: Mapping[str, object]) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for key, value in values.items():
        if not isinstance(key, str):
            raise ValueError("legacy safe detail keys must be strings")
        if value is None:
            raise ValueError("legacy safe detail values cannot be null")
        result.append((key, "text", value) if isinstance(value, str) else (key, "canonical_json", canonical_json(value)))
    return result


def provider_submission_result_from_legacy(
    result: LegacySubmissionResult,
    *,
    envelope: CanonicalEnvelopeV1,
    execution_intent: ExecutionIntentV1,
) -> ProviderSubmissionResultV1:
    """Adapt ``ExchangeOrderSubmissionResult`` without interpreting ambiguity."""

    internal_client_order_id = execution_intent.internal_client_order_id

    def validate_order_identity(order: LegacyProviderOrder) -> None:
        if order.client_order_id != internal_client_order_id:
            raise ValueError("provider result client order identity mismatch")
        if order.product_id != execution_intent.instrument.internal_product:
            raise ValueError("provider result product identity mismatch")
        if order.side != execution_intent.side.value:
            raise ValueError("provider result side mismatch")

    if result.classification == "success":
        if result.order is None or result.rejection is not None or result.ambiguous is not None:
            raise ValueError("contradictory successful provider result")
        validate_order_identity(result.order)
        return ProviderSubmissionResultV1(
            schema_version=PROVIDER_SUBMISSION_RESULT_VERSION,
            envelope=envelope,
            outcome=ProviderOutcome.ACCEPTED,
            provider="kraken_spot",
            internal_client_order_id=internal_client_order_id,
            provider_order_id=ProviderOrderId(value=result.order.provider_order_id) if result.order.provider_order_id else None,
            provider_status=result.order.status,
            provider_timestamp=result.order.acknowledged_at or result.order.submitted_at,
            reconciliation_required=True,
        )
    if result.classification == "rejected":
        if result.rejection is None or result.ambiguous is not None:
            raise ValueError("contradictory rejected provider result")
        reserved = {"code", "message", "retryable"}
        collisions = reserved.intersection(result.rejection.safe_details)
        if collisions:
            raise ValueError(f"legacy safe details contain reserved keys: {sorted(collisions)}")
        errors = _safe_typed_fields(result.rejection.safe_details)
        errors.extend((
            ("code", "text", result.rejection.code),
            ("message", "text", result.rejection.message),
            ("retryable", "text", "true" if result.rejection.retryable else "false"),
        ))
        rejection_order = result.order
        if rejection_order:
            validate_order_identity(rejection_order)
        return ProviderSubmissionResultV1(
            schema_version=PROVIDER_SUBMISSION_RESULT_VERSION,
            envelope=envelope,
            outcome=ProviderOutcome.REJECTED,
            provider="kraken_spot",
            internal_client_order_id=internal_client_order_id,
            provider_order_id=(
                ProviderOrderId(value=rejection_order.provider_order_id)
                if rejection_order and rejection_order.provider_order_id
                else None
            ),
            provider_status=result.rejection.provider_status,
            reason_code=result.rejection.code,
            safe_error_fields=errors,
            reconciliation_required=False,
        )
    if result.classification == "ambiguous":
        if result.ambiguous is None or result.rejection is not None:
            raise ValueError("contradictory ambiguous provider result")
        errors = _safe_typed_fields(result.ambiguous.safe_details)
        if result.order:
            validate_order_identity(result.order)
        return ProviderSubmissionResultV1(
            schema_version=PROVIDER_SUBMISSION_RESULT_VERSION,
            envelope=envelope,
            outcome=ProviderOutcome.AMBIGUOUS,
            provider="kraken_spot",
            internal_client_order_id=internal_client_order_id,
            provider_order_id=None,
            provider_status=result.order.status if result.order else None,
            reason_code=result.ambiguous.reason,
            safe_error_fields=errors,
            provider_timestamp=(result.order.acknowledged_at or result.order.submitted_at) if result.order else None,
            reconciliation_required=True,
            blind_resubmission_permitted=False,
        )
    raise ValueError("unsupported legacy provider classification")


def provider_fill_reference_from_legacy(fill: LegacyProviderFill) -> ProviderFillReferenceV1:
    """Adapt the verified ``ExchangeProviderFill`` value shape."""

    if fill.provider_fill_id is None:
        raise ValueError("provider fill identity is required")
    return ProviderFillReferenceV1(
        schema_version=PROVIDER_FILL_REFERENCE_VERSION,
        provider_fill_id=ProviderFillId(value=fill.provider_fill_id),
        quantity=fill.size,
        price=fill.price,
        fee_amount=fill.fee.amount if fill.fee else None,
        fee_asset=fill.fee.currency if fill.fee else None,
        occurred_at=fill.occurred_at,
    )
