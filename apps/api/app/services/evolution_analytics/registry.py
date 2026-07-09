from __future__ import annotations

from app.services.evolution_analytics.service import EvolutionAnalyticsService


_EVOLUTION_ANALYTICS_SERVICE = EvolutionAnalyticsService()


def get_evolution_analytics_service() -> EvolutionAnalyticsService:
    return _EVOLUTION_ANALYTICS_SERVICE
