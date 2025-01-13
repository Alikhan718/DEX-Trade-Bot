import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from ...database.models import User, CopyTrade, CopyTradeTransaction
from ...solana_module.copy_trade_manager import CopyTradeManager
from ...solana_module.solana_client import SolanaClient

logger = logging.getLogger(__name__)

class CopyTradeService:
    _instance: Optional['CopyTradeService'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.solana_client = SolanaClient(100000)  # Default compute unit price
            self.manager = CopyTradeManager(self.solana_client)
            self.Session = None
            self.initialized = True

    async def start(self, session: AsyncSession):
        """Start the copy trade service"""
        try:
            # Store session factory
            self.Session = async_sessionmaker(
                session.bind,
                expire_on_commit=False
            )
            
            # Load active trades from database
            await self.manager.load_active_trades(session)
            
            # Set up transaction callback
            self.manager.monitor.set_transaction_callback(self.handle_transaction_with_session)
            
            logger.info("Copy trade service started")
        except Exception as e:
            logger.error(f"Error starting copy trade service: {e}")
            raise

    async def stop(self):
        """Stop the copy trade service"""
        try:
            await self.manager.monitor.stop_monitoring()
            logger.info("Copy trade service stopped")
        except Exception as e:
            logger.error(f"Error stopping copy trade service: {e}")
            raise

    async def handle_transaction_with_session(self, leader: str, tx_type: str, signature: str, token_address: str):
        """Create new session and handle transaction"""
        if not self.Session:
            logger.error("Session factory not initialized")
            return
            
        async with self.Session() as session:
            try:
                await self.handle_transaction(leader, tx_type, signature, token_address, session)
            except Exception as e:
                logger.error(f"Error handling transaction: {e}")
                await session.rollback()
            else:
                await session.commit()

    async def handle_transaction(self, leader: str, tx_type: str, signature: str, token_address: str, session: AsyncSession):
        """Handle detected transaction"""
        try:
            await self.manager.process_transaction(leader, tx_type, signature, token_address, session)
        except Exception as e:
            logger.error(f"Error handling transaction: {e}")

    async def add_copy_trade(self, copy_trade: CopyTrade):
        """Add new copy trade"""
        try:
            if copy_trade.is_active:
                await self.manager.add_copy_trade(copy_trade)
            logger.info(f"Added copy trade {copy_trade.id} for wallet {copy_trade.wallet_address}")
        except Exception as e:
            logger.error(f"Error adding copy trade: {e}")
            raise

    async def remove_copy_trade(self, copy_trade: CopyTrade):
        """Remove copy trade"""
        try:
            await self.manager.remove_copy_trade(copy_trade)
            logger.info(f"Removed copy trade {copy_trade.id}")
        except Exception as e:
            logger.error(f"Error removing copy trade: {e}")
            raise

    async def toggle_copy_trade(self, copy_trade: CopyTrade, session: AsyncSession):
        """Toggle copy trade active status"""
        try:
            if copy_trade.is_active:
                await self.add_copy_trade(copy_trade)
            else:
                await self.remove_copy_trade(copy_trade)
            await session.commit()
            logger.info(f"Toggled copy trade {copy_trade.id} active status to {copy_trade.is_active}")
        except Exception as e:
            logger.error(f"Error toggling copy trade: {e}")
            raise 