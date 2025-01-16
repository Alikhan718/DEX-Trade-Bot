import os
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import NullPool
from dotenv import load_dotenv
from src.database import Base

# Load environment variables
load_dotenv()

# Alembic Config object, which provides access to the .ini file values
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The target metadata for auto-generation
target_metadata = Base.metadata

# Database URL from environment variables
database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost/dbname")

# Create async engine
connectable = create_async_engine(database_url, poolclass=NullPool)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async connection."""
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)


def do_run_migrations(connection):
    """Helper to set up context configuration for migrations."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# This will automatically determine whether Alembic should run in offline or online mode
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
