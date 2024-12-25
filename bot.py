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
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp

# Database ORM
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql import func

# Solana integration
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey as PublicKey
from solders.keypair import Keypair

# Additional utilities
import logging.config
import uuid
from datetime import datetime, timedelta
import base58
from contextlib import asynccontextmanager

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
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                'encoding': 'utf-8'
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
                'level': get_env_variable('LOG_LEVEL', 'INFO'),
                'stream': 'ext://sys.stdout'
            },
            'file': {
                'class': 'logging.FileHandler',
                'filename': 'bot.log',
                'formatter': 'standard',
                'level': 'ERROR',
                'encoding': 'utf-8'
            }
        },
        'loggers': {
            '': {
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
    telegram_id = Column(Integer, unique=True)
    solana_wallet = Column(String, unique=True)
    private_key = Column(String)
    referral_code = Column(String, unique=True)
    total_volume = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())
    last_activity = Column(DateTime)

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
            # Initialize price tracking attributes
            self.sol_price = 0
            self.last_price_update = None
            
            # Initialize bot components
            self.bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
            self.dp = Dispatcher(storage=MemoryStorage())
            self.solana_client = AsyncClient(Config.SOLANA_RPC_URL)
            
            # Initialize database
            engine = create_engine(Config.DATABASE_URL)
            Base.metadata.create_all(engine)
            self.Session = sessionmaker(bind=engine)
            
            # Register handlers
            self._register_handlers()
            
            logger.info("Bot initialized successfully")
        
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise
    
    def _register_handlers(self):
        """Register message and callback handlers"""
        # Command handlers
        self.dp.message.register(self.cmd_start, Command("start"))
        self.dp.message.register(self.import_wallet, Command("import_wallet"))
        
        # Callback query handlers
        self.dp.callback_query.register(self.on_copy_trader_button, lambda c: c.data == "copy_trader")
        self.dp.callback_query.register(self.on_my_copies_button, lambda c: c.data == "my_copies")
        self.dp.callback_query.register(self.on_show_private_key_button, lambda c: c.data == "show_private_key")
        self.dp.callback_query.register(self.on_import_wallet_button, lambda c: c.data == "import_wallet")
        self.dp.callback_query.register(self.on_main_menu_button, lambda c: c.data == "main_menu")
    
    async def show_main_menu(self, message: types.Message):
        """Show main menu with wallet info"""
        try:
            session = self.Session()
            user = session.query(User).filter(
                User.telegram_id == message.from_user.id
            ).first()
            
            if not user:
                # Generate new Solana wallet for new user
                new_keypair = Keypair()
                user = User(
                    telegram_id=message.from_user.id,
                    solana_wallet=str(new_keypair.pubkey()),
                    private_key=base58.b58encode(bytes(new_keypair)).decode(),
                    referral_code=str(uuid.uuid4())[:8],
                    total_volume=0.0
                )
                session.add(user)
                session.commit()
            
            # Get wallet balance and SOL price
            balance = await self.get_wallet_balance(user.solana_wallet)
            sol_price = await self.get_sol_price()
            usd_balance = balance * sol_price
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💰 Копировать трейдера", callback_data="copy_trader"),
                    InlineKeyboardButton(text="📊 Мои копии", callback_data="my_copies")
                ],
                [
                    InlineKeyboardButton(text="🔑 Показать приватный ключ", callback_data="show_private_key"),
                    InlineKeyboardButton(text="📥 Импортировать кошелек", callback_data="import_wallet")
                ]
            ])
            
            await message.answer(
                f"💳 Ваш кошелек: <code>{user.solana_wallet[:8]}...{user.solana_wallet[-4:]}</code>\n\n"
                f"💰 Баланс: {balance:.4f} SOL (${usd_balance:.2f})\n\n"
                "💡 Вы можете отправить SOL на этот адрес или импортировать существующий кошелек.\n\n"
                "Выберите действие:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error showing main menu: {e}")
            await message.answer("❌ Ошибка при загрузке меню")
        finally:
            session.close()

    async def cmd_start(self, message: types.Message):
        """Handle /start command - creates new wallet for user"""
        try:
            session = self.Session()
            try:
                user = session.query(User).filter(
                    User.telegram_id == message.from_user.id
                ).first()
                
                if user:
                    await self.show_main_menu(message)
                else:
                    # Generate new Solana wallet
                    new_keypair = Keypair()  # Creates a new random keypair by default
                    
                    # Create user with new wallet
                    user = User(
                        telegram_id=message.from_user.id,
                        solana_wallet=str(new_keypair.pubkey()),
                        private_key=base58.b58encode(bytes(new_keypair)).decode(),
                        referral_code=str(uuid.uuid4())[:8],
                        total_volume=0.0
                    )
                    session.add(user)
                    session.commit()
                    
                    await message.answer(
                        "🎉 Добро пожаловать! Для вас создан новый Solana кошелек:\n\n"
                        f"Адрес: <code>{str(new_keypair.pubkey())}</code>\n\n"
                        "⚠️ ВАЖНО: Храните приватный ключ в безопасном месте!\n"
                        "Никогда не делитесь им ни с кем.\n"
                        "Используйте кнопку «Показать приватный ключ» чтобы увидеть его.",
                        parse_mode="HTML"
                    )
                    await self.show_main_menu(message)
                
                logger.info(f"Start command handled for user {message.from_user.id}")
                
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()
            
        except Exception as e:
            logger.error(f"Error handling start command: {e}")
            await message.answer("❌ Ошибка при запуске бота. Попробуйте еще раз.")

    async def connect_wallet(self, message: types.Message):
        """Connect Solana wallet"""
        try:
            # Extract wallet address
            parts = message.text.split()
            if len(parts) != 2:
                await message.answer(
                    "❌ Please provide a wallet address:\n"
                    "<code>/connect_wallet WALLET_ADDRESS</code>",
                    parse_mode="HTML"
                )
                return
            
            wallet_address = parts[1]
            
            # Validate Solana address format
            try:
                # Convert to bytes first if it's base58
                if len(wallet_address) == 44:  # Base58 encoded length
                    wallet_bytes = base58.b58decode(wallet_address)
                    public_key = PublicKey(wallet_bytes)
                else:
                    public_key = PublicKey(wallet_address)
            except Exception as e:
                logger.error(f"Invalid wallet address: {e}")
                await message.answer("❌ Invalid Solana wallet address")
                return
            
            # Database session
            session = self.Session()
            try:
                # Check if user already exists
                existing_user = session.query(User).filter(
                    User.telegram_id == message.from_user.id
                ).first()
                
                if existing_user:
                    existing_user.solana_wallet = str(public_key)
                    await message.answer(
                        f"✅ Wallet updated successfully!\n"
                        f"Address: <code>{str(public_key)[:8]}...</code>",
                        parse_mode="HTML"
                    )
                    await self.show_main_menu(message)  # Show main menu after update
                else:
                    # Create user
                    user = User(
                        telegram_id=message.from_user.id,
                        solana_wallet=str(public_key),
                        referral_code=str(uuid.uuid4())[:8],
                        total_volume=0.0
                    )
                    session.add(user)
                    await message.answer(
                        f"✅ Wallet connected successfully!\n"
                        f"Address: <code>{str(public_key)[:8]}...</code>",
                        parse_mode="HTML"
                    )
                    await self.show_main_menu(message)  # Show main menu after connection
                
                session.commit()
                logger.info(f"Wallet connected for user {message.from_user.id}")
                
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()
        
        except Exception as e:
            logger.error(f"Wallet connection error: {e}")
            await message.answer(
                "❌ Error connecting wallet. Please try again or contact support."
            )
    
    async def list_top_traders(self, message: types.Message):
        """List top traders"""
        session = self.Session()
        try:
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
            await message.answer("❌ Error retrieving top traders")
        finally:
            session.close()

    async def start(self):
        """Start the bot polling"""
        try:
            logger.info("Starting bot polling")
            await self.dp.start_polling(self.bot)
        
        except Exception as e:
            logger.error(f"Bot polling error: {e}")

    async def on_connect_wallet_button(self, callback_query: types.CallbackQuery):
        """Handle connect wallet button press"""
        await callback_query.answer()
        await callback_query.message.answer(
            "Please send your Solana wallet address using the command:\n"
            "<code>/connect_wallet WALLET_ADDRESS</code>"
        )

    async def on_top_traders_button(self, callback_query: types.CallbackQuery):
        """Handle top traders button press"""
        await callback_query.answer()
        await self.list_top_traders(callback_query.message)

    async def follow_trader(self, message: types.Message):
        """Follow a trader's transactions"""
        session = self.Session()
        try:
            trader_address = message.text.split()[1]
            PublicKey(trader_address)  # Validate address
            
            # Check if trader exists
            trader = session.query(CopyTrader).filter_by(wallet_address=trader_address).first()
            if not trader:
                trader = CopyTrader(wallet_address=trader_address)
                session.add(trader)
            
            trader.followers_count += 1
            session.commit()
            
            await message.answer(f"��� Now following trader {trader_address[:8]}...")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error following trader: {e}")
            await message.answer("❌ Error following trader. Please check the address.")
        finally:
            session.close()

    async def on_show_private_key(self, callback_query: types.CallbackQuery):
        """Handle private key display request"""
        await callback_query.answer()
        
        session = self.Session()
        try:
            user = session.query(User).filter(
                User.telegram_id == callback_query.from_user.id
            ).first()
            
            if not user:
                await callback_query.message.answer(
                    "❌ Кошелек не найден. Используйте /start для создания."
                )
                return
            
            # Send private key in private message
            await callback_query.message.answer(
                "🔐 Ваш приватный ключ:\n\n"
                f"<code>{user.private_key}</code>\n\n"
                "⚠️ ВНИМАНИЕ:\n"
                "1. Никогда не делитесь этим ключом\n"
                "2. Сохраните его в надежном месте\n"
                "3. Потеря ключа = потеря доступа к кошельку",
                parse_mode="HTML"
            )
            
        finally:
            session.close()

    async def on_import_wallet_button(self, callback_query: types.CallbackQuery):
        """Handle wallet import button"""
        await callback_query.answer()
        await callback_query.message.answer(
            "📥 Для импорта существующего кошелька отправьте команду:\n"
            "<code>/import_wallet PRIVATE_KEY</code>\n\n"
            "⚠️ ВНИМАНИЕ:\n"
            "1. Импорт нового кошелька заменит текущий\n"
            "2. Сохраните приватный ключ текущего кошелька, если хотите сохранить к нему доступ\n"
            "3. Никогда не делитесь приватным ключом",
            parse_mode="HTML"
        )

    async def import_wallet(self, message: types.Message):
        """Import existing wallet using private key"""
        try:
            # Delete message with private key for security
            await message.delete()
            
            parts = message.text.split()
            if len(parts) != 2:
                await message.answer(
                    "❌ Пожалуйста, укажите приватный ключ:\n"
                    "<code>/import_wallet PRIVATE_KEY</code>",
                    parse_mode="HTML"
                )
                return
            
            private_key = parts[1]
            
            try:
                # Validate private key and get public key
                secret_bytes = base58.b58decode(private_key)
                keypair = Keypair.from_bytes(secret_bytes)  # Use the full bytes
                public_key = str(keypair.pubkey())
                
            except Exception as e:
                logger.error(f"Invalid private key: {e}")
                await message.answer("❌ Неверный формат приватного ключа")
                return
            
            # Update database
            session = self.Session()
            try:
                user = session.query(User).filter(
                    User.telegram_id == message.from_user.id
                ).first()
                
                if user:
                    user.solana_wallet = public_key
                    user.private_key = private_key
                else:
                    user = User(
                        telegram_id=message.from_user.id,
                        solana_wallet=public_key,
                        private_key=private_key,
                        referral_code=str(uuid.uuid4())[:8],
                        total_volume=0.0
                    )
                    session.add(user)
                
                session.commit()
                
                await message.answer(
                    "✅ Кошелек ��спешно импортирован!\n"
                    f"Адрес: <code>{public_key[:8]}...</code>",
                    parse_mode="HTML"
                )
                await self.show_main_menu(message)
                
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Wallet import error: {e}")
            await message.answer(
                "❌ Ошибка при импорте кошелька. Попробуйте еще раз."
            )

    async def get_sol_price(self):
        """Get current SOL price in USD"""
        # Update price only every 5 minutes
        if (self.last_price_update and 
            datetime.now() - self.last_price_update < timedelta(minutes=5)):
            return self.sol_price
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd') as response:
                    data = await response.json()
                    self.sol_price = data['solana']['usd']
                    self.last_price_update = datetime.now()
                    return self.sol_price
        except Exception as e:
            logger.error(f"Error fetching SOL price: {e}")
            return self.sol_price

    async def get_wallet_balance(self, wallet_address: str) -> float:
        """Get SOL balance for wallet"""
        try:
            response = await self.solana_client.get_balance(PublicKey.from_string(wallet_address))
            return response.value / 1e9  # Convert lamports to SOL
        except Exception as e:
            logger.error(f"Error fetching wallet balance: {e}")
            return 0.0

    # Add "Back to menu" button to other menus
    async def on_copy_trader_button(self, callback_query: types.CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Найти трейдера", callback_data="find_trader")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        await callback_query.message.edit_text(
            "👥 Копирование трейдеров\n\n"
            "Выберите действие:",
            reply_markup=keyboard
        )

    async def on_my_copies_button(self, callback_query: types.CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        await callback_query.message.edit_text(
            "📊 Ваши активные копии:\n\n"
            "(Список будет здесь)",
            reply_markup=keyboard
        )

    # Add handler for back button
    async def on_main_menu_button(self, callback_query: types.CallbackQuery):
        await callback_query.answer()
        await self.show_main_menu(callback_query.message)

    async def on_show_private_key_button(self, callback_query: types.CallbackQuery):
        """Handle show private key button press"""
        try:
            session = self.Session()
            user = session.query(User).filter(
                User.telegram_id == callback_query.from_user.id
            ).first()
            
            if user:
                # Send private key in private message
                await callback_query.message.answer(
                    "🔑 Ваш приватный ключ:\n"
                    f"<code>{user.private_key}</code>\n\n"
                    "⚠️ Никому не показывайте этот ключ!",
                    parse_mode="HTML"
                )
                # Delete message after 30 seconds
                await asyncio.sleep(30)
                await callback_query.message.delete()
            else:
                await callback_query.answer("❌ Кошелек не найден")
        except Exception as e:
            logger.error(f"Error showing private key: {e}")
            await callback_query.answer("❌ Ошибка при показе приватного ключа")
        finally:
            session.close()

    async def on_import_wallet_button(self, callback_query: types.CallbackQuery):
        """Handle import wallet button press"""
        await callback_query.message.answer(
            "🔑 Чтобы импортировать кошелек, отправьте команду:\n"
            "<code>/import_wallet PRIVATE_KEY</code>",
            parse_mode="HTML"
        )
        await callback_query.answer()

async def main():
    """Main async entry point"""
    try:
        bot = SolanaDEXBot()
        await bot.start()
    except Exception as e:
        logger.critical(f"Critical error starting bot: {e}")

if __name__ == '__main__':
    asyncio.run(main())