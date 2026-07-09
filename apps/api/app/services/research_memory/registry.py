from __future__ import annotations

from app.services.research_memory.service import ResearchMemory


_RESEARCH_MEMORY = ResearchMemory()


def get_research_memory() -> ResearchMemory:
    return _RESEARCH_MEMORY
