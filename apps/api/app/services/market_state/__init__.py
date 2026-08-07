"""Canonical deterministic Market State Classifier — research/replay layer only.

See `docs/MARKET_STATE_CLASSIFIER.md` and
`docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`.

This package must never be imported from a live strategy, risk,
mandate/campaign, or order-execution path. It is not consumed by
`app.services.strategy_roster`, `app.services.autonomous_cycle`,
`app.services.capital_campaign_orchestration`, `app.services.risk`, or any
other production decision path, and must remain that way unless a future,
separate, explicitly-authorized change decides otherwise.
"""

from __future__ import annotations

from .contracts import (
    DirectionState,
    MarketState,
    MarketStateClassifierParams,
    OHLCVBar,
    ParticipationState,
    VolatilityState,
)
from .deterministic_classifier import (
    CLASSIFIER_VERSION,
    DEFAULT_PARAMS,
    InsufficientMarketDataError,
    classify_market_state,
)

__all__ = [
    "CLASSIFIER_VERSION",
    "DEFAULT_PARAMS",
    "DirectionState",
    "InsufficientMarketDataError",
    "MarketState",
    "MarketStateClassifierParams",
    "OHLCVBar",
    "ParticipationState",
    "VolatilityState",
    "classify_market_state",
]
