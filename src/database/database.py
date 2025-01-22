from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from src.utils.config import Config

# Create base class for models
Base = declarative_base()

# Create async engine with PostgreSQL
engine = create_async_engine(
    Config.DATABASE_URL,
    echo=False,  # Disable SQL query logging in production
    pool_size=99999,
    max_overflow=10000,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=30
)

# Create session factory
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


async def init_models():
    """Initialize database models"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Get database session"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
