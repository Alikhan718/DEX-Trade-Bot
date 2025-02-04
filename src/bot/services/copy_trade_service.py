import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram import Bot

from src.solana_module.solana_client import SolanaClient
from src.solana_module.copy_trade_manager import CopyTradeManager
from src.database.models import CopyTrade

logger = logging.getLogger(__name__)


class CopyTradeService:
    _instance: Optional["CopyTradeService"] = None
    _bot: Optional[Bot] = None

    def __new__(cls):
        """Создаем единственный экземпляр синглтона"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False  # Флаг для отслеживания инициализации
        return cls._instance

    def __init__(self):
        """Инициализируем только один раз"""
        if self._initialized:
            return

        self.solana_client = SolanaClient(100000)  # Default compute unit price
        self.manager: Optional[CopyTradeManager] = None
        self.Session: Optional[async_sessionmaker] = None
        self._initialized = True  # Флаг, предотвращающий повторную инициализацию

    @classmethod
    def set_bot(cls, bot: Bot):
        """Устанавливаем экземпляр бота"""
        cls._bot = bot
        logger.info("Bot instance set in CopyTradeService")

    async def start(self, session: AsyncSession):
        """Запуск сервиса копи-трейда"""
        if not self._bot:
            raise ValueError("Bot instance not set. Call set_bot() first.")

        try:
            # Инициализация менеджера
            self.manager = CopyTradeManager(self.solana_client, self._bot)

            # Создание фабрики сессий
            self.Session = async_sessionmaker(
                session.bind,
                expire_on_commit=False
            )

            # Загрузка активных трейдов из базы
            await self.manager.load_active_trades(session)

            # Установка обработчика транзакций
            self.manager.monitor.set_transaction_callback(self.handle_transaction_with_session)

            logger.info("Copy Trade service started")
        except Exception as e:
            logger.error(f"Error while starting copy trade service: {e}", exc_info=True)
            raise

    async def stop(self):
        """Остановка сервиса"""
        try:
            if self.manager:
                await self.manager.monitor.stop_monitoring()
            logger.info("COPY TRADE STOPPED")
        except Exception as e:
            logger.error(f"ERROR COPY TRADE: {e}", exc_info=True)
            raise

    async def handle_transaction_with_session(self, leader: str, tx_type: str, signature: str, token_address: str):
        """Создает новую сессию и обрабатывает транзакцию"""
        if not self.Session:
            logger.error("ERROR COPY TRADE")
            return

        async with self.Session() as session:
            try:
                await self.handle_transaction(leader, tx_type, signature, token_address, session)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"ERROR COPY TRADE: {e}", exc_info=True)

    async def handle_transaction(self, leader: str, tx_type: str, signature: str, token_address: str, session: AsyncSession):
        """Обрабатывает найденную транзакцию"""
        try:
            if self.manager:
                await self.manager.process_transaction(leader, tx_type, signature, token_address, session)
        except Exception as e:
            logger.error(f"ERROR COPY TRADE: {e}", exc_info=True)

    async def add_copy_trade(self, copy_trade: CopyTrade, session: AsyncSession):
        """Добавляет копи-трейд"""
        try:
            if self.manager:
                await self.manager.add_copy_trade(copy_trade)
            logger.info(f"COPY TRADE:{copy_trade.id} FOR WALLET {copy_trade.wallet_address}")
        except Exception as e:
            logger.error(f"ERROR COPY TRADE: {e}", exc_info=True)
            raise

    async def remove_copy_trade(self, copy_trade: CopyTrade, session: AsyncSession = None):
        """Удаляет копи-трейд"""
        try:
            if self.manager:
                await self.manager.remove_copy_trade(copy_trade)
            logger.info(f"DELETE COPY TRADE {copy_trade.id}")
        except Exception as e:
            logger.error(f"ERROR COPY TRADE: {e}", exc_info=True)
            raise

    async def toggle_copy_trade(self, copy_trade: CopyTrade, session: AsyncSession):
        """Переключает статус копи-трейда (активен/не активен)"""
        try:
            if copy_trade.is_active:
                await self.add_copy_trade(copy_trade)
            else:
                await self.remove_copy_trade(copy_trade)

            await session.commit()
            logger.info(f"STATUS COPY TRADE {copy_trade.id} CHANGED TO {copy_trade.is_active}")
        except Exception as e:
            logger.error(f"ERROR TOGGLE COPY TRADE: {e}", exc_info=True)
            raise
