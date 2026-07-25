from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.base import Base
from app.db import session as production_session
from app.services.historical_simulation import persistence
from app.services.historical_simulation.persistence import SimulationBase, SimulationConfigurationError, get_simulation_engine
from app.services.historical_simulation.isolation import SimulationIsolationViolation

_PRODUCTION_URL = "postgresql+asyncpg://produser:prodsecret@prod-host:5432/omnitrade"
_SIMULATION_URL = "postgresql+asyncpg://simuser:simsecret@sim-host:5433/omnitrade_sim"


@pytest.fixture(autouse=True)
def _reset_caches():
    get_settings.cache_clear()
    get_simulation_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_simulation_engine.cache_clear()


def _set_urls(monkeypatch: pytest.MonkeyPatch, *, database_url: str, simulation_url: str | None) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    if simulation_url is None:
        monkeypatch.delenv("OT_SIMULATION_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("OT_SIMULATION_DATABASE_URL", simulation_url)
    get_settings.cache_clear()
    get_simulation_engine.cache_clear()


def test_production_and_simulation_metadata_share_zero_table_names() -> None:
    production_tables = set(Base.metadata.tables.keys())
    simulation_tables = set(SimulationBase.metadata.tables.keys())
    assert production_tables, "production Base must have real tables for this test to be meaningful"
    assert production_tables.isdisjoint(simulation_tables)


def test_simulation_base_is_a_distinct_declarative_base_from_production() -> None:
    assert SimulationBase is not Base
    assert SimulationBase.metadata is not Base.metadata


def test_production_session_module_is_unaffected_by_simulation_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=_SIMULATION_URL)
    # Production's own engine/sessionmaker are constructed once at import
    # time against the settings available then; this test only asserts the
    # production module's *contract* -- reading settings.database_url,
    # never reading simulation config -- not that its already-constructed
    # engine picks up an env change after import.
    assert production_session.AsyncSessionLocal is not None
    assert get_settings().database_url == _PRODUCTION_URL


def test_simulation_engine_binds_to_ot_simulation_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=_SIMULATION_URL)
    engine = get_simulation_engine()
    expected = make_url(_SIMULATION_URL)
    assert engine.url.host == expected.host
    assert engine.url.port == expected.port
    assert engine.url.database == expected.database


def test_simulation_engine_raises_when_url_is_unset_with_no_fallback_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=None)
    with pytest.raises(SimulationConfigurationError):
        get_simulation_engine()


def test_simulation_engine_raises_when_url_equals_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=_PRODUCTION_URL)
    with pytest.raises(SimulationIsolationViolation):
        get_simulation_engine()


def test_simulation_engine_raises_when_url_is_equivalent_to_production_under_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    equivalent = "postgresql://different_user:different_pass@prod-host:5432/omnitrade"
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=equivalent)
    with pytest.raises(SimulationIsolationViolation):
        get_simulation_engine()


def test_get_simulation_engine_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=_SIMULATION_URL)
    assert get_simulation_engine() is get_simulation_engine()


def test_no_real_connection_is_attempted_by_importing_or_constructing_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # Constructing the engine object must be pure/local -- create_async_engine
    # does not itself open a socket. If this test ever needs network access
    # to pass, that itself is the regression.
    _set_urls(monkeypatch, database_url=_PRODUCTION_URL, simulation_url=_SIMULATION_URL)
    assert persistence.get_simulation_engine() is not None
