from __future__ import annotations

from sqlalchemy.engine import make_url

from app.services.historical_simulation.run_mode import RunMode

_ISOLATION_REQUIRED_MODES = {RunMode.HISTORICAL_SIMULATION, RunMode.COUNTERFACTUAL}


class SimulationIsolationViolation(RuntimeError):
    """Raised when a historical/counterfactual run would compromise
    production isolation. Always fail-closed: callers must invoke
    IsolationGuard.verify_or_die before binding a session or provider,
    never treat its absence-of-exception as anything but "safe so far"."""


def _redact(url: str) -> str:
    """Credential-safe rendering for use inside error messages: dialect,
    host, port, and database name only. Never username or password --
    render_as_string(hide_password=True) still exposes the username, which
    is why this builds its own string from the normalized tuple rather
    than delegating to SQLAlchemy's renderer. On any parse failure, no
    fragment of the input is echoed back at all."""
    try:
        dialect, host, port, database = _normalized_target(url)
    except Exception:
        return "<unparseable-database-url>"
    netloc = host or "<unknown-host>"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{dialect}://{netloc}/{database or '<unknown-database>'}"


def _normalized_target(url: str) -> tuple[str, str | None, int | None, str | None]:
    """(dialect, host, port, database name), ignoring driver suffix
    (postgresql+asyncpg vs postgresql) and credentials -- two URLs that
    differ only in those respects still point at the same database."""
    parsed = make_url(url)
    return (parsed.get_backend_name(), parsed.host, parsed.port, parsed.database)


def _is_live_provider(provider_name: str) -> bool:
    # Imported locally to avoid this scaffolding module creating an
    # import-time dependency on the production exchange-provider registry;
    # reuses that registry as the single source of truth for "live" rather
    # than maintaining a second, competing list of provider names here.
    from app.services.exchange_connections.providers.registry import get_exchange_provider_metadata

    try:
        get_exchange_provider_metadata(provider_name)
        return True
    except Exception:
        return False


class IsolationGuard:
    """Fail-closed boundary between production and historical/counterfactual
    runs. Stateless: every check is a pure function of its arguments."""

    @staticmethod
    def verify_or_die(
        *,
        run_mode: RunMode,
        database_url: str | None,
        production_database_url: str,
        provider_name: str | None = None,
    ) -> None:
        if run_mode not in _ISOLATION_REQUIRED_MODES:
            return

        if not database_url or not database_url.strip():
            raise SimulationIsolationViolation(
                f"{run_mode.value} requires an explicit, isolated simulation database "
                "target; none was configured (no fallback to the production database "
                "is permitted)."
            )

        try:
            simulation_target = _normalized_target(database_url)
            production_target = _normalized_target(production_database_url)
        except Exception:
            raise SimulationIsolationViolation(
                f"{run_mode.value} simulation database configuration is ambiguous or "
                "unparseable."
            ) from None

        if simulation_target == production_target:
            raise SimulationIsolationViolation(
                f"{run_mode.value} database target resolves to the same database as "
                f"production ({_redact(database_url)}); a historical/counterfactual run "
                "must use a distinct, isolated database."
            )

        if provider_name is not None and _is_live_provider(provider_name):
            raise SimulationIsolationViolation(
                f"{run_mode.value} may not bind to live execution provider "
                f"'{provider_name}'."
            )
