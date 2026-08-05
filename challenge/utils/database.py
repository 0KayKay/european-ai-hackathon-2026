from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg
import structlog
from asyncpg import Connection
from google.cloud.sql.connector import create_async_connector
from google.cloud.sql.connector.enums import IPTypes
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession

from challenge.config import get_settings


def get_asyncpg_connection() -> Callable[[Any], Coroutine[Any, Any, Connection]] | None:
    """Get a connection to the Cloud SQL instance using the Cloud SQL Python Connector if necessary."""
    settings = get_settings()
    if not settings.CLOUD_SQL_CONNECTION_NAME:
        return None

    async def get_connection(*args: object, **kwargs: object) -> asyncpg.Connection:
        connector = await create_async_connector()
        return await connector.connect_async(
            instance_connection_string=cast(str, settings.CLOUD_SQL_CONNECTION_NAME),
            driver=settings.DB_DRIVER,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            db=settings.DB_NAME,
            ip_type=IPTypes.PRIVATE,
        )

    return get_connection


db_url = get_settings().database_url(include_driver=True)
engine = create_async_engine(
    db_url, pool_pre_ping=True, async_creator=get_asyncpg_connection()
)
SessionLocal = async_sessionmaker(engine)
logger = structlog.get_logger("db_session")


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependency function that yields a new SQLAlchemy session for each request."""
    async with SessionLocal() as db, db.begin():
        try:
            yield db
        except SQLAlchemyError as e:
            logger.exception(f"Rolling back transaction due to {e}")
            await db.rollback()
            raise
        finally:
            await db.close()
