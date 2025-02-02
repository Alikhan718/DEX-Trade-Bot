import time
import traceback

import logging
from typing import Dict, Set, Optional

import requests
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from solders.signature import Signature
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from src.services.token_info import TokenInfoService
from src.bot.handlers.buy import _format_price
from .solana_monitor import SolanaMonitor
from src.database.models import CopyTrade, ExcludedToken, CopyTradeTransaction, User
from .solana_client import SolanaClient, LAMPORTS_PER_SOL
from .transaction_handler import UserTransactionHandler
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)


class CopyTradeManager:
    def __init__(self, solana_client: SolanaClient, bot: Bot):
        self.solana_client = solana_client
        self.monitor = SolanaMonitor()
        self.active_trades: Dict[str, Set[CopyTrade]] = {}  # wallet -> set of copy trades
        self.bot = bot

    async def send_notification(self, user_id: int, message: str, parse_mode: str = "HTML"):
        """Send notification to user via Telegram bot"""
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=parse_mode
            )
            logger.info(f"[MANAGER] Notification sent to user {user_id}")
        except TelegramAPIError as e:
            logger.error(f"[MANAGER] Failed to send notification to user {user_id}: {e}")

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
            traceback.print_exc()
            raise

    async def process_transaction(self, leader: str, tx_type: str, signature: str, token_address: str,
                                  session: AsyncSession):
        token_address = str(token_address)
        """Обработать транзакцию и создать копии для подписчиков"""
        try:
            transaction_start_time = time.time()
            logger.info(f"[MANAGER] Processing transaction from leader {leader}")
            logger.info(
                f"[MANAGER] Transaction details - Type: {tx_type}, Signature: {signature}, Token: {token_address}")

            if leader not in self.active_trades:
                logger.info(f"[MANAGER] No active trades found for leader {leader}")
                return

            # Получаем все копитрейды для этого лидера
            copy_trades = self.active_trades[leader]
            logger.info(f"[MANAGER] Found {len(copy_trades)} active copy trades for leader {leader}")

            # Convert signature string to Signature object
            try:
                signature_obj = Signature.from_string(signature)
                logger.info(f"[MANAGER] Successfully converted signature to Signature object")
            except Exception as e:
                logger.error(f"[MANAGER] Failed to convert signature to Signature object: {str(e)}")
                return

            # Если token_address не передан, пытаемся получить его из транзакции
            if not token_address:
                try:
                    tx_info = await self.solana_client.get_transaction(signature_obj)
                    logger.info(f"[MANAGER] Transaction info: {tx_info}")
                    if tx_info:
                        # Get mint address from accounts[2] (third account in instruction)
                        token_address = tx_info.get("token_address")
                        logger.info(f"[MANAGER] Extracted token address from transaction: {token_address}")
                    if not token_address:
                        logger.error(f"[MANAGER] Failed to get token address from transaction {signature}")
                        return
                except Exception as e:
                    logger.error(f"[MANAGER] Error extracting token address: {str(e)}")
                    return

            for trade in copy_trades:
                try:
                    logger.info(f"[MANAGER] Processing copy trade {trade.id} for user {trade.user_id}")

                    # Get user for notifications
                    user = await session.scalar(
                        select(User).where(User.id == trade.user_id)
                    )
                    if not user:
                        logger.error(f"[MANAGER] User {trade.user_id} not found")
                        continue

                    # Проверяем исключенные токены
                    is_excluded = await session.scalar(
                        select(ExcludedToken)
                        .where(ExcludedToken.user_id == trade.user_id)
                        .where(ExcludedToken.token_address == str(token_address))
                    )
                    if is_excluded:
                        logger.info(f"[MANAGER] Token {token_address} is excluded for user {trade.user_id}")
                        await self.send_notification(
                            user.telegram_id,
                            f"ℹ️ Пропущена транзакция {tx_type} для токена <code>{token_address}</code>\n"
                            f"Причина: Токен в списке исключений"
                        )
                        continue

                    # Проверяем настройки копирования продаж
                    if tx_type == "SELL" and not trade.copy_sells:
                        logger.info(f"[MANAGER] Sell copying is disabled for trade {trade.id}")
                        await self.send_notification(
                            user.telegram_id,
                            f"ℹ️ Пропущена транзакция SELL для токена <code>{token_address}</code>\n"
                            f"Причина: Копирование продаж отключено"
                        )
                        continue

                    # Создаем запись о транзакции
                    new_transaction = CopyTradeTransaction(
                        copy_trade_id=trade.id,
                        original_signature=signature,
                        token_address=str(token_address),
                        transaction_type=tx_type,
                        status="PENDING"
                    )
                    start_message = (
                        f"🔄 Начинаю копировать транзакцию {tx_type}\n\n"
                        f"🏦 Кошелек лидера: <code>{leader}</code>\n"
                        f"💎 Токен: <code>{token_address}</code>\n"
                    )
                    await self.send_notification(user.telegram_id, start_message)

                    session.add(new_transaction)
                    await session.commit()
                    await session.refresh(new_transaction)
                    logger.info(f"[MANAGER] Created new transaction record {new_transaction.id}")
                    leader_token_info = None
                    leader_price_usd = None
                    try:
                        # Получаем информацию о транзакции лидера
                        leader_token_info = await self.solana_client.token_info(token_address)
                        if leader_token_info:
                            platform_id = leader_token_info['platformId']
                            pool_id = leader_token_info['poolId']
                            req = requests.get(f"https://api.coinmarketcap.com/kline/v3/k-line/candles/{str(platform_id)}/{str(pool_id)}?type=1m&countBack=1")
                            leader_price_usd = req.json()['data'][-1]['close']
                        tx_info = await self.solana_client.get_transaction(signature_obj)
                        if not tx_info:
                            logger.error(f"[MANAGER] Failed to get transaction info for {signature}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = "Failed to get transaction info"
                            await session.commit()
                            continue
                        logger.info(f"[MANAGER] Retrieved transaction info")
                        token_info_service = TokenInfoService()
                        sol_price_usd = await token_info_service.get_token_info('So11111111111111111111111111111111111111112')

                        if tx_type == "SELL":
                            # Для SELL транзакций нам нужно получить баланс токенов пользователя
                            try:
                                private_key = user.private_key
                                user_client = SolanaClient(
                                    compute_unit_price=self.solana_client.compute_unit_price,
                                    private_key=private_key
                                )
                                user_client.load_keypair()
                                token_balance = await user_client.get_token_balance(Pubkey.from_string(token_address))
                                logger.info(f"[MANAGER] User token balance: {token_balance}")

                                if token_balance <= 0:
                                    logger.error(f"[MANAGER] User has no tokens to sell")
                                    new_transaction.status = "FAILED"
                                    new_transaction.error = "No tokens to sell"
                                    await session.commit()
                                    continue

                                # Рассчитываем количество токенов для продажи
                                token_amount = token_balance * (trade.copy_percentage / 100)
                                logger.info(
                                    f"[MANAGER] Calculated token amount to sell: {token_amount} ({trade.copy_percentage}%)")


                                # Проверяем минимальную сумму в SOL после конвертации
                                token_info = await token_info_service.get_token_info(token_address)
                                # Get token price before transaction
                                token_price_sol = token_info.price_usd / sol_price_usd.price_usd
                                estimated_sol = token_amount * token_price_sol

                                if trade.min_amount and estimated_sol < trade.min_amount:
                                    logger.info(
                                        f"[MANAGER] Estimated SOL amount {estimated_sol} is below minimum {trade.min_amount} SOL")
                                    new_transaction.status = "SKIPPED"
                                    new_transaction.error = f"Amount below minimum"
                                    await session.commit()
                                    continue

                                if trade.max_amount and estimated_sol > trade.max_amount:
                                    # Корректируем количество токенов для продажи
                                    token_amount = trade.max_amount / token_price_sol
                                    logger.info(
                                        f"[MANAGER] Token amount reduced to {token_amount} to match maximum SOL amount")

                                copy_amount = token_amount  # Для SELL это количество токенов

                            except Exception as e:
                                logger.error(f"[MANAGER] Error calculating token amount: {str(e)}")
                                new_transaction.status = "FAILED"
                                new_transaction.error = f"Failed to calculate token amount: {str(e)}"
                                await session.commit()
                                continue
                        else:
                            # Для BUY транзакций оставляем текущую логику
                            # Получаем сумму транзакции в SOL (уже в lamports)
                            amount_sol = tx_info.get("amount_sol", 0)
                            if amount_sol == 0:
                                logger.error(f"[MANAGER] Failed to get transaction amount for {signature}")
                                new_transaction.status = "FAILED"
                                new_transaction.error = "Failed to get transaction amount"
                                await session.commit()
                                continue

                            # Конвертируем в SOL
                            amount_sol = amount_sol / LAMPORTS_PER_SOL
                            logger.info(f"[MANAGER] Original transaction amount: {amount_sol} SOL")

                            # Рассчитываем сумму для копирования
                            copy_amount = amount_sol * (trade.copy_percentage / 100)
                            logger.info(
                                f"[MANAGER] Calculated copy amount: {copy_amount} SOL ({trade.copy_percentage}%)")

                        # Проверяем общий лимит
                        if trade.total_amount:
                            total_spent = await session.scalar(
                                select(func.sum(CopyTradeTransaction.amount_sol))
                                .where(CopyTradeTransaction.copy_trade_id == trade.id)
                                .where(CopyTradeTransaction.status == "SUCCESS")
                            ) or 0

                            logger.info(f"[MANAGER] Total amount spent so far: {total_spent} SOL")
                            if total_spent + copy_amount > trade.total_amount:
                                logger.info(f"[MANAGER] Total amount limit reached for trade {trade.id}")
                                new_transaction.status = "SKIPPED"
                                new_transaction.error = "Total amount limit reached"
                                await session.commit()
                                continue

                        # Проверяем лимит копий токена
                        if trade.max_copies_per_token:
                            copies_count = await session.scalar(
                                select(func.count(CopyTradeTransaction.id))
                                .where(CopyTradeTransaction.copy_trade_id == trade.id)
                                .where(CopyTradeTransaction.token_address == str(token_address))
                                .where(CopyTradeTransaction.status == "SUCCESS")
                            ) or 0

                            logger.info(f"[MANAGER] Current copies count for token: {copies_count}")
                            if copies_count >= trade.max_copies_per_token:
                                logger.info(f"[MANAGER] Max copies limit reached for token {token_address}")
                                new_transaction.status = "SKIPPED"
                                new_transaction.error = "Max copies limit reached"
                                await session.commit()
                                continue

                        # Получаем пользователя для проверки баланса
                        user = await session.scalar(
                            select(User).where(User.id == trade.user_id)
                        )
                        if not user or not user.solana_wallet:
                            logger.error(f"[MANAGER] User {trade.user_id} not found or no wallet")
                            new_transaction.status = "FAILED"
                            new_transaction.error = "User wallet not found"
                            await session.commit()
                            continue

                        # Получаем private key пользователя
                        if not private_key:
                            logger.error(f"[MANAGER] No private key found for user {trade.user_id}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = "No private key found"
                            await session.commit()
                            continue

                        logger.info(f"[MANAGER] Retrieved private key for user {trade.user_id}")
                        logger.debug(f"[MANAGER] Private key string length: {len(private_key)}")

                        # Создаем новый экземпляр клиента с private key пользователя
                        try:
                            logger.info(f"[MANAGER] Creating new SolanaClient instance for user {trade.user_id}")

                            # Проверяем формат private key
                            try:
                                key_parts = private_key.split(',')
                                logger.debug(f"[MANAGER] Split private key into {len(key_parts)} parts")

                                # Пробуем сконвертировать в числа
                                key_bytes = [int(i) for i in key_parts]
                                logger.debug(f"[MANAGER] Converted to bytes array with length: {len(key_bytes)}")

                                if len(key_bytes) != 64:
                                    raise ValueError(f"Invalid key length: {len(key_bytes)} (expected 64)")

                            except Exception as e:
                                logger.error(f"[MANAGER] Invalid private key format: {str(e)}")
                                new_transaction.status = "FAILED"
                                new_transaction.error = f"Invalid private key format: {str(e)}"
                                await session.commit()
                                continue

                            # Проверяем что ключ успешно загружен
                            try:
                                payer = user_client.load_keypair()
                                logger.info(
                                    f"[MANAGER] Successfully loaded keypair for user {trade.user_id}. Public key: {payer.pubkey()}")

                                # Проверяем что публичный ключ соответствует адресу кошелька
                                if str(payer.pubkey()) != user.solana_wallet:
                                    logger.error(
                                        f"[MANAGER] Keypair public key {payer.pubkey()} does not match wallet address {user.solana_wallet}")
                                    new_transaction.status = "FAILED"
                                    new_transaction.error = "Invalid keypair"
                                    await session.commit()
                                    continue

                            except Exception as e:
                                logger.error(f"[MANAGER] Failed to load keypair: {str(e)}")
                                logger.error(f"[MANAGER] Error type: {type(e).__name__}")
                                new_transaction.status = "FAILED"
                                new_transaction.error = f"Failed to load keypair: {str(e)}"
                                await session.commit()
                                continue

                        except Exception as e:
                            logger.error(f"[MANAGER] Failed to create SolanaClient for user {trade.user_id}: {str(e)}")
                            logger.error(f"[MANAGER] Error type: {type(e).__name__}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = f"Failed to create client: {str(e)}"
                            await session.commit()
                            continue

                        # Проверяем баланс используя клиент пользователя
                        try:
                            balance = await user_client.get_sol_balance(user.solana_wallet)
                            logger.info(f"[MANAGER] User balance: {balance} SOL")
                            if balance < copy_amount:
                                logger.error(f"[MANAGER] Insufficient balance for user {trade.user_id}")
                                new_transaction.status = "FAILED"
                                new_transaction.error = "Insufficient balance"
                                await session.commit()
                                continue
                        except Exception as e:
                            logger.error(f"[MANAGER] Failed to get balance for user {trade.user_id}: {str(e)}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = f"Failed to get balance: {str(e)}"
                            await session.commit()
                            continue

                        # Получаем адреса кривых

                        mint = str(token_address)  # token_address уже является Pubkey
                        logger.info(f"[MANAGER] Using mint address: {mint}")
                        th = UserTransactionHandler(private_key, user_client.compute_unit_price)

                        # Выполняем транзакцию
                        logger.info(f"[MANAGER] Executing {tx_type} transaction for user {trade.user_id}")
                        try:
                            if tx_type == "BUY":
                                result = await th.buy_token(
                                    token_address=mint,
                                    amount_sol=copy_amount,
                                    slippage=trade.buy_slippage  # Convert percentage to decimal
                                )
                            else:  # SELL
                                result = await th.sell_token(
                                    token_address=mint,
                                    amount_tokens=copy_amount,  # Здесь copy_amount это количество токенов
                                    sell_percentage=trade.copy_percentage,  # Здесь copy_percentage
                                    slippage=trade.sell_slippage
                                )

                            # Если результат это Signature - значит транзакция успешна
                            if isinstance(result, Signature):
                                execution_time = time.time() - transaction_start_time
                                copied_signature = str(result)
                                new_transaction.status = "SUCCESS"
                                new_transaction.copied_signature = copied_signature
                                new_transaction.amount_sol = copy_amount
                                logger.info(
                                    f"[MANAGER] Successfully copied transaction {signature} for user {trade.user_id}")
                                logger.info(f"[MANAGER] Copy transaction signature: {copied_signature}")
                                token_info = await user_client.token_info(token_address)
                                price_usd = token_info['priceUsd']
                                # Send success notification
                                token_price_sol = float(price_usd) / sol_price_usd.price_usd

                                success_message = (
                                    f"✅ Успешно скопирована транзакция {tx_type}\n\n"
                                    f"🏦 Кошелек лидера: <code>{leader}</code>\n\n"
                                    f"💵 Цена токена лидера (На момент покупки): {_format_price(leader_price_usd)} SOL\n"
                                    f"💵 Цена вашего токена (На момент покупки): {_format_price(price_usd)} SOL\n"
                                    f"💎 Токен: <code>{token_address}</code>\n"
                                    f"💰 Сумма: {_format_price(amount_sol)} SOL\n"
                                    f"🔢 Количество токенов: {_format_price(amount_sol / token_price_sol)}\n"
                                    f"⏱ Время выполнения: {execution_time:.2f} сек\n"
                                    f"🔗 Транзакция: <a href='https://solscan.io/tx/{copied_signature}'>Solscan</a>"
                                )
                                await self.send_notification(user.telegram_id, success_message)
                                await session.commit()

                            else:
                                # Если результат это словарь с ошибкой
                                error_message = (result or {}).get("error", "Transaction execution failed")
                                logger.error(f"[MANAGER] Transaction failed for user {trade.user_id}: {error_message}")
                                new_transaction.status = "FAILED"
                                new_transaction.error = error_message
                                await session.commit()

                                # Send failure notification
                                failure_message = (
                                    f"❌ Ошибка при копировании транзакции {tx_type}\n\n"
                                    f"🏦 Кошелек лидера: <code>{leader}</code>\n"
                                    f"💎 Токен: <code>{token_address}</code>\n"
                                    f"💰 Сумма: {copy_amount:.4f} SOL\n"
                                    f"❗️ Причина: {error_message}"
                                )
                                await self.send_notification(user.telegram_id, failure_message)

                        except Exception as e:
                            logger.error(f"[MANAGER] Error executing transaction: {str(e)}")
                            logger.error(f"[MANAGER] Error type: {type(e).__name__}")
                            import traceback
                            logger.error(f"[MANAGER] Traceback: {traceback.format_exc()}")
                            new_transaction.status = "FAILED"
                            new_transaction.error = str(e)
                            await session.commit()

                            # Send error notification
                            error_message = (
                                f"❌ Ошибка при копировании транзакции {tx_type}\n\n"
                                f"🏦 Кошелек лидера: <code>{leader}</code>\n"
                                f"💎 Токен: <code>{token_address}</code>\n"
                                f"💰 Сумма: {copy_amount:.4f} SOL\n"
                                f"❗️ Причина: {str(e)}"
                            )
                            await self.send_notification(user.telegram_id, error_message)

                    except Exception as e:
                        logger.error(f"[MANAGER] Error executing transaction: {str(e)}")
                        logger.error(f"[MANAGER] Error type: {type(e).__name__}")
                        import traceback
                        logger.error(f"[MANAGER] Traceback: {traceback.format_exc()}")
                        new_transaction.status = "FAILED"
                        new_transaction.error = str(e)
                        await session.commit()

                        # Send error notification
                        error_message = (
                            f"❌ Ошибка при копировании транзакции {tx_type}\n\n"
                            f"🏦 Кошелек лидера: <code>{leader}</code>\n"
                            f"💎 Токен: <code>{token_address}</code>\n"
                            f"💰 Сумма: {copy_amount:.4f} SOL\n"
                            f"❗️ Причина: {str(e)}"
                        )
                        await self.send_notification(user.telegram_id, error_message)

                except Exception as e:
                    logger.error(f"[MANAGER] Error processing copy trade {trade.id}: {str(e)}")
                    logger.error(f"[MANAGER] Error type: {type(e).__name__}")
                    import traceback
                    logger.error(f"[MANAGER] Traceback: {traceback.format_exc()}")
                    continue

        except Exception as e:
            logger.error(f"[MANAGER] Error processing transaction: {str(e)}")
            logger.error(f"[MANAGER] Error type: {type(e).__name__}")
            import traceback
            logger.error(f"[MANAGER] Traceback: {traceback.format_exc()}")
            raise

    async def add_copy_trade(self, copy_trade: CopyTrade):
        """Добавить новый копитрейд в мониторинг"""
        wallet = copy_trade.wallet_address
        if wallet not in self.active_trades:
            self.active_trades[wallet] = set()
            self.monitor.add_leader(wallet)
        self.active_trades[wallet].add(copy_trade)
        self.monitor.add_relationship(wallet, str(copy_trade.id))
        await self.monitor.start_monitoring()

    async def remove_copy_trade(self, copy_trade: CopyTrade):
        """Удалить копитрейд из мониторинга"""
        wallet = copy_trade.wallet_address
        if wallet in self.active_trades:
            self.active_trades[wallet].discard(copy_trade)
            self.monitor.remove_leader(wallet)
            if not self.active_trades[wallet]:
                del self.active_trades[wallet]
        await self.monitor.start_monitoring()


    async def handle_transaction_with_session(self, leader: str, tx_type: str, signature: str,
                                              token_address: Optional[str]):
        """Handle a transaction with a database session."""
        try:
            # Convert signature string to Signature object
            signature_obj = Signature.from_string(signature)

            async with self.session_maker() as session:
                # Get transaction info
                tx_info = await self.solana_client.get_transaction(signature_obj)
                if not tx_info:
                    logger.error(f"Failed to get transaction info for {signature}")
                    return

                await self.process_transaction(leader, tx_type, signature_obj, token_address, session)
        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            raise