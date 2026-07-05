from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.backtests import router as backtests_router
from app.api.routes.health import router as health_router
from app.api.routes.markets import router as markets_router
from app.api.routes.parameter_sets import router as parameter_sets_router
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(title="OmniTrade Legacy Engine API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(markets_router)
    app.include_router(parameter_sets_router)
    app.include_router(backtests_router)

    return app


app = create_app()
