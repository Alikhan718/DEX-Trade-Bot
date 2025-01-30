import logging
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.utils.config import Config
from src.utils.logger import setup_logging
from src.database.models import Base
from src.services.solana_service import SolanaService
from src.services.smart_money import SmartMoneyTracker
from src.services.rugcheck import RugCheckService
from .middleware import DatabaseMiddleware, ServicesMiddleware
from .handlers import start, wallet, smart_money, help, buy, rugcheck, copy_trade, sell, settings, referral_system
from .services.copy_trade_service import CopyTradeService
from src.solana_module.limit_orders import AsyncLimitOrders

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
            self.copy_trade_service = CopyTradeService()
            self.copy_trade_service.set_bot(self.bot)  # Set bot instance for notifications
            
            # Initialize limit orders service
            self.limit_orders_service = None  # Will be initialized after DB setup

            # Setup database
            self.engine = create_async_engine(
                Config.DATABASE_URL,
                pool_size=99999,
                max_overflow=10000,
                pool_timeout=30,
                pool_pre_ping=True,
                pool_recycle=30,
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
        self.dp.include_router(sell.router)
        self.dp.include_router(rugcheck.router)
        self.dp.include_router(copy_trade.router)
        self.dp.include_router(buy.router)
        self.dp.include_router(settings.router)
        self.dp.include_router(referral_system.router)

        logger.info("Handlers registered successfully")

    async def init_db(self):
        """Initialize database tables"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def start(self):
        """Start the bot polling"""
        try:
            # Initialize limit orders service
            logger.info("Starting limit orders monitoring...")
            self.limit_orders_service = AsyncLimitOrders(self.Session, self)
            await self.limit_orders_service.start()
            
            # Start monitoring in background
            self.limit_orders_task = asyncio.create_task(
                self.limit_orders_service.monitor_prices(interval=15)
            )

            # Start copy trade service
            logger.info("Starting copy trade service...")
            async with self.Session() as session:
                await self.copy_trade_service.start(session)

            logger.info("Starting bot polling")
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
        finally:
            # Cleanup
            if hasattr(self, 'limit_orders_service'):
                await self.limit_orders_service.close()
            if hasattr(self, 'copy_trade_service'):
                await self.copy_trade_service.stop()
            if hasattr(self, 'rugcheck_service'):
                await self.rugcheck_service.close()
            if hasattr(self, 'engine'):
                await self.engine.dispose()

            # Close all RPC clients
            # if hasattr(self, 'smart_money_tracker'):
            #     for client in self.smart_money_tracker.rpc_clients:
            #         await client.close()


async def main():
    """Main async entry point"""
    try:
        bot = SolanaDEXBot()
        await bot.start()
    except Exception as e:
        logger.critical(f"Critical error starting bot: {e}")


if __name__ == '__main__':
    asyncio.run(main())
