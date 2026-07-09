from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_alternative_action import DecisionAlternativeAction
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.services.decisions.contracts import DecisionRecordContract, DecisionSnapshotContract


DECISION_PACKAGE_SCHEMA_VERSION = "dp_v1"
DECISION_PACKAGE_HASH_PREFIX = "decision-package"

AvailabilityState = Literal["known", "unknown", "unavailable"]


@dataclass(frozen=True, slots=True)
class DecisionExplainabilityEvidenceContract:
    id: uuid.UUID
    decision_id: uuid.UUID
    evidence_role: str
    evidence_name: str
    evidence_payload: dict[str, Any]
    provenance: dict[str, Any]
    availability_state: AvailabilityState
    state_reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionQualityScoreContract:
    id: uuid.UUID
    decision_id: uuid.UUID
    idempotency_key: str
    scoring_model_version: str
    composite_score: Decimal
    component_scores: list[dict[str, Any]]
    weight_profile: dict[str, str]
    provenance: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionCounterfactualResultContract:
    id: uuid.UUID
    decision_id: uuid.UUID
    idempotency_key: str
    horizon_label: str
    horizon_minutes: int
    decision_timestamp: datetime
    evaluated_at: datetime
    asset_symbol: str
    actual_action: str
    shadow_buy_return_pct: Decimal | None
    shadow_sell_return_pct: Decimal | None
    shadow_wait_return_pct: Decimal | None
    best_action: str | None
    actual_action_correct: bool | None
    evaluation_state: AvailabilityState
    state_reason: str | None
    lesson_tags: list[dict[str, Any]]
    feature_snapshot: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionAlternativeActionContract:
    id: uuid.UUID
    decision_id: uuid.UUID
    idempotency_key: str
    chosen_action: str
    alternative_action: str
    reference_horizon_minutes: int | None
    comparison_payload: dict[str, Any]
    provenance: dict[str, Any]
    availability_state: AvailabilityState
    state_reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionPackageAvailabilityContract:
    decision_snapshot: AvailabilityState
    explainability_records: AvailabilityState
    quality_scores: AvailabilityState
    counterfactual_results: AvailabilityState
    alternative_actions: AvailabilityState


@dataclass(frozen=True, slots=True)
class DecisionPackageContract:
    schema_version: str
    decision_id: uuid.UUID
    built_at: datetime
    content_hash: str
    source_lineage: dict[str, list[str]]
    field_provenance: dict[str, list[dict[str, object]]]
    decision_record: DecisionRecordContract
    decision_snapshot: DecisionSnapshotContract | None
    explainability_records: list[DecisionExplainabilityEvidenceContract]
    quality_scores: list[DecisionQualityScoreContract]
    counterfactual_results: list[DecisionCounterfactualResultContract]
    alternative_actions: list[DecisionAlternativeActionContract]
    availability_state: DecisionPackageAvailabilityContract


