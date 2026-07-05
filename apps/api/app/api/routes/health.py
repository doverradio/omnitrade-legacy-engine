from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "db": "disconnected",
                "last_ingestion_at": None,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "db": "connected",
            "last_ingestion_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )
