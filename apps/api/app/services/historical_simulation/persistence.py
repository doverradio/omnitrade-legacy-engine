from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings
from app.services.historical_simulation.isolation import IsolationGuard
from app.services.historical_simulation.run_mode import RunMode


class SimulationBase(DeclarativeBase):
    """Metadata root for the simulation namespace. Deliberately a distinct
    DeclarativeBase from app.db.base.Base: no simulation model may ever be
    registered on the production Base, and no production model may ever be
    registered here (enforced by test, not just convention -- see
    tests/unit/services/historical_simulation/test_persistence_isolation.py).
    No table is bound to this metadata yet in this phase; it exists purely
    as the isolated root future simulation models will attach to."""


class SimulationConfigurationError(RuntimeError):
    """Raised when simulation persistence is requested but cannot be safely
    started -- missing configuration or an isolation violation. Always
    fail-closed: there is no code path that falls back to the production
    database_url."""


@lru_cache
def get_simulation_engine() -> AsyncEngine:
    """Lazily constructs the simulation engine on first use. Never called
    at import time (unlike app.db.session's production engine, which is
    constructed unconditionally at module load) specifically so that
    importing this module never requires OT_SIMULATION_DATABASE_URL to be
    set, and never opens a real connection during collection/import of
    unit tests. Cached like app.config.get_settings so tests can reset it
    via get_simulation_engine.cache_clear()."""
    settings = get_settings()
    url = settings.simulation_database_url
    if not url or not url.strip():
        raise SimulationConfigurationError(
            "OT_SIMULATION_DATABASE_URL is not configured; simulation persistence "
            "cannot start without an explicit, isolated database target."
        )
    IsolationGuard.verify_or_die(
        run_mode=RunMode.HISTORICAL_SIMULATION,
        database_url=url,
        production_database_url=settings.database_url,
    )
    return create_async_engine(url, future=True, pool_pre_ping=True)


def get_simulation_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_simulation_engine(), expire_on_commit=False, class_=AsyncSession)
