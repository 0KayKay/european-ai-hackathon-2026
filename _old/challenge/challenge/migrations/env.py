import asyncio
import structlog
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from challenge.config import get_settings
from challenge.migrations.models.base import Base
from _old.challenge.challenge.utils.database import get_asyncpg_connection

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.

target_metadata = Base.metadata
settings = get_settings()
logger = structlog.get_logger("migrations")

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


# add an include_object function to exclude LangGraph specific tables from alembic context
def include_object(object, name, type_, reflected, compare_to):
    """
    Exclude LangGraph tables from autogenerate detection.
    """
    if type_ == "table":
        # Ignore LangGraph checkpoint tables
        if name in [
            'checkpoints',
            'checkpoint_blobs',
            'checkpoint_writes',
            'checkpoint_migrations'
        ]:
            return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        async_creator=get_asyncpg_connection(),
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    except Exception as e:
        logger.error(f"Failed to run migrations: {e}")

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    try:
        asyncio.run(run_async_migrations())
    except Exception:
        logger.exception("Failed to run migrations")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
