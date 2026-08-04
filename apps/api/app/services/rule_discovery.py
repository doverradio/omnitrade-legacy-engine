from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.schemas.strategy_lab_offline import BranchReplayRequest, CandidateRuleCreateRequest, CandidateRuleUpdateRequest
from app.services.strategy_lab_offline import _config, _dataset_path, _identity, load_dataset

from strategy_lab.rule_discovery import (
    CandidateRule, StrategyBranch, build_strategy_package, create_candidate_rule, create_strategy_branch,
    overfitting_warnings, promotion_eligibility, replay_branch_partition, SUPPORTED_ENTRY_REPLAY_ACTIONS,
    validate_rule_document,
)
from strategy_lab.rule_discovery.schema import RuleValidationError
from strategy_lab.capital import CapitalPolicy

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_ARTIFACT_ROOT = Path(os.environ.get("OMNITRADE_RULE_DISCOVERY_ROOT", _PROJECT_ROOT / "strategy_lab_data" / ".rule_discovery"))
_LOCK = RLock()
_RULE_ID = re.compile(r"CR-\d{6}")
_BRANCH_ID = re.compile(r"SB-(001|002)-[0-9a-f]{12}-draft")


def create_rule(payload: CandidateRuleCreateRequest) -> dict[str, Any]:
    try:
        normalized = validate_rule_document(payload.rule_document)
    except RuleValidationError as exc:
        raise InvalidRequestError(message=str(exc)) from exc
    creation = {
        **payload.model_dump(exclude={"rule_document"}),
        "source_finding_ids": sorted(payload.source_finding_ids),
        "rule_document": normalized,
    }
    fingerprint = _hash(creation)
    with _LOCK:
        for path in _directory("rules").glob("CR-*.json"):
            stored = _read(path)
            if stored.get("creation_fingerprint") == fingerprint:
                return stored["candidate_rule"]
        existing = [int(path.stem[3:]) for path in _directory("rules").glob("CR-*.json") if _RULE_ID.fullmatch(path.stem)]
        candidate = create_candidate_rule(
            candidate_rule_id=f"CR-{max(existing, default=0) + 1:06d}",
            name=payload.name, description=payload.description, source_analysis_id=payload.source_analysis_id,
            source_finding_ids=tuple(payload.source_finding_ids),
            source_candidate_experiment_id=payload.source_candidate_experiment_id,
            parent_strategy_version=payload.parent_strategy_version, rule_document=normalized,
            created_by=payload.created_by, research_notes=payload.research_notes, evidence=payload.evidence,
        )
        _write_new(_path("rules", candidate.candidate_rule_id), {"creation_fingerprint": fingerprint, "candidate_rule": asdict(candidate)})
        return asdict(candidate)


