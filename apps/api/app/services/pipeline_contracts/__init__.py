"""Pure, non-authoritative canonical pipeline contract primitives."""

from app.services.pipeline_contracts.btc_kraken import (
    AccountingResultReferenceV1,
    BtcKrakenInstrumentV1,
    CandleObservationV1,
    ExecutionIntentV1,
    ExecutionSide,
    GovernanceAuthorizationReferenceV1,
    GovernanceDisposition,
    OrderType,
    ProviderFillReferenceV1,
    ProviderOutcome,
    ProviderSubmissionResultV1,
    ReconciliationResultReferenceV1,
    ReconciliationStatus,
    RiskDecisionReferenceV1,
    RiskDisposition,
    StrategyAction,
    StrategyEvaluationResultV1,
)

from app.services.pipeline_contracts.context import (
    EXECUTION_CONTEXT_SCHEMA_VERSION,
    ExecutionContextV1,
    FixedClock,
    OperatingMode,
    VersionManifest,
)
from app.services.pipeline_contracts.envelope import (
    CANONICAL_ENVELOPE_SCHEMA_VERSION,
    CanonicalEnvelopeV1,
    QualityStatus,
)
from app.services.pipeline_contracts.identifiers import (
    AccountingId,
    AssetId,
    AssetIdentity,
    CampaignId,
    CausationId,
    CorrelationId,
    EventId,
    ExecutionClaimId,
    InstrumentId,
    InstrumentIdentity,
    LineageAuthority,
    LineageKind,
    LineageReference,
    PackageId,
    PortfolioId,
    ProofId,
    ProviderOrderId,
    ReconciliationId,
    RunId,
)
from app.services.pipeline_contracts.serialization import canonical_json, canonical_json_bytes, integrity_sha256

__all__ = [
    "AccountingId", "AccountingResultReferenceV1", "AssetId", "AssetIdentity",
    "BtcKrakenInstrumentV1", "CANONICAL_ENVELOPE_SCHEMA_VERSION", "CandleObservationV1",
    "CampaignId", "CausationId", "CorrelationId", "EXECUTION_CONTEXT_SCHEMA_VERSION",
    "EventId", "ExecutionClaimId", "ExecutionContextV1", "ExecutionIntentV1", "ExecutionSide",
    "FixedClock", "GovernanceAuthorizationReferenceV1", "GovernanceDisposition", "InstrumentId",
    "InstrumentIdentity", "LineageAuthority", "LineageKind", "LineageReference",
    "OperatingMode", "OrderType", "PackageId", "PortfolioId", "ProofId", "ProviderFillReferenceV1",
    "ProviderOrderId", "ProviderOutcome", "ProviderSubmissionResultV1", "QualityStatus",
    "ReconciliationId", "ReconciliationResultReferenceV1", "ReconciliationStatus", "RiskDecisionReferenceV1",
    "RiskDisposition", "RunId", "StrategyAction", "StrategyEvaluationResultV1", "VersionManifest", "CanonicalEnvelopeV1",
    "canonical_json", "canonical_json_bytes", "integrity_sha256",
]
