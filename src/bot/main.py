import logging
import asyncio
from typing import Optional, Dict, Any, Callable, Awaitable

from aiogram import Bot, Dispatcher, Router
from aiogram.types import TelegramObject
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from ..utils.config import Config
from ..utils.logger import setup_logging
from ..database.models import Base
from ..services.solana import SolanaService
from ..services.smart_money import SmartMoneyTracker
from ..services.token_info import TokenInfoService
from ..services.rugcheck import RugCheckService
from .middleware import DatabaseMiddleware, ServicesMiddleware
from .handlers import start, wallet, smart_money, help, buy, rugcheck, copy_trade

logger = setup_logging()

class SolanaDEXBot:
    def __init__(self):
        """Initialize bot and its components"""
        try:
            # Initialize bot and dispatcher
            self.bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
            self.storage = MemoryStorage()
            self.dp = Dispatcher(storage=self.storage)
            
            # Initialize services
            self.solana_service = SolanaService()
            self.smart_money_tracker = SmartMoneyTracker()
            self.rugcheck_service = RugCheckService()
            
            # Setup database
            self.engine = create_async_engine(
                Config.DATABASE_URL,
                pool_size=20,
                max_overflow=10,
                pool_timeout=30,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False
            )
            
            # Create async session factory
            self.Session = sessionmaker(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            # Register middlewares
            self.dp.message.middleware(DatabaseMiddleware(self.Session))
            self.dp.callback_query.middleware(DatabaseMiddleware(self.Session))
            
            self.dp.message.middleware(ServicesMiddleware(
                self.solana_service,
                self.smart_money_tracker,
                self.rugcheck_service
            ))
            self.dp.callback_query.middleware(ServicesMiddleware(
                self.solana_service,
                self.smart_money_tracker,
                self.rugcheck_service
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
        self.dp.include_router(buy.router)
        self.dp.include_router(rugcheck.router)
        self.dp.include_router(copy_trade.router)
        
        logger.info("Handlers registered successfully")
        
    async def init_db(self):
        """Initialize database tables"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
    async def start(self):
        """Start the bot polling"""
        try:
            logger.info("Initializing database...")
            await self.init_db()
            
            logger.info("Starting bot polling")
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
        finally:
            # Cleanup
            if hasattr(self, 'rugcheck_service'):
                await self.rugcheck_service.close()
            if hasattr(self, 'engine'):
                await self.engine.dispose()
            
            # Close all RPC clients
            if hasattr(self, 'smart_money_tracker'):
                for client in self.smart_money_tracker.rpc_clients:
                    await client.close()

async def main():
    """Main async entry point"""
    try:
        bot = SolanaDEXBot()
        await bot.start()
    except Exception as e:
        logger.critical(f"Critical error starting bot: {e}")

if __name__ == '__main__':
    asyncio.run(main()) 