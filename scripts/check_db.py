import os
import sys
import asyncio

# Add project root directory to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from sqlalchemy.ext.asyncio import create_async_engine
from src.database import Base
from src.utils.config import Config

async def update_database():
    """Update database schema"""
    try:
        # Create async engine
        engine = create_async_engine(Config.DATABASE_URL, echo=True)
        
        async with engine.begin() as conn:
            # Drop all tables
            await conn.run_sync(Base.metadata.drop_all)
            print("✅ Dropped existing tables")
            
            # Create all tables with new schema
            await conn.run_sync(Base.metadata.create_all)
            print("✅ Created new tables with updated schema")
            
        await engine.dispose()
        
    except Exception as e:
        print(f"❌ Error updating database: {e}")

if __name__ == "__main__":
    asyncio.run(update_database()) 