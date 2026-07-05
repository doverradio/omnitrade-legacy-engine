from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.models.backtest import Backtest
from app.models.backtest_trade import BacktestTrade
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy


def test_strategy_model_matches_documented_uniqueness_and_defaults() -> None:
    unique_constraints = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in Strategy.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints["uq_strategies_slug"] == ("slug",)
    assert str(Strategy.__table__.c.is_active.server_default.arg) == "false"


def test_parameter_set_model_uses_jsonb_params() -> None:
    assert isinstance(ParameterSet.__table__.c.params.type, JSONB)


def test_backtest_model_matches_documented_constraints_and_jsonb_fields() -> None:
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Backtest.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_constraints["ck_backtests_initial_capital_min"] == "initial_capital >= 25"
    assert "pending" in check_constraints["ck_backtests_status"]
    assert "failed" in check_constraints["ck_backtests_status"]
    assert isinstance(Backtest.__table__.c.metrics.type, JSONB)
    assert isinstance(Backtest.__table__.c.small_account_warning.type, JSONB)


def test_backtest_trade_model_matches_documented_side_constraint() -> None:
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in BacktestTrade.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_constraints["ck_backtest_trades_side"] == "side IN ('buy','sell')"