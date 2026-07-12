from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.risk_event import RiskEvent


PREVIEW_LINKAGE_FEATURE_INTRODUCED_AT = datetime(2026, 7, 9, 22, 30, tzinfo=timezone.utc)

_TERMINAL_PREVIEW_STATUSES = {
    "RISK_REJECTED",
    "PREVIEW_READY",
    "PREVIEW_FAILED",
    "CANCELLED",
    "EXPIRED",
    "BALANCE_INSUFFICIENT",
    "CONNECTION_NOT_READY",
}


@dataclass(slots=True)
class LinkageViolation:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


async def guard_preview_linkage_integrity(
    *,
    db: AsyncSession,
    actor: str,
    preview: CryptoOrderPreview,
    stage: str,
) -> list[LinkageViolation]:
    """Emit explicit integrity events when preview linkage invariants are violated.

    The guard is intentionally non-blocking to avoid changing execution behavior in this phase.
    """

    violations: list[LinkageViolation] = []

    try:
        decision_record_id = preview.decision_record_id
        risk_event_id = preview.risk_event_id

        if decision_record_id is None:
            violations.append(LinkageViolation("missing_decision_record_id", "preview.decision_record_id is NULL"))
        if risk_event_id is None:
            violations.append(LinkageViolation("missing_risk_event_id", "preview.risk_event_id is NULL"))
        if preview.audit_correlation_id is None:
            violations.append(LinkageViolation("missing_audit_correlation_id", "preview.audit_correlation_id is NULL"))

        decision: DecisionRecord | None = None
        if decision_record_id is not None:
            decision = await db.scalar(
                select(DecisionRecord).where(DecisionRecord.decision_id == decision_record_id).limit(1)
            )
            if decision is None:
                violations.append(
                    LinkageViolation("missing_decision_record_row", "preview links to a missing DecisionRecord")
                )

        if risk_event_id is not None:
            risk_event = await db.scalar(select(RiskEvent).where(RiskEvent.id == risk_event_id).limit(1))
            if risk_event is None:
                violations.append(LinkageViolation("missing_risk_event_row", "preview links to a missing RiskEvent"))

        if decision is not None:
            preview_ref = str(preview.crypto_order_preview_id)
            risk_ref = str(preview.risk_event_id) if preview.risk_event_id is not None else None
            correlation_ref = str(preview.audit_correlation_id) if preview.audit_correlation_id is not None else None

            lineage = decision.source_lineage if isinstance(decision.source_lineage, dict) else {}
            execution_details = decision.execution_details if isinstance(decision.execution_details, dict) else {}
            expected_risk = decision.expected_risk if isinstance(decision.expected_risk, dict) else {}

            preview_lineage = lineage.get("crypto_order_previews")
            if not isinstance(preview_lineage, list) or preview_ref not in {str(item) for item in preview_lineage}:
                violations.append(
                    LinkageViolation(
                        "decision_missing_preview_lineage",
                        "DecisionRecord source_lineage.crypto_order_previews does not reference this preview",
                    )
                )

            preview_execution_ref = execution_details.get("preview_id")
            if str(preview_execution_ref or "") != preview_ref:
                violations.append(
                    LinkageViolation(
                        "decision_missing_preview_execution_ref",
                        "DecisionRecord execution_details.preview_id does not reference this preview",
                    )
                )

            decision_risk_ref = expected_risk.get("risk_event_id")
            if risk_ref is not None and str(decision_risk_ref or "") != risk_ref:
                violations.append(
                    LinkageViolation(
                        "decision_missing_expected_risk_ref",
                        "DecisionRecord expected_risk.risk_event_id does not match preview.risk_event_id",
                    )
                )

            decision_correlation_ref = execution_details.get("audit_correlation_id")
            if correlation_ref is not None and str(decision_correlation_ref or "") != correlation_ref:
                violations.append(
                    LinkageViolation(
                        "decision_audit_correlation_mismatch",
                        "DecisionRecord execution_details.audit_correlation_id does not match preview audit correlation",
                    )
                )

        if violations:
            db.add(
                AuditLog(
                    actor=actor,
                    action="decision_linkage_integrity_violation",
                    entity_type="decision_linkage_integrity",
                    entity_id=preview.crypto_order_preview_id,
                    before_state=None,
                    after_state={
                        "stage": stage,
                        "preview_id": str(preview.crypto_order_preview_id),
                        "preview_status": preview.status,
                        "decision_record_id": str(preview.decision_record_id) if preview.decision_record_id else None,
                        "risk_event_id": str(preview.risk_event_id) if preview.risk_event_id else None,
                        "audit_correlation_id": str(preview.audit_correlation_id) if preview.audit_correlation_id else None,
                        "violations": [item.to_dict() for item in violations],
                    },
                )
            )
            await db.flush()
    except Exception as exc:  # pragma: no cover - guard must never block workflow
        db.add(
            AuditLog(
                actor=actor,
                action="decision_linkage_integrity_guard_error",
                entity_type="decision_linkage_integrity",
                entity_id=preview.crypto_order_preview_id,
                before_state=None,
                after_state={
                    "stage": stage,
                    "preview_id": str(preview.crypto_order_preview_id),
                    "error": str(exc),
                },
            )
        )
        await db.flush()

    return violations


