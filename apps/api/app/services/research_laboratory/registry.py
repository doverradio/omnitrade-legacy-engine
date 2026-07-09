from __future__ import annotations

from app.services.research_laboratory.service import ResearchLaboratory


_LABORATORY = ResearchLaboratory()


def get_research_laboratory() -> ResearchLaboratory:
    return _LABORATORY
