from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from .schema import RULE_SCHEMA_VERSION, validate_rule_document

CANDIDATE_RULE_STATUSES = (
    "DRAFT", "READY_TO_TEST", "TRAINING_PASSED", "VALIDATION_PASSED",
    "FINAL_TEST_PASSED", "REJECTED", "PROMOTABLE", "ARCHIVED",
)
SIMULATOR_VERSION = "strategy-lab-1.0.0"


@dataclass(frozen=True)
class CandidateRule:
    candidate_rule_id: str
    name: str
    description: str
    status: str
    source_analysis_id: str
    source_finding_ids: tuple[str, ...]
    source_candidate_experiment_id: str
    parent_strategy_version: str
    conditions: dict[str, Any]
    action: dict[str, Any]
    risk_controls: dict[str, Any]
    created_by: str
    created_at: str
    rule_schema_version: str = RULE_SCHEMA_VERSION
    content_hash: str = ""
    research_notes: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyBranch:
    strategy_branch_id: str
    parent_strategy_version: str
    candidate_rule_id: str
    candidate_rule_content_hash: str
    rule_document: dict[str, Any]
    simulator_version: str
    created_at: str
    content_hash: str
    status: str = "DRAFT"


def create_candidate_rule(
    *, candidate_rule_id: str, name: str, description: str, source_analysis_id: str,
    source_finding_ids: tuple[str, ...], source_candidate_experiment_id: str,
    parent_strategy_version: str, rule_document: Mapping[str, Any], created_by: str,
    created_at: str | None = None, research_notes: str = "", evidence: Mapping[str, Any] | None = None,
) -> CandidateRule:
    normalized = validate_rule_document(rule_document)
    if not candidate_rule_id.startswith("CR-") or not candidate_rule_id[3:].isdigit():
        raise ValueError("candidate_rule_id must use CR-###### format")
    if parent_strategy_version not in {"001", "002"}:
        raise ValueError("parent strategy must be an immutable supported version")
    if not source_finding_ids:
        raise ValueError("candidate rules require source finding IDs")
    created = created_at or datetime.now(timezone.utc).isoformat()
    candidate = CandidateRule(
        candidate_rule_id=candidate_rule_id, name=name.strip(), description=description.strip(), status="DRAFT",
        source_analysis_id=source_analysis_id, source_finding_ids=tuple(source_finding_ids),
        source_candidate_experiment_id=source_candidate_experiment_id,
        parent_strategy_version=parent_strategy_version, conditions=normalized["when"], action=normalized["then"],
        risk_controls=normalized["risk_controls"], created_by=created_by, created_at=created,
        research_notes=research_notes, evidence=dict(evidence or {}),
    )
    return replace(candidate, content_hash=_content_hash({**asdict(candidate), "content_hash": ""}))


def create_strategy_branch(candidate: CandidateRule, *, created_at: str | None = None) -> StrategyBranch:
    rule_document = {
        "schema_version": candidate.rule_schema_version,
        "when": candidate.conditions,
        "then": candidate.action,
        "risk_controls": candidate.risk_controls,
    }
    identity = _content_hash({
        "parent_strategy_version": candidate.parent_strategy_version,
        "candidate_rule_content_hash": candidate.content_hash,
        "rule_document": rule_document,
        "simulator_version": SIMULATOR_VERSION,
    })
    branch = StrategyBranch(
        strategy_branch_id=f"SB-{candidate.parent_strategy_version}-{identity[:12]}-draft",
        parent_strategy_version=candidate.parent_strategy_version,
        candidate_rule_id=candidate.candidate_rule_id,
        candidate_rule_content_hash=candidate.content_hash,
        rule_document=rule_document,
        simulator_version=SIMULATOR_VERSION,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        content_hash=identity,
    )
    return branch


def _content_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()