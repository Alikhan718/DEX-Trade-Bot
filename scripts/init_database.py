import asyncio
import os
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.database.database import init_models
from src.utils.config import Config

async def initialize_database():
    """Initialize a new clean database"""
    try:
        print(f"Initializing database at: {Config.DATABASE_URL}")
        await init_models()
        print("✅ Database initialized successfully!")
        
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(initialize_database()) 