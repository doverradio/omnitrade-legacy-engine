from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from app.db.session import AsyncSessionLocal
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one autonomous preview cycle without submission")
    parser.add_argument("--mandate-id", type=UUID, required=True)
    parser.add_argument("--actor", type=str, required=True)
    parser.add_argument("--product-id", type=str, default="BTC-USD")
    parser.add_argument("--strategy-interval", type=str, default="15m")
    parser.add_argument("--trigger", type=str, default="manual")
    parser.add_argument("--idempotency-seed", type=str, default=None)
    parser.add_argument("--reuse-idempotency-key", action="store_true", help="Reuse the orchestrator-derived idempotency key instead of minting a fresh preview seed.")
    parser.add_argument("--software-build-version", type=str, default=None)
    parser.add_argument("--forced-action", type=str, choices=["BUY", "SELL", "HOLD"], default=None)
    return parser.parse_args(argv)


def _resolve_idempotency_seed(args: argparse.Namespace) -> str | None:
    if getattr(args, "reuse_idempotency_key", False):
        return None
    if args.idempotency_seed:
        return args.idempotency_seed
    return uuid4().hex


async def _run_once(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await run_autonomous_preview_cycle(
            db=db,
            request=AutonomousCycleRequest(
                mandate_id=args.mandate_id,
                actor=args.actor,
                product_id=args.product_id,
                strategy_interval=args.strategy_interval,
                trigger=args.trigger,
                idempotency_seed=_resolve_idempotency_seed(args),
                software_build_version=args.software_build_version,
                forced_action=args.forced_action,
            ),
        )

    payload = {
        "cycle_id": str(result.cycle_id),
        "state": result.state,
        "idempotency_key": result.idempotency_key,
        "mandate_id": str(result.mandate_id),
        "mandate_version_id": str(result.mandate_version_id) if result.mandate_version_id else None,
        "proposed_action": result.proposed_action,
        "mandate_verdict": result.mandate_verdict,
        "risk_verdict": result.risk_verdict,
        "decision_record_id": str(result.decision_record_id) if result.decision_record_id else None,
        "preview_id": str(result.preview_id) if result.preview_id else None,
        "mandate_evaluation_id": str(result.mandate_evaluation_id) if result.mandate_evaluation_id else None,
        "risk_event_id": str(result.risk_event_id) if result.risk_event_id else None,
        "audit_correlation_id": str(result.audit_correlation_id),
        "replayed": result.replayed,
        "cycle_context": result.cycle_context,
        "diagnostics": {
            "duration_ms": result.diagnostics.duration_ms,
            "evaluation_stage": result.diagnostics.evaluation_stage,
            "termination_stage": result.diagnostics.termination_stage,
            "failure_reason": result.diagnostics.failure_reason,
            "deterministic_explanation": list(result.diagnostics.deterministic_explanation),
        },
    }

    print(json.dumps(payload, sort_keys=True))
    return 0 if result.state in {"COMPLETE", "PREVIEW_READY"} else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run_once(args))


if __name__ == "__main__":
    raise SystemExit(main())
