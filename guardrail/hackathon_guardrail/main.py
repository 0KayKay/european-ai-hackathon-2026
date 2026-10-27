from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from hackathon_guardrail.api import router
from hackathon_guardrail.extensions import (
    LogCorrelationIdMiddleware,
    configure_logging,
    global_exception_handler,
    normalize_external_loggers,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    normalize_external_loggers()
    structlog.get_logger("main").info("Starting application", app="Guardrail")
    yield


app = FastAPI(title="Guardrail", lifespan=lifespan)
app.add_middleware(LogCorrelationIdMiddleware)
app.add_exception_handler(Exception, global_exception_handler)
app.include_router(router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
