from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.strategies.base import Signal


def test_signal_validation_accepts_valid_signal() -> None:
    signal = Signal(
        action="buy",
        strength=Decimal("0.75"),
        reason="Fast MA is above slow MA.",
        indicators={"fast_ma": Decimal("12"), "slow_ma": Decimal("10")},
        timestamp=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    assert signal.action == "buy"


@pytest.mark.parametrize("strength", [Decimal("-0.01"), Decimal("1.01")])
def test_signal_validation_rejects_out_of_range_strength(strength: Decimal) -> None:
    with pytest.raises(ValidationError):
        Signal(
            action="buy",
            strength=strength,
            reason="Invalid strength.",
            indicators={"value": 1},
            timestamp=datetime(2026, 7, 5, tzinfo=timezone.utc),
        )


def test_signal_validation_rejects_blank_reason() -> None:
    with pytest.raises(ValidationError):
        Signal(
            action="hold",
            strength=Decimal("0.0"),
            reason="   ",
            indicators={"value": 1},
            timestamp=datetime(2026, 7, 5, tzinfo=timezone.utc),
        )