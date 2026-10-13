from contextlib import asynccontextmanager

from fastapi import FastAPI

from hackathon_deepteam.api.runs import router
from hackathon_deepteam.extensions import (
    LogCorrelationIdMiddleware,
    configure_logging,
    global_exception_handler,
    normalize_external_loggers,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    normalize_external_loggers()
    yield


app = FastAPI(title="DeepTeam", lifespan=lifespan)
app.add_middleware(LogCorrelationIdMiddleware)
app.add_exception_handler(Exception, global_exception_handler)
app.include_router(router)
