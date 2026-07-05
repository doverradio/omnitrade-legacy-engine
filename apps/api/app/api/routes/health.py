from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.data.ingestion_status import get_last_successful_ingestion_at

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

    last_ingestion_at = get_last_successful_ingestion_at()

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "db": "connected",
            "last_ingestion_at": (
                last_ingestion_at.isoformat().replace("+00:00", "Z") if last_ingestion_at else None
            ),
        },
    )
