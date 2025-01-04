from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.orm import Session

from .handlers import start, wallet, smart_money, help, buy
from .middleware import DatabaseMiddleware
from ..services.solana import SolanaService
from ..utils.config import Config
from ..utils.logger import setup_logging

def create_bot(config: Config, session: Session) -> Bot:
    """Creates and configures the bot"""
    bot = Bot(token=config.telegram_bot_token)
    return bot

def setup_handlers(dp: Dispatcher, session: Session, solana_service: SolanaService):
    """Sets up all handlers"""
    # Register middlewares
    dp.message.middleware(DatabaseMiddleware(session))
    dp.callback_query.middleware(DatabaseMiddleware(session))
    
    # Include routers
    dp.include_router(start.router)
    dp.include_router(wallet.router)
    dp.include_router(smart_money.router)
    dp.include_router(help.router)
    dp.include_router(buy.router)
    
    return dp

def create_dispatcher(config: Config, session: Session, solana_service: SolanaService) -> Dispatcher:
    """Creates and configures the dispatcher"""
    # Setup logger
    setup_logging()
    
    # Create dispatcher
    dp = Dispatcher(storage=MemoryStorage())
    
    # Setup handlers
    setup_handlers(dp, session, solana_service)
    
    return dp 