def validate_rule_document_request(rule_document: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = validate_rule_document(rule_document)
    except RuleValidationError as exc:
        raise InvalidRequestError(message=str(exc)) from exc
    return {"valid": True, "normalized_rule": normalized}


def list_rules() -> list[dict[str, Any]]:
    return [_read(path)["candidate_rule"] for path in sorted(_directory("rules").glob("CR-*.json"))]


def get_rule(rule_id: str) -> dict[str, Any]:
    return _rule_record(rule_id)["candidate_rule"]


def update_rule(rule_id: str, payload: CandidateRuleUpdateRequest) -> dict[str, Any]:
    record = _rule_record(rule_id)
    current = _candidate(record["candidate_rule"])
    if current.status != "DRAFT":
        raise ConflictError(message="Only DRAFT Candidate Rules may be edited")
    try:
        normalized = validate_rule_document(payload.rule_document)
    except RuleValidationError as exc:
        raise InvalidRequestError(message=str(exc)) from exc
    updated = create_candidate_rule(
        candidate_rule_id=current.candidate_rule_id, name=payload.name, description=payload.description,
        source_analysis_id=current.source_analysis_id, source_finding_ids=current.source_finding_ids,
        source_candidate_experiment_id=current.source_candidate_experiment_id,
        parent_strategy_version=current.parent_strategy_version, rule_document=normalized,
        created_by=current.created_by, created_at=current.created_at, research_notes=payload.research_notes,
        evidence=current.evidence,
    )
    _atomic_replace(_path("rules", rule_id), {**record, "candidate_rule": asdict(updated)})
    return asdict(updated)


def validate_rule(rule_id: str) -> dict[str, Any]:
    candidate = _candidate(get_rule(rule_id))
    normalized = validate_rule_document(_rule_document(candidate))
    return {"valid": True, "candidate_rule_id": rule_id, "normalized_rule": normalized, "content_hash": candidate.content_hash}


def create_branch(rule_id: str) -> dict[str, Any]:
    candidate = _candidate(get_rule(rule_id))
    action = candidate.action["action"]
    if action not in SUPPORTED_ENTRY_REPLAY_ACTIONS:
        raise InvalidRequestError(message=f"{action} is not executable by the Phase 1 entry-rule replay hook")
    branch = create_strategy_branch(candidate)
    path = _path("branches", branch.strategy_branch_id)
    payload = asdict(branch)
    with _LOCK:
        if path.exists():
            existing = _read(path)
            if existing.get("content_hash") != branch.content_hash:
                raise ConflictError(message="Conflicting immutable strategy branch identity")
            return existing
        _write_new(path, payload)
    return payload


def replay_branch(branch_id: str, request: BranchReplayRequest) -> dict[str, Any]:
    branch = _branch(branch_id)
    candidate = _candidate(get_rule(branch.candidate_rule_id))
    if candidate.content_hash != branch.candidate_rule_content_hash:
        raise ConflictError(message="Candidate Rule changed after immutable branch creation")
    dataset_path = _dataset_path(request.dataset_id)
    _, interval = _identity(dataset_path)
    capital_policy = CapitalPolicy(
        name="candidate_rule_request",
        trade_deployment_pct=request.parameters.trade_deployment_pct,
        profit_compound_pct=request.parameters.profit_compound_pct,
        profit_withdrawal_pct=request.parameters.profit_withdrawal_pct,
        profit_tax_reserve_pct=request.parameters.profit_tax_reserve_pct,
    )
    report = replay_branch_partition(
        load_dataset(request.dataset_id), candidate, branch, _config(request.parameters, interval),
        request.partition, capital_policy,
    )
    artifact = {
        **report,
        "dataset_id": request.dataset_id,
        "candidate_rule_id": candidate.candidate_rule_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact["content_hash"] = _hash({key: value for key, value in artifact.items() if key != "created_at"})
    path = _replay_path(branch_id, request.dataset_id, request.partition)
    if path.exists() and _read(path)["content_hash"] == artifact["content_hash"]:
        return _read(path)
    _atomic_replace(path, artifact)
    return artifact


def branch_comparison(branch_id: str, dataset_id: str) -> dict[str, Any]:
    _branch(branch_id)
    _dataset_path(dataset_id)
    reports = _reports(branch_id, dataset_id)
    candidate = _candidate(get_rule(_branch(branch_id).candidate_rule_id))
    return {
        "strategy_branch_id": branch_id,
        "dataset_id": dataset_id,
        "reports": reports,
        "overfitting_warnings": overfitting_warnings(reports, rules_tested_on_dataset=_dataset_rule_count(dataset_id)),
        "promotion": promotion_eligibility(candidate, reports),
    }


def branch_package(branch_id: str, dataset_id: str) -> dict[str, Any]:
    branch = _branch(branch_id)
    candidate = _candidate(get_rule(branch.candidate_rule_id))
    path = _dataset_path(dataset_id)
    asset, interval = _identity(path)
    reports = _reports(branch_id, dataset_id)
    package = build_strategy_package(
        candidate=candidate, branch=branch,
        dataset_identity={"id": dataset_id, "asset": asset, "interval": interval, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        reports=reports, feature_versions={"rule_features": "1.0.0"},
        detector_versions={str(key): str(value) for key, value in candidate.evidence.get("detector_versions", {}).items()},
    )
    certificate = {
        "certificate_version": "1.0.0", "candidate_rule_id": candidate.candidate_rule_id,
        "strategy_branch_id": branch_id, "dataset_id": dataset_id,
        "sample_size": sum(int(report["rule_match_count"]) for report in reports.values()),
        "robustness_warnings": overfitting_warnings(reports, rules_tested_on_dataset=_dataset_rule_count(dataset_id)),
        "verdict": package["promotion_status"], "promotion_eligibility": promotion_eligibility(candidate, reports),
        "strategy_package_content_hash": package["content_hash"],
    }
    certificate["content_hash"] = _hash(certificate)
    _atomic_replace(_package_path(branch_id, dataset_id), package)
    _atomic_replace(_certificate_path(branch_id, dataset_id), certificate)
    return {"strategy_package": package, "certificate": certificate}


def _reports(branch_id: str, dataset_id: str) -> dict[str, dict[str, Any]]:
    reports = {}
    for partition in ("training", "validation", "final_test", "entire_dataset"):
        path = _replay_path(branch_id, dataset_id, partition)
        if path.exists():
            reports[partition] = _read(path)
    return reports


def _dataset_rule_count(dataset_id: str) -> int:
    return len({path.parent.parent.name for path in _directory("replays").glob(f"*/{dataset_id}/*.json")})


def _candidate(data: dict[str, Any]) -> CandidateRule:
    return CandidateRule(**{**data, "source_finding_ids": tuple(data["source_finding_ids"])})


def _branch(branch_id: str) -> StrategyBranch:
    _validate_id(branch_id, _BRANCH_ID, "strategy branch")
    path = _path("branches", branch_id)
    if not path.exists():
        raise NotFoundError(message="Draft strategy branch not found")
    return StrategyBranch(**_read(path))


def _rule_record(rule_id: str) -> dict[str, Any]:
    _validate_id(rule_id, _RULE_ID, "Candidate Rule")
    path = _path("rules", rule_id)
    if not path.exists():
        raise NotFoundError(message="Candidate Rule not found")
    return _read(path)


def _rule_document(candidate: CandidateRule) -> dict[str, Any]:
    return {"schema_version": candidate.rule_schema_version, "when": candidate.conditions, "then": candidate.action, "risk_controls": candidate.risk_controls}


def _validate_id(value: str, pattern: re.Pattern, label: str) -> None:
    if not pattern.fullmatch(value):
        raise InvalidRequestError(message=f"Invalid {label} identifier")


def _directory(name: str) -> Path:
    path = _ARTIFACT_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(directory: str, identifier: str) -> Path:
    return _directory(directory) / f"{identifier}.json"


def _replay_path(branch_id: str, dataset_id: str, partition: str) -> Path:
    _validate_id(branch_id, _BRANCH_ID, "strategy branch")
    if Path(dataset_id).name != dataset_id or not re.fullmatch(r"[A-Za-z0-9_-]+", dataset_id):
        raise InvalidRequestError(message="Invalid dataset identifier")
    path = _directory("replays") / branch_id / dataset_id
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{partition}.json"


def _package_path(branch_id: str, dataset_id: str) -> Path:
    return _replay_path(branch_id, dataset_id, "package").with_name("strategy-package.json")


def _certificate_path(branch_id: str, dataset_id: str) -> Path:
    return _replay_path(branch_id, dataset_id, "certificate").with_name("candidate-rule-certificate.json")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ConflictError(message="Immutable artifact already exists") from exc


def _atomic_replace(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)