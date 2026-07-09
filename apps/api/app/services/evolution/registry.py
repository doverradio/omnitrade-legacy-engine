from __future__ import annotations

from app.services.evolution.service import EvolutionEngine


_EVOLUTION_ENGINE = EvolutionEngine()


def get_evolution_engine() -> EvolutionEngine:
    return _EVOLUTION_ENGINE