class DecisionPackageBuilder:
    async def build_decision_package(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> DecisionPackageContract | None:
        decision_record = await self._load_decision_record(db=db, decision_id=decision_id)
        if decision_record is None:
            return None

        decision_snapshot = await self._load_decision_snapshot(db=db, decision_id=decision_id)
        explainability_records = await self._load_explainability_records(db=db, decision_id=decision_id)
        quality_scores = await self._load_quality_scores(db=db, decision_id=decision_id)
        counterfactual_results = await self._load_counterfactual_results(db=db, decision_id=decision_id)
        alternative_actions = await self._load_alternative_actions(db=db, decision_id=decision_id)

        return self._compose_package(
            decision_record=decision_record,
            decision_snapshot=decision_snapshot,
            explainability_records=explainability_records,
            quality_scores=quality_scores,
            counterfactual_results=counterfactual_results,
            alternative_actions=alternative_actions,
        )

    async def _load_decision_record(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> DecisionRecord | None:
        return await db.scalar(
            select(DecisionRecord)
            .where(DecisionRecord.decision_id == decision_id)
            .limit(1)
        )

    async def _load_decision_snapshot(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> DecisionSnapshot | None:
        return await db.scalar(
            select(DecisionSnapshot)
            .where(DecisionSnapshot.decision_id == decision_id)
            .limit(1)
        )

    async def _load_explainability_records(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> list[DecisionExplainabilityRecord]:
        result = await db.execute(
            select(DecisionExplainabilityRecord)
            .where(DecisionExplainabilityRecord.decision_id == decision_id)
            .order_by(DecisionExplainabilityRecord.created_at.asc(), DecisionExplainabilityRecord.id.asc())
        )
        return list(result.scalars().all())

    async def _load_quality_scores(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> list[DecisionQualityScore]:
        result = await db.execute(
            select(DecisionQualityScore)
            .where(DecisionQualityScore.decision_id == decision_id)
            .order_by(DecisionQualityScore.created_at.asc(), DecisionQualityScore.id.asc())
        )
        return list(result.scalars().all())

    async def _load_counterfactual_results(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> list[DecisionCounterfactualResult]:
        result = await db.execute(
            select(DecisionCounterfactualResult)
            .where(DecisionCounterfactualResult.decision_id == decision_id)
            .order_by(DecisionCounterfactualResult.horizon_minutes.asc(), DecisionCounterfactualResult.id.asc())
        )
        return list(result.scalars().all())

    async def _load_alternative_actions(
        self,
        *,
        db: AsyncSession,
        decision_id: uuid.UUID,
    ) -> list[DecisionAlternativeAction]:
        result = await db.execute(
            select(DecisionAlternativeAction)
            .where(DecisionAlternativeAction.decision_id == decision_id)
            .order_by(DecisionAlternativeAction.created_at.asc(), DecisionAlternativeAction.id.asc())
        )
        return list(result.scalars().all())

    def _compose_package(
        self,
        *,
        decision_record: DecisionRecord,
        decision_snapshot: DecisionSnapshot | None,
        explainability_records: list[DecisionExplainabilityRecord],
        quality_scores: list[DecisionQualityScore],
        counterfactual_results: list[DecisionCounterfactualResult],
        alternative_actions: list[DecisionAlternativeAction],
    ) -> DecisionPackageContract:
        decision_record_contract = self._to_decision_record_contract(decision_record=decision_record)
        decision_snapshot_contract = (
            self._to_decision_snapshot_contract(decision_snapshot=decision_snapshot)
            if decision_snapshot is not None
            else None
        )
        explainability_contracts = [self._to_explainability_contract(record=item) for item in explainability_records]
        quality_contracts = [self._to_quality_contract(record=item) for item in quality_scores]
        counterfactual_contracts = [
            self._to_counterfactual_contract(record=item)
            for item in counterfactual_results
        ]
        alternative_contracts = [
            self._to_alternative_action_contract(record=item)
            for item in alternative_actions
        ]

        availability_state = DecisionPackageAvailabilityContract(
            decision_snapshot="known" if decision_snapshot_contract is not None else "unavailable",
            explainability_records="known" if explainability_contracts else "unavailable",
            quality_scores="known" if quality_contracts else "unavailable",
            counterfactual_results="known" if counterfactual_contracts else "unavailable",
            alternative_actions="known" if alternative_contracts else "unavailable",
        )

        built_at = datetime.now(timezone.utc)
        source_lineage = _copy_string_lists(decision_record.source_lineage)
        field_provenance = _copy_field_provenance(decision_record.field_provenance)

        package_without_hash = {
            "schema_version": DECISION_PACKAGE_SCHEMA_VERSION,
            "decision_id": str(decision_record.decision_id),
            "source_lineage": source_lineage,
            "field_provenance": field_provenance,
            "decision_record": decision_record_contract,
            "decision_snapshot": decision_snapshot_contract,
            "explainability_records": explainability_contracts,
            "quality_scores": quality_contracts,
            "counterfactual_results": counterfactual_contracts,
            "alternative_actions": alternative_contracts,
            "availability_state": availability_state,
        }
        content_hash = _build_package_content_hash(package_without_hash)

        return DecisionPackageContract(
            schema_version=DECISION_PACKAGE_SCHEMA_VERSION,
            decision_id=decision_record.decision_id,
            built_at=built_at,
            content_hash=content_hash,
            source_lineage=source_lineage,
            field_provenance=field_provenance,
            decision_record=decision_record_contract,
            decision_snapshot=decision_snapshot_contract,
            explainability_records=explainability_contracts,
            quality_scores=quality_contracts,
            counterfactual_results=counterfactual_contracts,
            alternative_actions=alternative_contracts,
            availability_state=availability_state,
        )

    def _to_decision_record_contract(self, *, decision_record: DecisionRecord) -> DecisionRecordContract:
        return DecisionRecordContract(
            version=decision_record.version,
            timestamp=decision_record.timestamp,
            asset=_copy_any_dict(decision_record.asset),
            timeframe=decision_record.timeframe,
            market_regime=_copy_any_dict(decision_record.market_regime),
            indicators=_copy_any_dict(decision_record.indicators),
            generated_signals=_copy_dict_list(decision_record.generated_signals),
            signal_strength=decision_record.signal_strength,
            confidence=decision_record.confidence,
            supporting_strategies=_copy_dict_list(decision_record.supporting_strategies),
            opposing_strategies=_copy_dict_list(decision_record.opposing_strategies),
            risk_adjustments=_copy_dict_list(decision_record.risk_adjustments),
            expected_risk=_copy_optional_dict(decision_record.expected_risk),
            expected_reward=_copy_optional_dict(decision_record.expected_reward),
            position_size=decision_record.position_size,
            trade_accepted=decision_record.trade_accepted,
            trade_rejected_reason=decision_record.trade_rejected_reason,
            execution_details=_copy_optional_dict(decision_record.execution_details),
            exit_details=_copy_optional_dict(decision_record.exit_details),
            pnl=_copy_optional_dict(decision_record.pnl),
            duration=decision_record.duration,
            outcome=decision_record.outcome,
            post_trade_notes=_copy_optional_dict(decision_record.post_trade_notes),
            lessons_learned=_copy_optional_dict_list(decision_record.lessons_learned),
            ai_reflection=_copy_optional_dict(decision_record.ai_reflection),
            future_tags=_copy_optional_string_list(decision_record.future_tags),
            confidence_calibration=_copy_optional_dict(decision_record.confidence_calibration),
            review_status=decision_record.review_status,
            human_notes=decision_record.human_notes,
        )

    def _to_decision_snapshot_contract(self, *, decision_snapshot: DecisionSnapshot) -> DecisionSnapshotContract:
        return DecisionSnapshotContract(
            timestamp=decision_snapshot.timestamp,
            asset=_copy_any_dict(decision_snapshot.asset),
            exchange=decision_snapshot.exchange,
            timeframe=decision_snapshot.timeframe,
            ohlcv_context=_copy_dict_list(decision_snapshot.ohlcv_context),
            indicators=_copy_any_dict(decision_snapshot.indicators),
            generated_features=_copy_any_dict(decision_snapshot.generated_features),
            market_regime=_copy_any_dict(decision_snapshot.market_regime),
            volatility=_copy_any_dict(decision_snapshot.volatility),
            spread_liquidity_context=_copy_optional_dict(decision_snapshot.spread_liquidity_context),
            strategy_inputs=_copy_any_dict(decision_snapshot.strategy_inputs),
            risk_inputs=_copy_any_dict(decision_snapshot.risk_inputs),
            current_position_state=_copy_optional_dict(decision_snapshot.current_position_state),
            open_trades=_copy_dict_list(decision_snapshot.open_trades),
            portfolio_exposure=_copy_any_dict(decision_snapshot.portfolio_exposure),
            parameter_set_version=decision_snapshot.parameter_set_version,
            strategy_version=decision_snapshot.strategy_version,
            ai_model_version=decision_snapshot.ai_model_version,
            decision_engine_version=decision_snapshot.decision_engine_version,
            configuration_version=decision_snapshot.configuration_version,
        )

    def _to_explainability_contract(
        self,
        *,
        record: DecisionExplainabilityRecord,
    ) -> DecisionExplainabilityEvidenceContract:
        return DecisionExplainabilityEvidenceContract(
            id=record.id,
            decision_id=record.decision_id,
            evidence_role=record.evidence_role,
            evidence_name=record.evidence_name,
            evidence_payload=_copy_any_dict(record.evidence_payload),
            provenance=_copy_any_dict(record.provenance),
            availability_state=record.availability_state,
            state_reason=record.state_reason,
            created_at=record.created_at,
        )

    def _to_quality_contract(self, *, record: DecisionQualityScore) -> DecisionQualityScoreContract:
        return DecisionQualityScoreContract(
            id=record.id,
            decision_id=record.decision_id,
            idempotency_key=record.idempotency_key,
            scoring_model_version=record.scoring_model_version,
            composite_score=record.composite_score,
            component_scores=_copy_dict_list(record.component_scores),
            weight_profile=dict(record.weight_profile),
            provenance=_copy_any_dict(record.provenance),
            created_at=record.created_at,
        )

    def _to_counterfactual_contract(self, *, record: DecisionCounterfactualResult) -> DecisionCounterfactualResultContract:
        return DecisionCounterfactualResultContract(
            id=record.id,
            decision_id=record.decision_id,
            idempotency_key=record.idempotency_key,
            horizon_label=record.horizon_label,
            horizon_minutes=record.horizon_minutes,
            decision_timestamp=record.decision_timestamp,
            evaluated_at=record.evaluated_at,
            asset_symbol=record.asset_symbol,
            actual_action=record.actual_action,
            shadow_buy_return_pct=record.shadow_buy_return_pct,
            shadow_sell_return_pct=record.shadow_sell_return_pct,
            shadow_wait_return_pct=record.shadow_wait_return_pct,
            best_action=record.best_action,
            actual_action_correct=record.actual_action_correct,
            evaluation_state=record.evaluation_state,
            state_reason=record.state_reason,
            lesson_tags=_copy_dict_list(record.lesson_tags),
            feature_snapshot=_copy_any_dict(record.feature_snapshot),
            created_at=record.created_at,
        )

    def _to_alternative_action_contract(
        self,
        *,
        record: DecisionAlternativeAction,
    ) -> DecisionAlternativeActionContract:
        return DecisionAlternativeActionContract(
            id=record.id,
            decision_id=record.decision_id,
            idempotency_key=record.idempotency_key,
            chosen_action=record.chosen_action,
            alternative_action=record.alternative_action,
            reference_horizon_minutes=record.reference_horizon_minutes,
            comparison_payload=_copy_any_dict(record.comparison_payload),
            provenance=_copy_any_dict(record.provenance),
            availability_state=record.availability_state,
            state_reason=record.state_reason,
            created_at=record.created_at,
        )


def _build_package_content_hash(package: dict[str, Any]) -> str:
    serialized = json.dumps(_normalize_value(package), sort_keys=True, separators=(",", ":"))
    digest = sha256(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{DECISION_PACKAGE_HASH_PREFIX}:{digest}"


def _normalize_value(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _normalize_value(getattr(value, field.name)) for field in fields(value)}

    if isinstance(value, dict):
        return {str(key): _normalize_value(value[key]) for key in sorted(value, key=lambda item: str(item))}

    if isinstance(value, list):
        return [_normalize_value(item) for item in value]

    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]

    if isinstance(value, Decimal):
        return _decimal_to_str(value)

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat()

    return value


def _copy_any_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    return {str(key): _copy_any_value(item) for key, item in value.items()}


def _copy_optional_dict(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if value is None else _copy_any_dict(value)


def _copy_dict_list(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not value:
        return []
    return [_copy_any_dict(item) for item in value]


def _copy_optional_dict_list(value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    return _copy_dict_list(value)


def _copy_optional_string_list(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    return [str(item) for item in value]


def _copy_string_lists(value: dict[str, list[str]]) -> dict[str, list[str]]:
    return {str(key): [str(item) for item in items] for key, items in sorted(value.items(), key=lambda item: str(item[0]))}


def _copy_field_provenance(value: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    return {
        str(key): [_copy_any_dict(item) for item in items]
        for key, items in sorted(value.items(), key=lambda item: str(item[0]))
    }


def _copy_any_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _copy_any_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_any_value(item) for item in value]
    if isinstance(value, tuple):
        return [_copy_any_value(item) for item in value]
    return value


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return format(normalized.quantize(Decimal(1)), "f")
    return format(normalized, "f")