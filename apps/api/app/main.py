import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.arena import router as arena_router
from app.api.routes.autonomous_capital_mandates import router as autonomous_capital_mandates_router
from app.api.routes.backtests import router as backtests_router
from app.api.routes.crypto_order_previews import router as crypto_order_previews_router
from app.api.routes.capital import router as capital_router
from app.api.routes.capital_campaigns import router as capital_campaigns_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.exchange_connections import router as exchange_connections_router
from app.api.routes.instant_trades import router as instant_trades_router
from app.api.routes.live_crypto_orders import router as live_crypto_orders_router
from app.api.routes.health import router as health_router
from app.api.routes.live import router as live_router
from app.api.routes.mission_control import router as mission_control_router
from app.api.routes.markets import router as markets_router
from app.api.routes.operations import router as operations_router
from app.api.routes.parameter_sets import router as parameter_sets_router
from app.api.routes.paper import router as paper_router
from app.api.routes.research import router as research_router
from app.api.routes.risk import router as risk_router
from app.api.routes.strategies import router as strategies_router
from app.api.routes.validation_runs import router as validation_runs_router
from app.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.services.research_persistence import ResearchPersistenceRepository, flush_legacy_research_state


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()

    app = FastAPI(title="OmniTrade Legacy Engine API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(markets_router)
    app.include_router(operations_router)
    app.include_router(strategies_router)
    app.include_router(parameter_sets_router)
    app.include_router(backtests_router)
    app.include_router(crypto_order_previews_router)
    app.include_router(capital_router)
    app.include_router(capital_campaigns_router)
    app.include_router(exchange_connections_router)
    app.include_router(dashboard_router)
    app.include_router(mission_control_router)
    app.include_router(arena_router)
    app.include_router(autonomous_capital_mandates_router)
    app.include_router(research_router)
    app.include_router(paper_router)
    app.include_router(risk_router)
    app.include_router(decisions_router)
    app.include_router(live_router)
    app.include_router(live_crypto_orders_router)
    app.include_router(instant_trades_router)
    app.include_router(validation_runs_router)

    @app.on_event("startup")
    async def _flush_legacy_research_state() -> None:
        repository = ResearchPersistenceRepository()
        try:
            async with AsyncSessionLocal() as session:
                flushed = await flush_legacy_research_state(db=session, repository=repository)
                if flushed:
                    await session.commit()
        except Exception:
            # Keep startup resilient in environments without a reachable DB.
            logger.warning("Skipping legacy research state flush at startup", exc_info=True)

    return app


app = create_app()
