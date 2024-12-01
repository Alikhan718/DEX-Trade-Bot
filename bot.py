import os
import logging
import asyncio
from typing import Dict, Optional

# Environment variable management
from dotenv import load_dotenv

# Telegram and bot framework
from aiogram import Bot, Dispatcher, Router, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

# Database ORM
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql import func

# Solana integration
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey as PublicKey

# Additional utilities
import logging.config
import uuid
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

# Base configuration and logging setup
class Config:
    """Centralized configuration management with secure loading"""
    
    @staticmethod
    def get_env_variable(var_name: str, default: Optional[str] = None) -> str:
        """
        Safely retrieve environment variables
        
        :param var_name: Name of the environment variable
        :param default: Default value if variable is not set
        :return: Value of the environment variable
        """
        value = os.getenv(var_name, default)
        if value is None or value.strip() == "":
            raise ValueError(f"Critical environment variable {var_name} is not set!")
        return value.strip()
    
    # Core bot configuration
    TELEGRAM_BOT_TOKEN = get_env_variable('TELEGRAM_BOT_TOKEN')
    SOLANA_RPC_URL = get_env_variable('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
    DATABASE_URL = get_env_variable('DATABASE_URL', 'sqlite:///solana_dex_bot.db')
    
    # Logging configuration
    LOGGING_CONFIG = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
                'level': get_env_variable('LOG_LEVEL', 'INFO')
            },
            'file': {
                'class': 'logging.FileHandler',
                'filename': 'bot.log',
                'formatter': 'standard',
                'level': 'ERROR'
            }
        },
        'loggers': {
            '': {  # Root logger
                'handlers': ['console', 'file'],
                'level': get_env_variable('LOG_LEVEL', 'INFO'),
                'propagate': True
            }
        }
    }

# Configure logging
logging.config.dictConfig(Config.LOGGING_CONFIG)
logger = logging.getLogger(__name__)

# Database Models
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    solana_wallet = Column(String, unique=True)
    referral_code = Column(String, unique=True)
    total_volume = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())
    last_activity = Column(DateTime, onupdate=func.now())

class CopyTrader(Base):
    __tablename__ = 'copy_traders'
    
    id = Column(Integer, primary_key=True)
    wallet_address = Column(String, unique=True, nullable=False)
    success_rate = Column(Float, default=0)
    total_trades = Column(Integer, default=0)
    followers_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

class Trade(Base):
    __tablename__ = 'trades'
    
    id = Column(Integer, primary_key=True)
    trader_id = Column(Integer, nullable=False)
    token_address = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    trade_type = Column(String, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())

class SolanaDEXBot:
    def __init__(self):
        """Initialize bot with secure configuration"""
        try:
            # Bot and Router initialization
            self.bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
            self.storage = MemoryStorage()
            self.dp = Dispatcher(bot=self.bot, storage=self.storage)
            self.router = Router()
            
            # Solana RPC Client
            self.solana_client = AsyncClient(Config.SOLANA_RPC_URL)
            
            # Database setup
            self.engine = create_engine(Config.DATABASE_URL)
            Base.metadata.create_all(self.engine)
            self.Session = sessionmaker(bind=self.engine)
            
            # Register handlers
            self._register_handlers()

            self.dp.include_router(self.router)
            
            logger.info("Bot initialized successfully")
        
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise
    
    def _register_handlers(self):
        """Register all message and callback handlers"""
        # Start command
        self.router.message.register(self.cmd_start, Command("start"))
        
        # Wallet connection
        self.router.message.register(self.connect_wallet, Command("connect_wallet"))
        
        # Top traders
        self.router.message.register(self.list_top_traders, Command("top_traders"))
    
    async def cmd_start(self, message: types.Message):
        """Handle /start command"""
        try:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='🔗 Подключить кошелек',
                        callback_data='connect_wallet'
                    ),
                    types.InlineKeyboardButton(
                        text='🏆 Топ трейдеры',
                        callback_data='top_traders'
                    )
                ]
            ])

            await message.answer(
                "🚀 Solana DEX Бот\nВыберите действие:",
                reply_markup=keyboard
            )

            logger.info(f"Start command handled for user {message.from_user.id}")

        except Exception as e:
            logger.error(f"Error in start command: {e}")

    
    async def connect_wallet(self, message: types.Message):
        """Connect Solana wallet"""
        try:
            # Extract wallet address
            wallet_address = message.text.split()[1]
            
            # Validate Solana address
            PublicKey(wallet_address)
            
            # Database session
            session = self.Session()
            
            # Create user
            user = User(
                telegram_id=message.from_user.id, 
                solana_wallet=wallet_address,
                referral_code=str(uuid.uuid4())[:8]
            )
            
            session.add(user)
            session.commit()
            
            await message.answer(f"✅ Кошелек {wallet_address} подключен!")
            
            logger.info(f"Wallet connected for user {message.from_user.id}")
        
        except Exception as e:
            logger.error(f"Wallet connection error: {e}")
            await message.answer("❌ Ошибка подключения кошелька")
    
    async def list_top_traders(self, message: types.Message):
        """List top traders"""
        try:
            session = self.Session()
            
            # Query top traders
            top_traders = session.query(CopyTrader).order_by(
                CopyTrader.success_rate.desc()
            ).limit(10)
            
            # Prepare response
            response = "🏆 Топ трейдеры:\n\n"
            for trader in top_traders:
                response += (
                    f"Адрес: {trader.wallet_address[:8]}...\n"
                    f"Успешность: {trader.success_rate*100:.2f}%\n"
                    f"Сделок: {trader.total_trades}\n"
                    f"Подписчиков: {trader.followers_count}\n\n"
                )
            
            await message.answer(response)
            
            logger.info("Top traders list retrieved")
        
        except Exception as e:
            logger.error(f"Error retrieving top traders: {e}")
    
    async def start(self):
        """Start the bot polling"""
        try:
            logger.info("Starting bot polling")
            await self.dp.start_polling(self.bot)
        
        except Exception as e:
            logger.error(f"Bot polling error: {e}")

async def main():
    """Main async entry point"""
    try:
        bot = SolanaDEXBot()
        await bot.start()
    except Exception as e:
        logger.critical(f"Critical error starting bot: {e}")

if __name__ == '__main__':
    asyncio.run(main())