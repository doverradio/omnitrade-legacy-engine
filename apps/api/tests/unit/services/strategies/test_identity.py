from __future__ import annotations

import pytest

from app.services.strategies.identity import build_strategy_identity, is_strategy_identity, parse_strategy_identity


def test_strategy_identity_is_deterministic() -> None:
    first = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    second = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")

    assert first == second == "ma_crossover@1.0.0"


def test_same_module_version_does_not_collide_across_strategies() -> None:
    first = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    second = build_strategy_identity(slug="rsi_mean_reversion", module_version="1.0.0")

    assert first != second


def test_same_slug_with_different_versions_remains_distinguishable() -> None:
    first = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    second = build_strategy_identity(slug="ma_crossover", module_version="1.0.1")

    assert first != second


@pytest.mark.parametrize("identity", ["", "1.0.0", "ma_crossover@", "@1.0.0", "ma_crossover@@1.0.0"])
def test_malformed_identity_is_rejected(identity: str) -> None:
    assert is_strategy_identity(identity) is False
    assert parse_strategy_identity(identity) is None
