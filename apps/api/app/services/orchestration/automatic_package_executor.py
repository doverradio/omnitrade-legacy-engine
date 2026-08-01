from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.controlled_proof_run import ControlledProofRun
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageActivationRequest,
    CanonicalPreviewPackageDryRunRequest,
    CanonicalPreviewPackageMandateAuthorizeRequest,
    _validate_canonical_package_authority,
    activate_canonical_proving_campaign,
    authorize_canonical_preview_package_under_mandate,
    run_dry_run_for_canonical_preview_package,
)
from app.services.controlled_proof.service import _ACTIVE_STATES as _CONTROLLED_PROOF_ACTIVE_STATES
from app.services.controlled_proof.service import (
    repair_controlled_proof_cached_order_ids,
    resolve_controlled_proof_leg_execution_lineage,
)
from app.services.mandates.contracts import MANDATE_PURPOSE_CONTROLLED_PROOF

logger = logging.getLogger(__name__)

_PROGRESSABLE_STATES = {"READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}

_AUTHORITY_MODE_GLOBAL_CONFIGURED = "GLOBAL_CONFIGURED_SCOPE"
_AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED = "CONTROLLED_PROOF_DERIVED_SCOPE"


@dataclass(frozen=True, slots=True)
class ResolvedAutomaticActivationScope:
    """The one authoritative scope execute_automatic_ready_package_through_activation
    progresses a package under -- resolved exactly once, from exactly one
    source per call (never a mix): either the operator's globally-pinned
    settings (ordinary automation), or a specific Controlled Proof's own
    persisted, already-validated package (authority_mode records which).
    Every field here is a real, persisted value -- never a caller-supplied
    or reconstructed one -- so a conflicting global selector setting can
    never redirect a Controlled-Proof-derived scope to a different package
    or mandate."""

    package_id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_version: int
    mandate_id: uuid.UUID
    mandate_version_id: uuid.UUID
    mandate_evaluation_id: uuid.UUID
    authority_mode: str
    controlled_proof_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class AutomaticPackageExecutionRequest:
    campaign_id: uuid.UUID
    campaign_version: int
    decision_record_id: uuid.UUID
    package_id: uuid.UUID | None = None
    software_build_version: str | None = None


@dataclass(frozen=True, slots=True)
class AutomaticPackageExecutionOutcome:
    package_id: uuid.UUID | None
    campaign_id: uuid.UUID
    campaign_version: int
    decision_record_id: uuid.UUID
    mandate_id: uuid.UUID | None
    authorization_state: str
    dry_run_state: str
    activation_state: str
    authority_source: str | None
    replayed: bool
    final_reason_code: str
    failed_closed: bool
    starting_state: str


def _phase_key(*, package_id: uuid.UUID, phase: str) -> str:
    return f"automatic-mandate-package:{package_id}:{phase}"


def _outcome(
    *,
    request: AutomaticPackageExecutionRequest,
    package: CanonicalPreviewPackage | None,
    reason: str,
    replayed: bool = False,
    failed_closed: bool = False,
    starting_state: str = "UNKNOWN",
) -> AutomaticPackageExecutionOutcome:
    state = "MISSING" if package is None else package.package_state
    return AutomaticPackageExecutionOutcome(
        package_id=None if package is None else package.package_id,
        campaign_id=request.campaign_id,
        campaign_version=request.campaign_version,
        decision_record_id=request.decision_record_id,
        mandate_id=None if package is None else package.mandate_id,
        authorization_state="AUTHORIZED" if state in {"AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"} else state,
        dry_run_state="DRY_RUN_PASSED" if state in {"DRY_RUN_PASSED", "ACTIVATED"} else "NOT_RUN",
        # Deliberately also gated on `not failed_closed`, not package_state
        # alone: the ACTIVATED-replay branch re-validates mandate authority
        # (_validate_canonical_package_authority) on every call, and that
        # re-validation can fail (e.g. authorization_expires_at has since
        # elapsed) for a package whose PERSISTED state is still "ACTIVATED"
        # from an earlier, genuinely-successful activation. Reporting
        # activation_state="ACTIVATED" from persisted state alone in that
        # case told the caller (continuous_pipeline_worker) it was safe to
        # proceed to claim/execute despite failed_closed=True -- the
        # confirmed root cause of the production sequence
        # activation_result=ACTIVATED -> reason_code=mandate ("mandate
        # package authorization expired") -> reason_code=unexpected_executor_failure.
        activation_state="ACTIVATED" if (state == "ACTIVATED" and not failed_closed) else "NOT_ACTIVATED",
        authority_source=None if package is None else package.authorization_source,
        replayed=replayed,
        final_reason_code=reason,
        failed_closed=failed_closed,
        starting_state=starting_state,
    )


async def _resolve_controlled_proof_activation_scope(
    *, db: AsyncSession, request: AutomaticPackageExecutionRequest,
) -> ResolvedAutomaticActivationScope | None:
    """A package created under an explicit, currently-active Controlled
    Proof may receive a narrow, package-scoped activation override -- the
    Controlled Proof itself (an operator-issued, fully-audited
    RUN_CONTROLLED_PROOF request) is its own explicit authority to attempt
    exactly one BUY-to-SELL lifecycle, distinct from (and much narrower
    than) permanently enabling unattended automatic activation for every
    package the campaign ever produces. This is evaluated unconditionally
    by the caller, regardless of the global automatic_mandate_package_
    activation_enabled feature's value -- a Controlled Proof's own
    authority must never depend on whether that unrelated global switch
    happens to be on (e.g. ordinary autonomous production) or off, and must
    never be redirected to the legacy global selector's mandate/campaign
    scope, which is deliberately pinned to a different, ordinary-production
    mandate. Every invariant below is re-verified fresh against the DB on
    every call -- never cached, never assumed from an earlier check in this
    same request -- and returns a ResolvedAutomaticActivationScope built
    exclusively from this exact package's own persisted fields (never from
    global settings) only when all of them hold. Returns None, with one
    precise logged reason, otherwise; the caller falls back to the global
    feature flag exactly as if no Controlled Proof authority applied.
    Ordinary automatic packages (no Controlled Proof linkage at all) always
    resolve to None here and remain governed solely by the existing global
    feature flag, unchanged."""
    logger.info(
        "controlled_proof_activation_override_evaluated campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s",
        request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
    )
    if request.package_id is None:
        logger.info(
            "controlled_proof_activation_override_blocked campaign_id=%s campaign_version=%s decision_record_id=%s package_id=None controlled_proof_id=None reason=no_package_identity",
            request.campaign_id, request.campaign_version, request.decision_record_id,
        )
        logger.info(
            "automatic_activation_scope_blocked authority_mode=%s controlled_proof_id=None package_id=None reason=no_package_identity",
            _AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED,
        )
        return None
    package = await db.get(CanonicalPreviewPackage, request.package_id)
    if package is None:
        logger.info(
            "controlled_proof_activation_override_blocked campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s controlled_proof_id=None reason=package_missing",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
        )
        logger.info(
            "automatic_activation_scope_blocked authority_mode=%s controlled_proof_id=None package_id=%s reason=package_missing",
            _AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED, request.package_id,
        )
        return None

    # Locked, not just read: a concurrent activation attempt for the same
    # proof (e.g. the API's immediate-dispatch task racing this cycle's
    # orchestration poll) must serialize here rather than both observing
    # the same not-yet-terminal proof as eligible.
    proof = await db.scalar(
        select(ControlledProofRun)
        .where(or_(
            ControlledProofRun.package_id == package.package_id,
            ControlledProofRun.sell_package_id == package.package_id,
        ))
        .with_for_update()
        .limit(1)
    )
    if proof is None:
        logger.info(
            "controlled_proof_activation_override_blocked campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s controlled_proof_id=None reason=no_controlled_proof_linkage",
            request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id,
        )
        logger.info(
            "automatic_activation_scope_blocked authority_mode=%s controlled_proof_id=None package_id=%s reason=no_controlled_proof_linkage",
            _AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED, package.package_id,
        )
        return None

    def _blocked(reason: str) -> None:
        logger.info(
            "controlled_proof_activation_override_blocked campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s controlled_proof_id=%s reason=%s",
            request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, proof.proof_id, reason,
        )
        logger.info(
            "automatic_activation_scope_blocked authority_mode=%s controlled_proof_id=%s package_id=%s reason=%s",
            _AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED, proof.proof_id, package.package_id, reason,
        )
        return None

    now = datetime.now(timezone.utc)
    recovery = None
    if (
        proof.status not in _CONTROLLED_PROOF_ACTIVE_STATES
        and package.side == "SELL"
        and proof.sell_package_id == package.package_id
    ):
        # status == "IN_PROGRESS" only, not "AUTHORIZED": eligibility
        # requires the recovery to actually be claimed
        # (claim_exit_recovery_by_id) before it can authorize an activation
        # attempt -- an authorized-but-unclaimed recovery must fail closed.
        recovery = await db.scalar(select(ControlledProofExitRecovery).where(
            ControlledProofExitRecovery.proof_id == proof.proof_id,
            ControlledProofExitRecovery.status == "IN_PROGRESS",
            ControlledProofExitRecovery.expires_at > now,
        ).limit(1))
    # Binding is (proof_id, package_id) alone -- proof.sell_package_id is
    # the single, authoritative, exclusively-owned link between a proof and
    # its one governed SELL package (set once by link_controlled_proof_
    # sell_package, cleared only by supersede_stale_exit_recovery_sell_
    # package, which also clears the old package's presence here entirely).
    # A package's own persisted market_evidence_identity.controlled_proof_
    # exit_recovery_id stamp must NOT additionally be required to equal
    # this exact recovery's id: authorize_controlled_proof_exit_recovery's
    # allow_existing_sell_package contract explicitly permits a fresh
    # recovery to resume a SELL package that predates it entirely (stamp is
    # None -- created before the proof ever expired) or that was stamped
    # under an earlier, now-terminal recovery attempt for this exact same
    # proof ("the later authority may resume only that package" --
    # docs/CONTROLLED_PROOF_ACTIVATION.md). Requiring an exact stamp match
    # here (added in 0a167eb) silently broke that documented resume path --
    # confirmed production defect: an authorized, claimed Exit Recovery for
    # a proof with a pre-existing linked SELL package reached
    # controlled_proof_not_active on every retry, forever.
    exit_recovery_authorized = bool(
        recovery is not None
        and package.side == "SELL"
        and proof.sell_package_id == package.package_id
    )
    # Fail closed if the proof is expired, cancelled, blocked, failed, or
    # otherwise terminal -- _ACTIVE_STATES is the exact same set
    # create_controlled_proof's own "already active" guard and the
    # database's uq_controlled_proof_runs_single_active partial index are
    # built on, never a second, possibly-divergent definition of "active".
    if proof.status not in _CONTROLLED_PROOF_ACTIVE_STATES and not exit_recovery_authorized:
        return _blocked("controlled_proof_not_active")
    # Postgres's TIMESTAMPTZ always round-trips timezone-aware; sqlite (used
    # only by this module's own test double) has no native tz-aware type and
    # can hand back a naive value after a flush-triggered reload -- normalize
    # rather than let that test-environment quirk raise TypeError here.
    authority_expires_at = recovery.expires_at if exit_recovery_authorized else proof.expires_at
    expires_at = authority_expires_at if authority_expires_at.tzinfo is not None else authority_expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return _blocked("controlled_proof_expired")
    if package.product != proof.product_id:
        return _blocked("controlled_proof_product_mismatch")
    if package.campaign_id != proof.campaign_id or package.campaign_version != proof.campaign_version:
        return _blocked("controlled_proof_campaign_scope_mismatch")
    if package.decision_record_id != request.decision_record_id:
        return _blocked("controlled_proof_decision_record_mismatch")
    if (
        package.decision_record_id is None
        or package.risk_event_id is None
        or package.mandate_id is None
        or package.mandate_version_id is None
        or package.mandate_evaluation_id is None
    ):
        return _blocked("controlled_proof_evidence_incomplete")
    if package.risk_approved_amount > proof.max_notional_usd:
        return _blocked("controlled_proof_notional_exceeds_maximum")
    if package.provider != proof.provider or package.environment != proof.environment:
        return _blocked("controlled_proof_provider_environment_mismatch")

    # Duplicate-submission guard for a package this proof has not yet
    # activated: if the proof already shows live-capital evidence on the
    # matching side, this is not a legitimate first activation attempt --
    # an already-ACTIVATED package instead takes the existing idempotent-
    # replay branch below (package.package_state == "ACTIVATED"), which
    # re-validates authority rather than re-running this check.
    if package.package_state != "ACTIVATED":
        is_buy_package = proof.package_id == package.package_id
        is_sell_package = proof.sell_package_id == package.package_id
        if is_buy_package and (proof.buy_live_crypto_order_id is not None or proof.position_id is not None):
            return _blocked("controlled_proof_live_capital_already_exists")
        if is_sell_package:
            await repair_controlled_proof_cached_order_ids(db=db, proof=proof)
            sell_lineage = await resolve_controlled_proof_leg_execution_lineage(
                db=db, proof=proof, package_id=proof.sell_package_id, side="SELL",
            )
            if sell_lineage.state != "PACKAGE_ONLY":
                return _blocked(
                    "controlled_proof_sell_execution_lineage_exists"
                    if sell_lineage.state in {"CLAIM_ONLY", "ORDER_LINKED"}
                    else "controlled_proof_sell_execution_lineage_inconsistent"
                )

    logger.info(
        "controlled_proof_activation_override_allowed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s controlled_proof_id=%s product=%s provider=%s environment=%s",
        request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id,
        proof.proof_id, proof.product_id, proof.provider, proof.environment,
    )
    scope = ResolvedAutomaticActivationScope(
        package_id=package.package_id,
        campaign_id=package.campaign_id,
        campaign_version=package.campaign_version,
        mandate_id=package.mandate_id,
        mandate_version_id=package.mandate_version_id,
        mandate_evaluation_id=package.mandate_evaluation_id,
        authority_mode=_AUTHORITY_MODE_CONTROLLED_PROOF_DERIVED,
        controlled_proof_id=proof.proof_id,
    )
    logger.info(
        "automatic_activation_scope_resolved authority_mode=%s controlled_proof_id=%s package_id=%s campaign_id=%s campaign_version=%s mandate_id=%s mandate_version_id=%s mandate_evaluation_id=%s",
        scope.authority_mode, scope.controlled_proof_id, scope.package_id, scope.campaign_id,
        scope.campaign_version, scope.mandate_id, scope.mandate_version_id, scope.mandate_evaluation_id,
    )
    return scope


async def execute_automatic_ready_package_through_activation(
    *,
    db: AsyncSession,
    request: AutomaticPackageExecutionRequest,
) -> AutomaticPackageExecutionOutcome:
    settings = get_settings()
    # Resolved unconditionally -- never gated on automatic_mandate_package_
    # activation_enabled. A Controlled Proof's own authority must never
    # depend on whether that unrelated global switch happens to be on
    # (ordinary autonomous production) or off; gating the attempt on it was
    # the confirmed production defect: with the switch on (the documented,
    # supported configuration for ordinary autonomy -- see
    # AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_RUNBOOK.md) and the legacy global
    # selector settings pinned to the ordinary production mandate, an
    # authorized Controlled Proof Exit Recovery's SELL package -- which is
    # deliberately authorized under the separate controlled_proof_mandate_id
    # (config.py) -- fell straight through to GLOBAL_CONFIGURED_SCOPE and
    # was checked against the wrong mandate, failing closed on
    # automatic_activation_mandate_scope_mismatch despite valid, authorized
    # Controlled Proof authority. Only the *fallback* below (when this
    # returns None) still depends on the flag, exactly as before.
    controlled_proof_scope: ResolvedAutomaticActivationScope | None = None
    try:
        controlled_proof_scope = await _resolve_controlled_proof_activation_scope(db=db, request=request)
    except Exception:
        # An unexpected failure while resolving Controlled Proof authority
        # must fail closed exactly like "no Controlled Proof authority
        # applies" -- never surface as a different, more permissive
        # outcome, and never as an unhandled crash either.
        logger.exception(
            "controlled_proof_activation_override_evaluation_failed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
        )
        controlled_proof_scope = None
    if controlled_proof_scope is None and not settings.automatic_mandate_package_activation_enabled:
        logger.info(
            "automatic_package_progression_skipped campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s reason=feature_disabled failed_closed=False",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
        )
        return _outcome(request=request, package=None, reason="automatic_mandate_package_activation_disabled")

    mandate_scope: tuple[uuid.UUID, uuid.UUID] | None
    if controlled_proof_scope is not None:
        # CONTROLLED_PROOF_DERIVED_SCOPE: package identity and mandate scope
        # come exclusively from the already-resolved, persisted scope --
        # never from global settings. No global selector (including any
        # pinned automatic_mandate_package_activation_package_id) can
        # redirect a Controlled-Proof-authorized package to a different
        # package or mandate, and no statically configured package ID is
        # required -- a fresh package_id is derived per Controlled Proof.
        resolved_package_id: uuid.UUID | None = controlled_proof_scope.package_id
        mandate_scope = (controlled_proof_scope.mandate_id, controlled_proof_scope.mandate_version_id)
    else:
        scope_values = {
            "campaign_id": getattr(settings, "automatic_mandate_package_activation_campaign_id", None),
            "campaign_version": getattr(settings, "automatic_mandate_package_activation_campaign_version", None),
            "mandate_id": getattr(settings, "automatic_mandate_package_activation_mandate_id", None),
            "mandate_version_id": getattr(settings, "automatic_mandate_package_activation_mandate_version_id", None),
        }
        configured_scope = [value is not None for value in scope_values.values()]
        if any(configured_scope) and not all(configured_scope):
            logger.warning(
                "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s "
                "package_id=%s reason=automatic_activation_scope_incomplete failed_closed=True",
                request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
            )
            return _outcome(request=request, package=None, reason="automatic_activation_scope_incomplete", failed_closed=True)
        if all(configured_scope) and (
            request.campaign_id != scope_values["campaign_id"]
            or request.campaign_version != scope_values["campaign_version"]
        ):
            logger.warning(
                "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s "
                "package_id=%s reason=automatic_activation_campaign_scope_mismatch "
                "configured_campaign_id=%s configured_campaign_version=%s failed_closed=True",
                request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
                scope_values["campaign_id"], scope_values["campaign_version"],
            )
            return _outcome(request=request, package=None, reason="automatic_activation_campaign_scope_mismatch", failed_closed=True)

        pinned_package_id = getattr(settings, "automatic_mandate_package_activation_package_id", None)
        if pinned_package_id is not None and request.package_id not in {None, pinned_package_id}:
            logger.warning(
                "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s pinned_package_id=%s reason=proof_package_pin_mismatch failed_closed=True",
                request.campaign_id, request.campaign_version, request.decision_record_id,
                request.package_id, pinned_package_id,
            )
            return _outcome(
                request=request,
                package=None,
                reason="proof_package_pin_mismatch",
                failed_closed=True,
            )

        resolved_package_id = request.package_id or pinned_package_id
        mandate_scope = (scope_values["mandate_id"], scope_values["mandate_version_id"]) if all(configured_scope) else None
        if all(configured_scope):
            logger.info(
                "automatic_activation_scope_resolved authority_mode=%s controlled_proof_id=None package_id=%s campaign_id=%s campaign_version=%s mandate_id=%s mandate_version_id=%s mandate_evaluation_id=None",
                _AUTHORITY_MODE_GLOBAL_CONFIGURED, resolved_package_id, scope_values["campaign_id"],
                scope_values["campaign_version"], scope_values["mandate_id"], scope_values["mandate_version_id"],
            )

    statement = select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.campaign_id == request.campaign_id,
        CanonicalPreviewPackage.campaign_version == request.campaign_version,
        CanonicalPreviewPackage.decision_record_id == request.decision_record_id,
        CanonicalPreviewPackage.package_state.in_(_PROGRESSABLE_STATES),
    )
    if resolved_package_id is not None:
        statement = statement.where(CanonicalPreviewPackage.package_id == resolved_package_id)
    rows = list((await db.execute(statement.order_by(CanonicalPreviewPackage.generated_at.desc()).limit(2).with_for_update())).scalars().all())
    if len(rows) != 1:
        reason = "eligible_package_missing" if not rows else "ambiguous_eligible_packages"
        logger.warning(
            "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s reason=%s package_count=%s failed_closed=True",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id, reason, len(rows),
        )
        return _outcome(request=request, package=None, reason=reason, failed_closed=True)
    package = rows[0]
    starting_state = package.package_state

    if mandate_scope is not None and (package.mandate_id, package.mandate_version_id) != mandate_scope:
        logger.warning(
            "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s "
            "package_id=%s reason=automatic_activation_mandate_scope_mismatch "
            "package_mandate_id=%s package_mandate_version_id=%s expected_mandate_id=%s expected_mandate_version_id=%s "
            "failed_closed=True",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
            package.mandate_id, package.mandate_version_id, mandate_scope[0], mandate_scope[1],
        )
        return _outcome(
            request=request, package=package, reason="automatic_activation_mandate_scope_mismatch",
            failed_closed=True, starting_state=starting_state,
        )

    try:
        if (
            package.campaign_id != request.campaign_id
            or package.campaign_version != request.campaign_version
            or package.decision_record_id != request.decision_record_id
            or (resolved_package_id is not None and package.package_id != resolved_package_id)
        ):
            raise PermissionError("resolved package identity mismatch")
        if package.package_state == "ACTIVATED":
            if package.authorization_source != "MANDATE":
                raise PermissionError("activated package has conflicting authority source")
            await _validate_canonical_package_authority(db=db, package=package, requested_approval_event_id=None)
            logger.info(
                "automatic_package_activated campaign_id=%s campaign_version=%s product_id=%s decision_record_id=%s package_id=%s mandate_id=%s replayed=True",
                request.campaign_id, request.campaign_version, package.product, request.decision_record_id, package.package_id, package.mandate_id,
            )
            return _outcome(request=request, package=package, reason="already_activated", replayed=True, starting_state=starting_state)

        if package.package_state == "READY":
            logger.info(
                "automatic_package_authorization_started campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id,
            )
            await authorize_canonical_preview_package_under_mandate(
                db=db,
                request=CanonicalPreviewPackageMandateAuthorizeRequest(
                    package_id=package.package_id,
                    idempotency_key=_phase_key(package_id=package.package_id, phase="authorize"),
                    software_build_version=request.software_build_version,
                    # CONTROLLED_PROOF_DERIVED_SCOPE already resolved and
                    # persisted the exact mandate this package must be
                    # authorized under (see _resolve_controlled_proof_
                    # activation_scope) -- constrain selection to it instead
                    # of the general ACTIVE/LEVEL_2/scope search, which a
                    # concurrently ACTIVE ordinary-production mandate for the
                    # identical provider/environment/connection/profile/
                    # paper_account/campaign can otherwise make ambiguous.
                    # None for every other (ordinary automatic) caller,
                    # leaving that search entirely unchanged.
                    expected_mandate_id=(
                        controlled_proof_scope.mandate_id if controlled_proof_scope is not None else None
                    ),
                    expected_mandate_purpose=(
                        MANDATE_PURPOSE_CONTROLLED_PROOF if controlled_proof_scope is not None else None
                    ),
                ),
            )
            logger.info(
                "automatic_package_authorized_under_mandate campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, package.mandate_id,
            )

        if package.package_state == "AUTHORIZED":
            if package.authorization_source != "MANDATE":
                raise PermissionError("authorized package has conflicting authority source")
            await run_dry_run_for_canonical_preview_package(
                db=db,
                request=CanonicalPreviewPackageDryRunRequest(
                    package_id=package.package_id,
                    approval_event_id=None,
                    operator_identity=None,
                    idempotency_token=_phase_key(package_id=package.package_id, phase="dry-run"),
                ),
            )
            logger.info(
                "automatic_package_dry_run_passed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, package.mandate_id,
            )

        if package.package_state == "DRY_RUN_PASSED":
            if package.authorization_source != "MANDATE" or package.dry_run_live_crypto_order_id is None:
                raise PermissionError("dry-run package authority evidence is incomplete")
            await activate_canonical_proving_campaign(
                db=db,
                request=CanonicalPreviewPackageActivationRequest(
                    package_id=package.package_id,
                    approval_event_id=None,
                    dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
                    actor=None,
                    expires_at=None,
                    idempotency_key=_phase_key(package_id=package.package_id, phase="activate"),
                ),
            )
            logger.info(
                "automatic_package_activated campaign_id=%s campaign_version=%s product_id=%s decision_record_id=%s package_id=%s mandate_id=%s replayed=False",
                request.campaign_id, request.campaign_version, package.product, request.decision_record_id, package.package_id, package.mandate_id,
            )

        if package.package_state != "ACTIVATED":
            raise PermissionError(f"automatic package progression stopped in unexpected state: {package.package_state}")
        return _outcome(request=request, package=package, reason="activated_under_mandate", starting_state=starting_state)
    except (LookupError, PermissionError, ValueError) as exc:
        logger.warning(
            "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s state=%s reason=%s failed_closed=True",
            request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id,
            package.mandate_id, package.package_state, str(exc),
        )
        return _outcome(request=request, package=package, reason=str(exc), failed_closed=True, starting_state=starting_state)
