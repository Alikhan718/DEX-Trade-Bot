import logging
from typing import Dict, Set
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from solana.transaction import Signature

from .solana_monitor import SolanaMonitor
from ..database.models import CopyTrade, ExcludedToken, CopyTradeTransaction, User
from .solana_client import SolanaClient

logger = logging.getLogger(__name__)

class CopyTradeManager:
    def __init__(self, solana_client: SolanaClient):
        self.solana_client = solana_client
        self.monitor = SolanaMonitor()
        self.active_trades: Dict[str, Set[CopyTrade]] = {}  # wallet -> set of copy trades

    async def load_active_trades(self, session: AsyncSession):
        """Загрузить активные копитрейды из базы данных"""
        try:
            # Получаем все активные копитрейды
            result = await session.execute(
                select(CopyTrade)
                .where(CopyTrade.is_active == True)
            )
            active_trades = result.scalars().all()

            # Сбрасываем текущие отслеживания
            self.active_trades.clear()
            await self.monitor.stop_monitoring()

            # Добавляем каждый копитрейд в монитор
            for trade in active_trades:
                wallet = trade.wallet_address
                if wallet not in self.active_trades:
                    self.active_trades[wallet] = set()
                    self.monitor.add_leader(wallet)
                self.active_trades[wallet].add(trade)
                self.monitor.add_relationship(wallet, str(trade.id))

            # Запускаем мониторинг если есть активные трейды
            if self.active_trades:
                await self.monitor.start_monitoring()
                logger.info(f"Started monitoring {len(self.active_trades)} wallets")

        except Exception as e:
            logger.error(f"Error loading active trades: {e}")
            raise

    async def process_transaction(self, leader: str, tx_type: str, signature: str, token_address: str, session: AsyncSession):
        """Обработать транзакцию и создать копии для подписчиков"""
        try:
            if leader not in self.active_trades:
                return

            # Получаем все копитрейды для этого лидера
            copy_trades = self.active_trades[leader]

            for trade in copy_trades:
                try:
                    # Проверяем исключенные токены
                    is_excluded = await session.scalar(
                        select(ExcludedToken)
                        .where(ExcludedToken.user_id == trade.user_id)
                        .where(ExcludedToken.token_address == token_address)
                    )
                    if is_excluded:
                        logger.info(f"Token {token_address} is excluded for user {trade.user_id}")
                        continue

                    # Проверяем настройки копирования продаж
                    if tx_type == "SELL" and not trade.copy_sells:
                        logger.info(f"Sell copying is disabled for trade {trade.id}")
                        continue

                    # Создаем запись о транзакции
                    new_transaction = CopyTradeTransaction(
                        copy_trade_id=trade.id,
                        original_signature=signature,
                        token_address=token_address,
                        transaction_type=tx_type,
                        status="PENDING"
                    )
                    session.add(new_transaction)
                    await session.commit()
                    await session.refresh(new_transaction)

                    try:
                        # Получаем информацию о транзакции лидера
                        tx_info = await self.solana_client.get_transaction(signature)
                        if not tx_info:
                            logger.error(f"Failed to get transaction info for {signature}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = "Failed to get transaction info"
                            await session.commit()
                            continue

                        # Получаем сумму транзакции в SOL
                        amount_sol = tx_info.get("amount_sol", 0)
                        
                        # Рассчитываем сумму для копирования
                        copy_amount = amount_sol * (trade.copy_percentage / 100)

                        # Проверяем лимиты
                        if trade.min_amount and copy_amount < trade.min_amount:
                            logger.info(f"Amount {copy_amount} SOL is below minimum {trade.min_amount} SOL")
                            new_transaction.status = "SKIPPED"
                            new_transaction.error = f"Amount below minimum"
                            await session.commit()
                            continue

                        if trade.max_amount and copy_amount > trade.max_amount:
                            copy_amount = trade.max_amount
                            logger.info(f"Amount reduced to maximum {trade.max_amount} SOL")

                        # Проверяем общий лимит
                        if trade.total_amount:
                            total_spent = await session.scalar(
                                select(func.sum(CopyTradeTransaction.amount_sol))
                                .where(CopyTradeTransaction.copy_trade_id == trade.id)
                                .where(CopyTradeTransaction.status == "SUCCESS")
                            ) or 0
                            
                            if total_spent + copy_amount > trade.total_amount:
                                logger.info(f"Total amount limit reached for trade {trade.id}")
                                new_transaction.status = "SKIPPED"
                                new_transaction.error = "Total amount limit reached"
                                await session.commit()
                                continue

                        # Проверяем лимит копий токена
                        if trade.max_copies_per_token:
                            copies_count = await session.scalar(
                                select(func.count(CopyTradeTransaction.id))
                                .where(CopyTradeTransaction.copy_trade_id == trade.id)
                                .where(CopyTradeTransaction.token_address == token_address)
                                .where(CopyTradeTransaction.status == "SUCCESS")
                            ) or 0
                            
                            if copies_count >= trade.max_copies_per_token:
                                logger.info(f"Max copies limit reached for token {token_address}")
                                new_transaction.status = "SKIPPED"
                                new_transaction.error = "Max copies limit reached"
                                await session.commit()
                                continue

                        # Получаем пользователя для проверки баланса
                        user = await session.scalar(
                            select(User).where(User.id == trade.user_id)
                        )
                        if not user or not user.solana_wallet:
                            logger.error(f"User {trade.user_id} not found or no wallet")
                            new_transaction.status = "FAILED"
                            new_transaction.error = "User wallet not found"
                            await session.commit()
                            continue

                        # Проверяем баланс
                        balance = await self.solana_client.get_sol_balance(user.solana_wallet)
                        if balance < copy_amount:
                            logger.error(f"Insufficient balance for user {trade.user_id}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = "Insufficient balance"
                            await session.commit()
                            continue

                        # Выполняем транзакцию
                        if tx_type == "BUY":
                            result = await self.solana_client.buy_token(
                                token_address=token_address,
                                amount_sol=copy_amount,
                                slippage=trade.buy_slippage,
                                compute_unit_price=trade.buy_gas_fee,
                                retries=trade.retry_count,
                                private_key=user.private_key
                            )
                        else:  # SELL
                            result = await self.solana_client.sell_token(
                                token_address=token_address,
                                amount_sol=copy_amount,
                                slippage=trade.sell_slippage,
                                compute_unit_price=trade.sell_gas_fee,
                                retries=trade.retry_count,
                                private_key=user.private_key
                            )

                        if result and result.get("success"):
                            new_transaction.status = "SUCCESS"
                            new_transaction.copied_signature = result.get("signature")
                            new_transaction.amount_sol = copy_amount
                            logger.info(f"Successfully copied transaction {signature} for user {trade.user_id}")
                        else:
                            new_transaction.status = "FAILED"
                            new_transaction.error = result.get("error", "Unknown error")
                            logger.error(f"Failed to copy transaction {signature} for user {trade.user_id}: {new_transaction.error}")

                        await session.commit()

                    except Exception as e:
                        logger.error(f"Error executing transaction: {e}")
                        new_transaction.status = "FAILED"
                        new_transaction.error = str(e)
                        await session.commit()

                except Exception as e:
                    logger.error(f"Error processing copy trade {trade.id}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            raise

    async def add_copy_trade(self, copy_trade: CopyTrade):
        """Добавить новый копитрейд в мониторинг"""
        wallet = copy_trade.wallet_address
        if wallet not in self.active_trades:
            self.active_trades[wallet] = set()
            self.monitor.add_leader(wallet)
        self.active_trades[wallet].add(copy_trade)
        self.monitor.add_relationship(wallet, str(copy_trade.id))

    async def remove_copy_trade(self, copy_trade: CopyTrade):
        """Удалить копитрейд из мониторинга"""
        wallet = copy_trade.wallet_address
        if wallet in self.active_trades:
            self.active_trades[wallet].discard(copy_trade)
            if not self.active_trades[wallet]:
                del self.active_trades[wallet]
                # TODO: Remove leader from monitor 

    async def handle_transaction_with_session(self, leader: str, tx_type: str, signature: str, token_address: str):
        """Handle transaction with database session"""
        try:
            signature_obj = Signature.from_string(signature)
            async with self.session_maker() as session:
                await self.process_transaction(leader, tx_type, signature_obj, token_address, session)
        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            raise