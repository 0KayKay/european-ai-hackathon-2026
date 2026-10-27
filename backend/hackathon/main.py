from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from hackathon.api.router import router as ag_ui_router
from hackathon.api.challenges import router as challenges_router
from hackathon.arena.api import get_level_catalog, router as arena_router
from hackathon.config import get_settings
from hackathon.extensions import (
    LogCorrelationIdMiddleware,
    configure_logging,
    global_exception_handler,
    normalize_external_loggers,
)
from hackathon.extensions.tracing import configure_tracing

logging_config = configure_logging()
logger = structlog.get_logger("main")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    normalize_external_loggers()
    logger.info("Starting application", app=settings.APP_NAME, environment=settings.ENV)
    # Fail fast on an invalid level config (R-CFG-5) instead of on the first arena request.
    get_level_catalog()
    yield None


app = FastAPI(title=get_settings().APP_NAME, lifespan=lifespan)
app.add_middleware(LogCorrelationIdMiddleware)
app.add_exception_handler(Exception, global_exception_handler)
app.include_router(ag_ui_router)
app.include_router(challenges_router)
app.include_router(arena_router)
if settings.PHOENIX_OTLP_ENDPOINT:
    configure_tracing(
        app, settings.PHOENIX_OTLP_ENDPOINT, api_key=settings.PHOENIX_API_KEY
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main(reload: bool | None = None) -> None:
    """Use uvicorn to run fastapi app.

    Run with "reload" enabled in local mode, unless explicitly set to False.
    """
    reload = reload if reload is not None else get_settings().is_local
    uvicorn.run(
        "hackathon.main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        log_config=logging_config,
    )


if __name__ == "__main__":
    """Run app"""
    main()
