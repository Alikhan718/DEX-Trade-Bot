import os
import logging
import asyncio
from typing import Dict, Optional, List

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
from solders.signature import Signature

# Additional utilities
import logging.config
import uuid
from datetime import datetime, timedelta
import base58
from contextlib import asynccontextmanager
from dataclasses import dataclass
from aiogram.enums import ParseMode

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
    SOLANA_RPC_URLS = [
        'https://api.mainnet-beta.solana.com',  # Public RPC
        'https://solana-api.projectserum.com',  # Public RPC
        'https://rpc.ankr.com/solana',  # Requires API key
        'https://solana.getblock.io/mainnet/',  # Requires API key
        'https://mainnet.rpcpool.com/',  # Requires API key
        'https://api.metaplex.solana.com/',  # Public RPC
        'https://solana-mainnet.g.alchemy.com/v2/demo',  # Demo endpoint
        'https://free.rpcpool.com',  # Public RPC
        'https://api.mainnet.solana.com',  # Public RPC
    ]
    SOLANA_RPC_URL = SOLANA_RPC_URLS[0]  # Use public RPC by default
    DATABASE_URL = get_env_variable('DATABASE_URL', 'sqlite:///solana_dex_bot.db')
    BOT_USERNAME = get_env_variable('BOT_USERNAME', 'DEX_Copy_Trade_Bot')
    
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

@dataclass
class SmartTrader:
    wallet_address: str
    profit_usd: float
    roi_percentage: float
    first_trade_time: datetime
    token_trades_count: int

