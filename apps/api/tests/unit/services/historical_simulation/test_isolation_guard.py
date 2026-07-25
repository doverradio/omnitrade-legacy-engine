from __future__ import annotations

import pytest

from app.services.historical_simulation.isolation import IsolationGuard, SimulationIsolationViolation
from app.services.historical_simulation.run_mode import RunMode

_PRODUCTION_URL = "postgresql+asyncpg://produser:prodsecret@prod-host:5432/omnitrade"
_SIMULATION_URL = "postgresql+asyncpg://simuser:simsecret@sim-host:5433/omnitrade_sim"


def test_production_live_mode_is_never_restricted_even_with_no_simulation_url() -> None:
    IsolationGuard.verify_or_die(
        run_mode=RunMode.PRODUCTION_LIVE, database_url=None, production_database_url=_PRODUCTION_URL,
    )


def test_forward_paper_mode_is_never_restricted() -> None:
    IsolationGuard.verify_or_die(
        run_mode=RunMode.FORWARD_PAPER, database_url=None, production_database_url=_PRODUCTION_URL,
    )


def test_unit_test_mode_is_never_restricted() -> None:
    IsolationGuard.verify_or_die(
        run_mode=RunMode.UNIT_TEST, database_url=None, production_database_url=_PRODUCTION_URL,
    )


@pytest.mark.parametrize("mode", [RunMode.HISTORICAL_SIMULATION, RunMode.COUNTERFACTUAL])
def test_historical_and_counterfactual_modes_pass_with_a_distinct_database(mode: RunMode) -> None:
    IsolationGuard.verify_or_die(
        run_mode=mode, database_url=_SIMULATION_URL, production_database_url=_PRODUCTION_URL,
    )


@pytest.mark.parametrize("mode", [RunMode.HISTORICAL_SIMULATION, RunMode.COUNTERFACTUAL])
def test_historical_and_counterfactual_modes_reject_missing_database_url(mode: RunMode) -> None:
    with pytest.raises(SimulationIsolationViolation):
        IsolationGuard.verify_or_die(run_mode=mode, database_url=None, production_database_url=_PRODUCTION_URL)

    with pytest.raises(SimulationIsolationViolation):
        IsolationGuard.verify_or_die(run_mode=mode, database_url="   ", production_database_url=_PRODUCTION_URL)


@pytest.mark.parametrize("mode", [RunMode.HISTORICAL_SIMULATION, RunMode.COUNTERFACTUAL])
def test_historical_and_counterfactual_modes_reject_the_exact_production_url(mode: RunMode) -> None:
    with pytest.raises(SimulationIsolationViolation):
        IsolationGuard.verify_or_die(run_mode=mode, database_url=_PRODUCTION_URL, production_database_url=_PRODUCTION_URL)


def test_equivalent_targets_are_rejected_despite_different_driver_and_credentials() -> None:
    # Same host/port/database as production, only the driver suffix and
    # credentials differ -- normalization must still catch this.
    equivalent = "postgresql://different_user:different_pass@prod-host:5432/omnitrade"
    with pytest.raises(SimulationIsolationViolation):
        IsolationGuard.verify_or_die(
            run_mode=RunMode.HISTORICAL_SIMULATION, database_url=equivalent, production_database_url=_PRODUCTION_URL,
        )


def test_different_database_name_on_same_host_is_not_treated_as_equivalent() -> None:
    distinct_db_same_host = "postgresql+asyncpg://simuser:simsecret@prod-host:5432/omnitrade_sim"
    IsolationGuard.verify_or_die(
        run_mode=RunMode.HISTORICAL_SIMULATION,
        database_url=distinct_db_same_host,
        production_database_url=_PRODUCTION_URL,
    )


def test_ambiguous_unparseable_simulation_url_is_rejected() -> None:
    with pytest.raises(SimulationIsolationViolation):
        IsolationGuard.verify_or_die(
            run_mode=RunMode.HISTORICAL_SIMULATION,
            database_url="not-a-valid-database-url",
            production_database_url=_PRODUCTION_URL,
        )


@pytest.mark.parametrize("mode", [RunMode.HISTORICAL_SIMULATION, RunMode.COUNTERFACTUAL])
def test_historical_and_counterfactual_modes_reject_a_live_provider(mode: RunMode) -> None:
    with pytest.raises(SimulationIsolationViolation):
        IsolationGuard.verify_or_die(
            run_mode=mode,
            database_url=_SIMULATION_URL,
            production_database_url=_PRODUCTION_URL,
            provider_name="kraken_spot",
        )


def test_historical_mode_accepts_an_unrecognized_non_live_provider_name() -> None:
    IsolationGuard.verify_or_die(
        run_mode=RunMode.HISTORICAL_SIMULATION,
        database_url=_SIMULATION_URL,
        production_database_url=_PRODUCTION_URL,
        provider_name="synthetic_broker",
    )


def test_error_messages_never_disclose_credentials() -> None:
    with pytest.raises(SimulationIsolationViolation) as excinfo:
        IsolationGuard.verify_or_die(
            run_mode=RunMode.HISTORICAL_SIMULATION, database_url=_PRODUCTION_URL, production_database_url=_PRODUCTION_URL,
        )
    message = str(excinfo.value)
    assert "prodsecret" not in message
    assert "produser" not in message
