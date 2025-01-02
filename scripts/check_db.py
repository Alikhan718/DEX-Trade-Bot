from sqlalchemy import create_engine
from src.database import Base
from src.utils.config import Config

def update_database():
    """Update database schema"""
    try:
        # Create engine
        engine = create_engine(Config.DATABASE_URL, echo=True)
        
        # Drop all tables
        Base.metadata.drop_all(engine)
        print("✅ Dropped existing tables")
        
        # Create all tables with new schema
        Base.metadata.create_all(engine)
        print("✅ Created new tables with updated schema")
        
    except Exception as e:
        print(f"❌ Error updating database: {e}")

if __name__ == "__main__":
    update_database() 