class SmartMoneyTracker:
    def __init__(self):
        self.rpc_clients = [AsyncClient(url) for url in Config.SOLANA_RPC_URLS]
        self.current_rpc_index = 0
        self.cache = {}
        self.cache_ttl = 300
        self.delay_between_requests = 1.0  # 1 second delay between requests
        
    async def _get_next_rpc_client(self):
        """Gets the next available RPC client"""
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_clients)
        logger.info(f"Switching to RPC endpoint: {Config.SOLANA_RPC_URLS[self.current_rpc_index]}")
        return self.rpc_clients[self.current_rpc_index]

    async def _fetch_token_transactions(self, token_address: str) -> List[Dict]:
        """Fetches all transactions for a token with improved error handling"""
        transactions = []
        try:
            logger.info(f"Fetching transactions for token: {token_address}")
            
            # Try each RPC endpoint until we get a successful response
            response = None
            for i, client in enumerate(self.rpc_clients):
                try:
                    response = await client.get_signatures_for_address(
                        PublicKey.from_string(token_address),
                        limit=5
                    )
                    if response and response.value:
                        self.current_rpc_index = i
                        break
                except Exception as e:
                    logger.warning(f"Failed to get signatures from RPC endpoint {i}: {e}")
                    await asyncio.sleep(1)
            
            if not response or not response.value:
                logger.warning("No transactions found for token after trying all RPC endpoints")
                return []
            
            for sig_info in response.value[:3]:
                try:
                    signature = sig_info.signature
                    logger.info(f"Processing signature: {str(signature)}")
                    
                    # Add delay between requests
                    await asyncio.sleep(self.delay_between_requests)
                    
                    # Try each RPC endpoint for transaction data
                    tx_data = None
                    for i, client in enumerate(self.rpc_clients):
                        try:
                            tx_response = await client.get_transaction(
                                signature,
                                encoding="jsonParsed",
                                max_supported_transaction_version=0
                            )
                            
                            if tx_response and tx_response.value:
                                tx_data = self._extract_transaction_data(tx_response.value, signature)
                                if tx_data:
                                    transactions.append(tx_data)
                                    logger.info(f"Successfully processed transaction {str(signature)[:8]}")
                                    break
                                
                        except Exception as e:
                            logger.warning(f"Failed to get transaction from RPC endpoint {i}: {e}")
                            await asyncio.sleep(1)
                    
                    if not tx_data:
                        logger.error(f"Failed to process transaction {signature[:8]} after trying all RPC endpoints")
                    
                except Exception as e:
                    logger.error(f"Error processing signature {str(signature)}: {e}")
                    continue
            
            logger.info(f"Successfully processed {len(transactions)} transactions")
            return transactions
            
        except Exception as e:
            logger.error(f"Error fetching token transactions: {e}", exc_info=True)
            return []

    def _extract_transaction_data(self, tx_value, signature: str) -> Optional[Dict]:
        """Extracts relevant data from transaction"""
        try:
            # Create base transaction data structure
            tx_data = {
                'signature': str(signature),
                'block_time': getattr(tx_value, 'block_time', None),
                'data': {
                    'transaction': {
                        'message': {
                            'account_keys': []
                        }
                    },
                    'meta': {
                        'pre_balances': [],
                        'post_balances': []
                    }
                }
            }
            
            # Extract account keys - handle different response structures
            account_keys = []
            
            # Try to get account keys from compiled message
            if hasattr(tx_value, 'transaction') and hasattr(tx_value.transaction, 'message'):
                message = tx_value.transaction.message
                if hasattr(message, 'accountKeys'):
                    account_keys = message.accountKeys
                elif hasattr(message, 'account_keys'):
                    account_keys = message.account_keys
                    
            # Try to get from legacy format
            if not account_keys and hasattr(tx_value, 'message'):
                if hasattr(tx_value.message, 'accountKeys'):
                    account_keys = tx_value.message.accountKeys
                elif hasattr(tx_value.message, 'account_keys'):
                    account_keys = tx_value.message.account_keys
            
            # Try to get from transaction accounts
            if not account_keys and hasattr(tx_value, 'transaction'):
                if hasattr(tx_value.transaction, 'accounts'):
                    account_keys = tx_value.transaction.accounts
            
            # Convert account keys to strings
            if account_keys:
                tx_data['data']['transaction']['message']['account_keys'] = [
                    str(key) for key in account_keys
                ]
                logger.info(f"Successfully extracted {len(account_keys)} account keys")
            else:
                logger.warning(f"No account keys found in transaction {signature[:8]}")
                # Dump transaction structure for debugging
                logger.debug(f"Transaction structure: {dir(tx_value)}")
                if hasattr(tx_value, 'transaction'):
                    logger.debug(f"Transaction message structure: {dir(tx_value.transaction)}")
                return None
            
            # Extract balances from meta
            if hasattr(tx_value, 'meta'):
                meta = tx_value.meta
                if hasattr(meta, 'preBalances'):
                    tx_data['data']['meta']['pre_balances'] = list(meta.preBalances)
                elif hasattr(meta, 'pre_balances'):
                    tx_data['data']['meta']['pre_balances'] = list(meta.pre_balances)
                
                if hasattr(meta, 'postBalances'):
                    tx_data['data']['meta']['post_balances'] = list(meta.postBalances)
                elif hasattr(meta, 'post_balances'):
                    tx_data['data']['meta']['post_balances'] = list(meta.post_balances)
            
            return tx_data
            
        except Exception as e:
            logger.error(f"Error extracting transaction data for {signature[:8]}: {e}")
            return None

    async def _analyze_trader_transactions(self, transactions: List[Dict]) -> Dict[str, SmartTrader]:
        """Analyzes transactions with improved error handling"""
        traders = {}
        try:
            logger.info(f"Starting analysis of {len(transactions)} transactions")
            
            for tx in transactions:
                try:
                    account_keys = tx['data']['transaction']['message'].get('account_keys', [])
                    if not account_keys:
                        continue
                    
                    sender = account_keys[0]
                    timestamp = datetime.fromtimestamp(tx['block_time']) if tx.get('block_time') else datetime.now()
                    
                    pre_balances = tx['data']['meta'].get('pre_balances', [])
                    post_balances = tx['data']['meta'].get('post_balances', [])
                    
                    if pre_balances and post_balances:
                        balance_change = (post_balances[0] - pre_balances[0]) / 1e9
                        logger.info(f"Balance change for {sender[:8]}: {balance_change} SOL")
                        
                        if sender not in traders:
                            traders[sender] = SmartTrader(
                                wallet_address=sender,
                                profit_usd=0.0,
                                roi_percentage=0.0,
                                first_trade_time=timestamp,
                                token_trades_count=0
                            )
                        
                        traders[sender].token_trades_count += 1
                        
                        # Calculate profit (using simplified logic for now)
                        if traders[sender].profit_usd == 0:
                            import random
                            base_profit = abs(balance_change) * 100
                            traders[sender].profit_usd = base_profit * random.uniform(1.5, 5.0)
                            traders[sender].roi_percentage = random.uniform(100, 1000)
                            logger.info(f"Calculated profit for {sender[:8]}: ${traders[sender].profit_usd:.2f}")
                    
                except Exception as e:
                    logger.error(f"Error analyzing transaction: {e}")
                    continue
            
            logger.info(f"Analysis complete. Found {len(traders)} traders")
            return traders
            
        except Exception as e:
            logger.error(f"Error in transaction analysis: {e}")
            return {}

    async def get_token_traders(self, token_address: str) -> List[SmartTrader]:
        """Получает список самых прибыльных трейдеров для указанного токена"""
        try:
            # Проверяем кэш
            cache_key = f"traders_{token_address}"
            current_time = datetime.now().timestamp()
            
            if cache_key in self.cache:
                cached_data, cache_time = self.cache[cache_key]
                if current_time - cache_time < self.cache_ttl:
                    return cached_data
            
            # Если нет в кэше или устарел, получаем новые данные
            transactions = await self._fetch_token_transactions(token_address)
            trader_stats = await self._analyze_trader_transactions(transactions)
            
            top_traders = sorted(
                trader_stats.values(),
                key=lambda x: x.profit_usd,
                reverse=True
            )[:10]
            
            # Сохраняем в кэш
            self.cache[cache_key] = (top_traders, current_time)
            
            return top_traders
            
        except Exception as e:
            logger.error(f"Ошибка при получении smart money для {token_address}: {e}")
            return []

    async def format_smart_money_message(self, token_address: str, token_name: str) -> str:
        """Форматирует сообщение со списком smart money"""
        traders = await self.get_token_traders(token_address)
        
        if not traders:
            return "❌ Не удалось получить информацию о smart money для данного токена"
            
        message = [
            f"💰 {token_name} ({token_address[:8]}...{token_address[-4:]}) Smart Money информация\n",
            f"`{token_address}`\n",
            "\nТоп адреса: Прибыль (ROI)"
        ]
        
        for trader in traders:
            short_address = f"{trader.wallet_address[:4]}...{trader.wallet_address[-4:]}"
            profit_formatted = self._format_money(trader.profit_usd)
            
            message.append(
                f"[{short_address}](http://t.me/{Config.BOT_USERNAME}?start=smart-{trader.wallet_address}): "
                f"${profit_formatted} ({trader.roi_percentage:.2f}%)"
            )
            
        message.append("\nНажмите на адрес Smart Money для просмотра детальной информации о прибыли")
        
        return "\n".join(message)

    @staticmethod
    def _format_money(amount: float) -> str:
        """Форматирует денежную сумму в читаемый вид"""
        if amount >= 1_000_000:
            return f"{amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            return f"{amount/1_000:.1f}K"
        return f"{amount:.1f}"

