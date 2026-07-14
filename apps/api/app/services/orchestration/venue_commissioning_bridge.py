from __future__ import annotations

from typing import Any


async def resume_runs(*, db: Any, actor: str, limit: int = 10) -> int:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    return int(
        await venue_commissioning_service["resume_runs"](
            db=db,
            actor=actor,
            limit=limit,
        )
    )


service = {
    "resume_runs": resume_runs,
}
