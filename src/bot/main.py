import logging
import asyncio
from typing import Optional, Dict, Any, Callable, Awaitable
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, Router, BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from ..utils.config import Config
from ..utils.logger import setup_logging
from ..database.models import Base
from ..services.solana import SolanaService
from ..services.smart_money import SmartMoneyTracker

from .handlers import start, wallet, smart_money, help

logger = setup_logging()

class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_factory):
        self.session_factory = session_factory
        
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        session = self.session_factory()
        data['session'] = session
        try:
            result = await handler(event, data)
            await self._commit_session(session)
            return result
        except Exception as e:
            await self._rollback_session(session)
            raise
        finally:
            await self._close_session(session)
    
    @staticmethod
    async def _commit_session(session):
        try:
            session.commit()
        except Exception as e:
            logger.error(f"Error committing session: {e}")
            session.rollback()
            raise
    
    @staticmethod
    async def _rollback_session(session):
        try:
            session.rollback()
        except Exception as e:
            logger.error(f"Error rolling back session: {e}")
    
    @staticmethod
    async def _close_session(session):
        try:
            session.close()
        except Exception as e:
            logger.error(f"Error closing session: {e}")

class ServicesMiddleware(BaseMiddleware):
    def __init__(self, solana_service: SolanaService, smart_money_tracker: SmartMoneyTracker):
        self.solana_service = solana_service
        self.smart_money_tracker = smart_money_tracker
        
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data['solana_service'] = self.solana_service
        data['smart_money_tracker'] = self.smart_money_tracker
        return await handler(event, data)

class SolanaDEXBot:
    def __init__(self):
        """Initialize bot and its components"""
        try:
            # Initialize bot and dispatcher
            self.bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
            self.storage = MemoryStorage()
            self.dp = Dispatcher(storage=self.storage)
            
            # Initialize router
            self.router = Router()
            self.dp.include_router(self.router)
            
            # Initialize database
            self.engine = create_engine(
                Config.DATABASE_URL,
                pool_size=40,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=3600
            )
            Base.metadata.create_all(self.engine)
            
            # Create scoped session factory
            session_factory = sessionmaker(bind=self.engine)
            self.Session = scoped_session(session_factory)
            
            # Initialize services
            self.solana_service = SolanaService()
            self.smart_money_tracker = SmartMoneyTracker()
            
            # Setup middlewares
            self.dp.update.outer_middleware(DatabaseMiddleware(self.Session))
            self.dp.update.outer_middleware(ServicesMiddleware(
                self.solana_service,
                self.smart_money_tracker
            ))
            
            # Register handlers
            self._register_handlers()
            
            logger.info("Bot initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise
    
    def _register_handlers(self):
        """Register message and callback handlers"""
        # Include routers from handler modules
        self.dp.include_router(start.router)
        self.dp.include_router(wallet.router)
        self.dp.include_router(smart_money.router)
        self.dp.include_router(help.router)
        
        logger.info("Handlers registered successfully")

    async def start(self):
        """Start the bot polling"""
        try:
            logger.info("Starting bot polling")
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
        finally:
            # Cleanup
            if hasattr(self, 'Session'):
                self.Session.remove()
            if hasattr(self, 'engine'):
                self.engine.dispose()

async def main():
    """Main async entry point"""
    try:
        bot = SolanaDEXBot()
        await bot.start()
    except Exception as e:
        logger.critical(f"Critical error starting bot: {e}")

if __name__ == '__main__':
    asyncio.run(main()) 