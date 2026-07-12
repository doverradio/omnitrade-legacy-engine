from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.db.session import AsyncSessionLocal
from app.services.mandates.contracts import MandateVersionReplacementRequest
from app.services.mandates.replacement import (
    ReplacementDryRunReport,
    ReplacementExecutionReport,
    dry_run_governing_version_replacement,
    replace_governing_mandate_version,
)
from app.services.strategies.identity import parse_strategy_identity


def _parse_json_object(raw: str, *, field_name: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON") from exc
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field_name} must be a non-empty JSON object")
    return value


def _parse_expires_at(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("--expires-at must include timezone information")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replace the governing immutable mandate version for an active mandate")
    parser.add_argument("--mandate-id", type=UUID, required=True)
    parser.add_argument("--source-mandate-version-id", type=UUID, required=True)
    parser.add_argument("--source-mandate-authorization-id", type=UUID, required=True)
    parser.add_argument("--replacement-strategy-identity", type=str, required=True)
    parser.add_argument("--actor", type=str, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Print the planned replacement without writing any data")
    parser.add_argument("--authorization-method", type=str, default="operator_attestation")
    parser.add_argument("--owner-acknowledgements-json", type=str, required=True)
    parser.add_argument("--authorization-evidence-json", type=str, required=True)
    parser.add_argument("--deterministic-explanation-json", type=str, required=True)
    parser.add_argument("--deployed-git-sha", type=str, required=True)
    parser.add_argument("--expires-at", type=str, default=None)
    parser.add_argument("--idempotency-key", type=str, default=None)
    parser.add_argument("--audit-correlation-id", type=UUID, required=True)
    parser.add_argument("--software-build-version", type=str, default=None)
    return parser.parse_args(argv)


def _validate_replacement_identity(identity: str) -> str:
    parsed = parse_strategy_identity(identity)
    if parsed is None:
        raise ValueError("--replacement-strategy-identity must be canonical slug@module_version")
    return f"{parsed[0]}@{parsed[1]}"


async def _async_main(args: argparse.Namespace) -> int:
    replacement_identity = _validate_replacement_identity(args.replacement_strategy_identity)
    owner_acknowledgements = _parse_json_object(args.owner_acknowledgements_json, field_name="owner_acknowledgements_json")
    authorization_evidence = _parse_json_object(args.authorization_evidence_json, field_name="authorization_evidence_json")
    deterministic_explanation = _parse_json_object(
        args.deterministic_explanation_json,
        field_name="deterministic_explanation_json",
    )
    expires_at = _parse_expires_at(args.expires_at)
    software_build_version = args.software_build_version or args.deployed_git_sha

    request = MandateVersionReplacementRequest(
        mandate_id=args.mandate_id,
        source_mandate_version_id=args.source_mandate_version_id,
        source_mandate_authorization_id=args.source_mandate_authorization_id,
        replacement_allowed_strategy_versions=(replacement_identity,),
        actor=args.actor,
        authorization_method=args.authorization_method,
        owner_acknowledgements=owner_acknowledgements,
        authorization_evidence=authorization_evidence,
        deterministic_explanation=deterministic_explanation,
        deployed_git_sha=args.deployed_git_sha,
        expires_at=expires_at,
        idempotency_key=args.idempotency_key,
        audit_correlation_id=args.audit_correlation_id,
        software_build_version=software_build_version,
    )

    async with AsyncSessionLocal() as db:
        if args.dry_run:
            report = await dry_run_governing_version_replacement(db=db, request=request)
            _print_dry_run(report)
            return 0 if report.stop_reason is None else 2

        report = await replace_governing_mandate_version(db=db, request=request)

    _print_execution(report)
    return 0


def _version_summary_payload(summary) -> dict[str, object]:
    return {
        "mandate_version_id": str(summary.mandate_version_id),
        "version_number": summary.version_number,
        "allowed_strategy_versions": list(summary.allowed_strategy_versions),
        "is_authorized": summary.is_authorized,
        "is_active": summary.is_active,
        "policy_hash": summary.policy_hash,
    }


def _authorization_summary_payload(summary) -> dict[str, object]:
    return {
        "mandate_authorization_id": str(summary.mandate_authorization_id),
        "mandate_version_id": str(summary.mandate_version_id),
        "mandate_version_number": summary.mandate_version_number,
        "authorization_state": summary.authorization_state,
        "approval_result": summary.approval_result,
        "recorded_at": summary.recorded_at.isoformat(),
        "expires_at": summary.expires_at.isoformat() if summary.expires_at else None,
        "revoked_at": summary.revoked_at.isoformat() if summary.revoked_at else None,
    }


def _print_dry_run(report: ReplacementDryRunReport) -> None:
    print(
        json.dumps(
            {
                "mandate_id": str(report.mandate_id),
                "mandate_status": report.mandate_status,
                "source_mandate_version_id": str(report.source_mandate_version_id),
                "source_mandate_authorization_id": str(report.source_mandate_authorization_id),
                "current_governing_version_id": str(report.current_governing_version_id) if report.current_governing_version_id else None,
                "current_governing_strategy_identity": report.current_governing_strategy_identity,
                "source_version_number": report.source_version_number,
                "source_allowed_strategy_versions": list(report.source_allowed_strategy_versions),
                "source_policy_hash": report.source_policy_hash,
                "proposed_replacement_strategy_versions": list(report.proposed_replacement_strategy_versions),
                "proposed_policy_hash": report.proposed_policy_hash,
                "replacement_required": report.replacement_required,
                "stop_reason": report.stop_reason,
                "versions_in_order": [_version_summary_payload(item) for item in report.versions_in_order],
                "exact_version_authorizations": [_authorization_summary_payload(item) for item in report.exact_version_authorizations],
            },
            sort_keys=True,
        )
    )


def _print_execution(report: ReplacementExecutionReport) -> None:
    print(
        json.dumps(
            {
                "dry_run": {
                    "mandate_status": report.dry_run.mandate_status,
                    "source_mandate_version_id": str(report.dry_run.source_mandate_version_id),
                    "current_governing_version_id": str(report.dry_run.current_governing_version_id) if report.dry_run.current_governing_version_id else None,
                    "current_governing_strategy_identity": report.dry_run.current_governing_strategy_identity,
                    "source_policy_hash": report.dry_run.source_policy_hash,
                    "proposed_policy_hash": report.dry_run.proposed_policy_hash,
                    "replacement_required": report.dry_run.replacement_required,
                    "stop_reason": report.dry_run.stop_reason,
                },
                "result": {
                    "mandate_id": str(report.result.mandate_id),
                    "source_mandate_version_id": str(report.result.source_mandate_version_id),
                    "replacement_mandate_version_id": str(report.result.replacement_mandate_version_id),
                    "authorization_id": str(report.result.authorization_id),
                    "mandate_status": report.result.mandate_status,
                    "selected_mandate_version_id": str(report.result.selected_mandate_version_id),
                    "selected_strategy_identity": report.result.selected_strategy_identity,
                    "created_replacement": report.result.created_replacement,
                },
            },
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
