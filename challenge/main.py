import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi.templating import Jinja2Templates
from fastapi import FastAPI
from alembic import command
from starlette.staticfiles import StaticFiles

from challenge.auth.middleware import AuthMiddleware
from challenge.auth.router import router as auth_router
from challenge.routers.time_limit_middleware import TimeLimitMiddleware
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from starlette.middleware.cors import CORSMiddleware
from chainlit.utils import mount_chainlit
import nest_asyncio

from challenge.config import get_settings
from challenge.extensions import configure_logging, logger
from challenge.routers import pages, public_pages, admin

settings = get_settings()
nest_asyncio.apply()
templates = Jinja2Templates(directory="challenge/templates")

os.environ["CHAINLIT_AUTH_SECRET"] = (
    "iYV,c_RAe>qgaEXb_VCiXeL2w^hfdD,mxUn$sA2*Sd>eveXHofb@L^.OJ.ZY4XAE"
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context manager to run Alembic migrations on startup."""
    configure_logging()

    logger.info(f"Starting {settings.APP_NAME} in {settings.APP_ENV} environment")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", settings.database_url(include_driver=True)
    )
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    # Initialize PostgresSaver checkpointer for LangGraph
    async with AsyncPostgresSaver.from_conn_string(
        settings.database_url()
    ) as checkpointer:
        await checkpointer.setup()

    yield

    logger.info(f"Shutting down {settings.APP_NAME}")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TimeLimitMiddleware)
app.add_middleware(AuthMiddleware)

app.include_router(auth_router)
app.include_router(public_pages.router)
app.include_router(pages.router)
app.include_router(admin.router)

# publish static files
app.mount("/public", StaticFiles(directory="./public"), name="static")
mount_chainlit(
    app=app, target="challenge/chat/chat_langgraph.py", path=settings.CHAINLIT_ROOT_PATH
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("challenge.main:app", host="localhost", port=9000, reload=True)