async def build_linkage_integrity_summary(
    *,
    db: AsyncSession,
    limit: int = 500,
) -> dict[str, Any]:
    rows = list(
        (
            await db.execute(
                select(CryptoOrderPreview)
                .order_by(CryptoOrderPreview.created_at.desc(), CryptoOrderPreview.preview_version.desc())
                .limit(limit)
            )
        ).scalars().all()
    )

    terminal_rows = [item for item in rows if item.status in _TERMINAL_PREVIEW_STATUSES]

    decision_ids = sorted({item.decision_record_id for item in terminal_rows if item.decision_record_id is not None})
    risk_ids = sorted({item.risk_event_id for item in terminal_rows if item.risk_event_id is not None})

    decision_map: dict[UUID, DecisionRecord] = {}
    if decision_ids:
        decision_records = list(
            (
                await db.execute(
                    select(DecisionRecord).where(DecisionRecord.decision_id.in_(decision_ids))
                )
            ).scalars().all()
        )
        decision_map = {item.decision_id: item for item in decision_records}

    risk_id_set: set[UUID] = set()
    if risk_ids:
        risk_rows = list((await db.execute(select(RiskEvent.id).where(RiskEvent.id.in_(risk_ids)))).scalars().all())
        risk_id_set = set(risk_rows)

    future_violations: list[dict[str, Any]] = []
    historical_exemptions: list[dict[str, Any]] = []
    healthy_count = 0

    for preview in terminal_rows:
        violations: list[dict[str, str]] = []
        decision = decision_map.get(preview.decision_record_id) if preview.decision_record_id is not None else None

        if preview.decision_record_id is None:
            violations.append({"code": "missing_decision_record_id", "message": "preview.decision_record_id is NULL"})
        if preview.risk_event_id is None:
            violations.append({"code": "missing_risk_event_id", "message": "preview.risk_event_id is NULL"})
        if preview.audit_correlation_id is None:
            violations.append({"code": "missing_audit_correlation_id", "message": "preview.audit_correlation_id is NULL"})

        if preview.decision_record_id is not None and decision is None:
            violations.append({"code": "missing_decision_record_row", "message": "linked DecisionRecord does not exist"})
        if preview.risk_event_id is not None and preview.risk_event_id not in risk_id_set:
            violations.append({"code": "missing_risk_event_row", "message": "linked RiskEvent does not exist"})

        if decision is not None:
            preview_ref = str(preview.crypto_order_preview_id)
            risk_ref = str(preview.risk_event_id) if preview.risk_event_id is not None else None
            correlation_ref = str(preview.audit_correlation_id) if preview.audit_correlation_id is not None else None

            lineage = decision.source_lineage if isinstance(decision.source_lineage, dict) else {}
            execution_details = decision.execution_details if isinstance(decision.execution_details, dict) else {}
            expected_risk = decision.expected_risk if isinstance(decision.expected_risk, dict) else {}

            preview_lineage = lineage.get("crypto_order_previews")
            if not isinstance(preview_lineage, list) or preview_ref not in {str(item) for item in preview_lineage}:
                violations.append(
                    {
                        "code": "decision_missing_preview_lineage",
                        "message": "DecisionRecord source_lineage.crypto_order_previews missing preview reference",
                    }
                )

            if str(execution_details.get("preview_id") or "") != preview_ref:
                violations.append(
                    {
                        "code": "decision_missing_preview_execution_ref",
                        "message": "DecisionRecord execution_details.preview_id missing preview reference",
                    }
                )

            if risk_ref is not None and str(expected_risk.get("risk_event_id") or "") != risk_ref:
                violations.append(
                    {
                        "code": "decision_missing_expected_risk_ref",
                        "message": "DecisionRecord expected_risk.risk_event_id mismatch",
                    }
                )

            if correlation_ref is not None and str(execution_details.get("audit_correlation_id") or "") != correlation_ref:
                violations.append(
                    {
                        "code": "decision_audit_correlation_mismatch",
                        "message": "DecisionRecord audit correlation mismatch",
                    }
                )

        if not violations:
            healthy_count += 1
            continue

        created_at = preview.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        issue = {
            "crypto_order_preview_id": str(preview.crypto_order_preview_id),
            "created_at": created_at.isoformat(),
            "status": preview.status,
            "decision_record_id": str(preview.decision_record_id) if preview.decision_record_id else None,
            "risk_event_id": str(preview.risk_event_id) if preview.risk_event_id else None,
            "audit_correlation_id": str(preview.audit_correlation_id) if preview.audit_correlation_id else None,
            "violations": violations,
        }

        if created_at < PREVIEW_LINKAGE_FEATURE_INTRODUCED_AT:
            historical_exemptions.append(issue)
        else:
            future_violations.append(issue)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feature_cutoff_preview_linkage": PREVIEW_LINKAGE_FEATURE_INTRODUCED_AT.isoformat(),
        "scope": {
            "limit": limit,
            "total_rows_scanned": len(rows),
            "terminal_rows_scanned": len(terminal_rows),
        },
        "counts": {
            "healthy": healthy_count,
            "future_violations": len(future_violations),
            "historical_exemptions": len(historical_exemptions),
        },
        "future_violations": future_violations,
        "historical_exemptions": historical_exemptions,
    }