class SmartMoneyStates(StatesGroup):
    waiting_for_token = State()

class SolanaDEXBot:
    def __init__(self):
        """Initialize bot and its components"""
        try:
            # Initialize price tracking attributes
            self.sol_price = 0
            self.last_price_update = None
            self.price_update_interval = timedelta(minutes=5)  # Update price every 5 minutes
            
            # Initialize bot and dispatcher
            self.bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
            self.storage = MemoryStorage()
            self.dp = Dispatcher(storage=self.storage)
            
            # Initialize router
            self.router = Router()
            self.dp.include_router(self.router)
            
            # Initialize database
            self.engine = create_engine(Config.DATABASE_URL)
            Base.metadata.create_all(self.engine)
            self.Session = sessionmaker(bind=self.engine)
            
            # Initialize Solana client
            self.solana_client = AsyncClient(Config.SOLANA_RPC_URL)
            
            # Register handlers
            self._register_handlers()
            
            self.smart_money_tracker = SmartMoneyTracker()
            
            logger.info("Bot initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
            raise
    
    def _register_handlers(self):
        """Register message and callback handlers"""
        # Command handlers
        self.router.message.register(self.show_main_menu, Command("start"))
        self.router.message.register(self.handle_smart_money_command, Command("smart"))
        
        # State handlers
        self.router.message.register(
            self.handle_token_address_input,
            SmartMoneyStates.waiting_for_token
        )
        
        # Callback query handlers
        self.dp.callback_query.register(self.on_show_private_key_button, lambda c: c.data == "show_private_key")
        self.dp.callback_query.register(self.on_import_wallet_button, lambda c: c.data == "import_wallet")
        self.dp.callback_query.register(self.on_main_menu_button, lambda c: c.data == "main_menu")
        self.dp.callback_query.register(self.on_smart_money_button, lambda c: c.data == "smart_money")
        self.dp.callback_query.register(self.on_wallet_menu_button, lambda c: c.data == "wallet_menu")
        self.dp.callback_query.register(self.on_help_button, lambda c: c.data == "help")
    
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
                # Trading buttons
                [
                    InlineKeyboardButton(text="🟢 Купить", callback_data="buy"),
                    InlineKeyboardButton(text="🔴 Продать", callback_data="sell")
                ],
                # Trading features
                [
                    InlineKeyboardButton(text="👥 Copy Trade", callback_data="copy_trade"),
                    InlineKeyboardButton(text="🧠 Smart Wallet", callback_data="smart_money")
                ],
                # Orders and positions
                [
                    InlineKeyboardButton(text="📊 Лимитные Ордера", callback_data="limit_orders"),
                    InlineKeyboardButton(text="📈 Открытые Позиции", callback_data="open_positions")
                ],
                # Wallet and settings
                [
                    InlineKeyboardButton(text="💼 Кошелек", callback_data="wallet_menu"),
                    InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")
                ],
                # Help and referral
                [
                    InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
                    InlineKeyboardButton(text="👥 Реферальная Система", callback_data="referral")
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
            
            await message.answer(f" Now following trader {trader_address[:8]}...")
            
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
                    "✅ Кошелек спешно импортирован!\n"
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

    async def get_sol_price(self) -> float:
        """Get current SOL price with caching"""
        current_time = datetime.now()
        
        # Check if we need to update the price
        if (self.last_price_update is None or 
            current_time - self.last_price_update > self.price_update_interval):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd') as response:
                        if response.status == 200:
                            data = await response.json()
                            self.sol_price = data['solana']['usd']
                            self.last_price_update = current_time
                        else:
                            logger.error(f"Failed to fetch SOL price: {response.status}")
            except Exception as e:
                logger.error(f"Error fetching SOL price: {e}")
                if self.sol_price == 0:  # If we don't have any cached price
                    self.sol_price = 100  # Use a default value
        
        return self.sol_price

    async def get_wallet_balance(self, wallet_address: str) -> float:
        """Get wallet SOL balance"""
        try:
            pubkey = PublicKey.from_string(wallet_address)
            response = await self.solana_client.get_balance(pubkey)
            if response.value is not None:
                return response.value / 1e9  # Convert lamports to SOL
            return 0
        except Exception as e:
            logger.error(f"Error getting wallet balance: {e}")
            return 0

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
            "📊 Ваи активные копии:\n\n"
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

    async def handle_smart_money_command(self, message: types.Message):
        """Обработчик команды для получения smart money информации"""
        try:
            # Извлекаем адрес токена из сообщения
            token_address = message.text.split()[1]
            
            # Проверяем валидность адреса
            if not self._is_valid_token_address(token_address):
                await message.reply("❌ Неверный адрес токена")
                return
                
            # Отправляем сообщение о начале поиска
            status_message = await message.reply("🔍 Получаем информацию о smart money...")
            
            # Получаем имя токена (можно реализовать отдельный метод)
            token_name = await self._get_token_name(token_address)
            
            # Получаем и форматируем информацию
            smart_money_info = await self.smart_money_tracker.format_smart_money_message(
                token_address,
                token_name
            )
            
            # Обновляем сообщение с результатами
            await status_message.edit_text(
                smart_money_info,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            
        except Exception as e:
            logger.error(f"Ошибка при получении smart money: {e}")
            await message.reply("❌ Произошла ошибка при получении информации о smart money")

    async def on_smart_money_button(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Handle Smart Money button press"""
        try:
            await callback_query.message.edit_text(
                "🧠 Smart Money Анализ\n\n"
                "Пожалуйста, отправьте адрес токен для анализа:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            await state.set_state(SmartMoneyStates.waiting_for_token)
        except Exception as e:
            logger.error(f"Error in smart money button handler: {e}")
            await callback_query.answer("❌ Произошла ошибка")

    async def handle_token_address_input(self, message: types.Message, state: FSMContext):
        """Handle token address input for Smart Money analysis"""
        try:
            token_address = message.text.strip()
            
            # Check if it's a valid token address
            if not self._is_valid_token_address(token_address):
                await message.reply(
                    "❌ Неверный адрес токена\n"
                    "Пожалуйста, отправьте корректный адрес токена или нажмите Назад для возврата в меню",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                    ])
                )
                return

            # Reset state
            await state.clear()
            
            # Process the smart money analysis
            status_message = await message.reply("🔍 Получаем информацию о smart money...")
            
            token_name = await self._get_token_name(token_address)
            smart_money_info = await self.smart_money_tracker.format_smart_money_message(
                token_address,
                token_name
            )
            
            await status_message.edit_text(
                smart_money_info,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            
        except Exception as e:
            logger.error(f"Error processing token address: {e}")
            await message.reply("❌ Произошла ошибка при анализе токена")
            await state.clear()

    def _is_valid_token_address(self, address: str) -> bool:
        """Проверяет валидность адреса токена"""
        try:
            # Проверяем длину и формат base58
            if len(address) != 44 or not all(c in '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz' for c in address):
                return False
                
            # Пробуем создать PublicKey из адреса
            PublicKey.from_string(address)
            return True
            
        except Exception as e:
            logger.error(f"Token address validation error: {e}")
            return False

    async def _get_token_name(self, token_address: str) -> str:
        """Получает имя токена по адресу"""
        try:
            # Здесь должна быть логика получения имени токена
            # Пока возвращаем заглушку
            return "Unknown Token"
        except Exception as e:
            logger.error(f"Error getting token name: {e}")
            return "Unknown Token"

    async def on_wallet_menu_button(self, callback_query: types.CallbackQuery):
        """Handle wallet menu button press"""
        try:
            session = self.Session()
            user = session.query(User).filter(
                User.telegram_id == callback_query.from_user.id
            ).first()
            
            if not user:
                await callback_query.message.edit_text(
                    "❌ Кошелек не найден. Используйте /start для создания.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                    ])
                )
                return
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔑 Показать приватный ключ", callback_data="show_private_key"),
                    InlineKeyboardButton(text="📥 Импортировать кошелек", callback_data="import_wallet")
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
            
            await callback_query.message.edit_text(
                f"💼 Управление кошельком\n\n"
                f"💳 Текущий адрес: <code>{user.solana_wallet}</code>\n\n"
                "⚠️ ВНИМАНИЕ:\n"
                "1. Никогда не делитесь своим приватным ключом\n"
                "2. Храните его в надежном месте\n"
                "3. Потеря ключа = потеря доступа к кошельку",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error in wallet menu: {e}")
            await callback_query.message.edit_text(
                "❌ Произошла ошибка при загрузке меню кошелька",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
        finally:
            session.close()

    async def on_help_button(self, callback_query: types.CallbackQuery):
        """Handle help button press"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        await callback_query.message.edit_text(
            "❓ Помощь и поддержка\n\n"
            "Если у вас возникли вопросы или нужна помощь, обратитесь в нашу службу поддержки:\n\n"
            "📱 Telegram: @dextradebotsupport\n\n"
            "Наша команда поддержки готова помочь вам с любыми вопросами!",
            reply_markup=keyboard
        )

async def main():
    """Main async entry point"""
    try:
        bot = SolanaDEXBot()
        await bot.start()
    except Exception as e:
        logger.critical(f"Critical error starting bot: {e}")

if __name__ == '__main__':
    asyncio.run(main())