import aiohttp
import asyncio
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import LimitOrder, User
from src.solana_module.transaction_handler import UserTransactionHandler
from src.solana_module.token_info import token_info
from src.services.token_info import TokenInfoService
from src.bot.handlers.buy import _format_price
from solders.signature import Signature
import traceback
import struct

logger = logging.getLogger(__name__)

class AsyncLimitOrders:
    def __init__(self, session_factory, bot):
        """
        Инициализация класса.
        :param session_factory: Фабрика сессий SQLAlchemy для работы с БД
        """
        self.session_factory = session_factory
        self.session = None
        self._running = False
        self.bot = bot

    async def start(self):
        """Инициализация сессии"""
        self._running = True
        logger.info("[LIMIT_ORDERS] Service started")

    async def close(self):
        """Закрытие сессии"""
        self._running = False
        logger.info("[LIMIT_ORDERS] Service stopped")
            
    async def show_success_limit_order(self, session: AsyncSession, order_id: int):
        """Отправить уведомление об успешном лимитном ордере"""
        try:
            stmt = (
                select(LimitOrder)
                .where(LimitOrder.id == order_id)
            )
            result = await session.execute(stmt)
            order = result.unique().scalar_one_or_none()
            
            if not order:
                logger.error("[LIMIT_ORDERS] Order %d not found for success notification", order_id)
                return

            # Получаем информацию о пользователе
            stmt = select(User).where(User.id == order.user_id)
            result = await session.execute(stmt)
            user = result.unique().scalar_one_or_none()
            
            if not user:
                logger.error("[LIMIT_ORDERS] User not found for order %d", order_id)
                return
            
            token_info_service = TokenInfoService()
            token_info = await token_info_service.get_token_info(order.token_address)
            
            if not token_info:
                logger.error("[LIMIT_ORDERS] Token info not found for order %d", order_id)
                return

            # Форматируем числа с фиксированной точностью
            amount = "{:.8f}".format(float(order.amount_sol)) if order.order_type == 'buy' else "{:.8f}".format(float(order.amount_tokens))
            trigger_price = "{:.8f}".format(float(order.trigger_price_usd))
            
            # Отправляем уведомление
            await self.bot.send_message(
                user.telegram_id,
                f"✅ Успешный лимитный ордер #{order_id}\n"
                f"💰 Сумма: {amount} {'SOL' if order.order_type == 'buy' else token_info.symbol}\n"
                f"📉 Триггер: {order.trigger_price_percent}% (${trigger_price})\n"
                f"🔖 Токен: {token_info.symbol}\n"
                f"⚙️ Slippage: {order.slippage}%\n"
                f"🕒 Создан: {order.created_at.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)"
            )
        except Exception as e:
            logger.error("[LIMIT_ORDERS] Error sending success notification: %s", str(e))
            logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())
    
    async def error_limit_order(self, session: AsyncSession, order_id: int):
        """Отправить уведомление об ошибочном лимитном ордере"""
        try:
            stmt = (
                select(LimitOrder)
                .where(LimitOrder.id == order_id)
            )
            result = await session.execute(stmt)
            order = result.unique().scalar_one_or_none()

            if not order:
                logger.error("[LIMIT_ORDERS] Order %d not found for error notification", order_id)
                return

            # Получаем информацию о пользователе
            stmt = select(User).where(User.id == order.user_id)
            result = await session.execute(stmt)
            user = result.unique().scalar_one_or_none()

            if not user:
                logger.error("[LIMIT_ORDERS] User not found for order %d", order_id)
                return
            
            token_info_service = TokenInfoService()

            # Получаем информацию о токене
            token_info = await token_info_service.get_token_info(order.token_address)
            
            if not token_info:
                logger.error("[LIMIT_ORDERS] Token info not found for order %d", order_id)
                return

            # Форматируем числа с фиксированной точностью
            amount = "{:.8f}".format(float(order.amount_sol)) if order.order_type == 'buy' else "{:.8f}".format(float(order.amount_tokens))
            trigger_price = "{:.8f}".format(float(order.trigger_price_usd))
            
            try:
                # Отправляем уведомление
                await self.bot.send_message(
                    user.telegram_id,
                    f"❌ Ошибочный лимитный ордер #{order_id}\n"
                    f"💰 Сумма: {amount} {'SOL' if order.order_type == 'buy' else token_info.symbol}\n"
                    f"📉 Триггер: {order.trigger_price_percent}% (${trigger_price})\n"
                    f"🔖 Токен: {token_info.symbol}\n"
                    f"⚙️ Slippage: {order.slippage}%\n"
                    f"🕒 Создан: {order.created_at.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)"
                )
            except Exception as e:
                logger.error("[LIMIT_ORDERS] Failed to send error notification: %s", str(e))
                logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())
        except Exception as e:
            logger.error("[LIMIT_ORDERS] Error in error_limit_order: %s", str(e))
            logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())

    async def execute_order(self, order: LimitOrder, session: AsyncSession) -> bool:
        """
        Выполняет лимитный ордер
        :param order: Объект лимитного ордера
        :param session: Сессия SQLAlchemy
        :return: True если ордер успешно выполнен, False в противном случае
        """
        try:
            logger.info("[LIMIT_ORDERS] Starting execution of order #%d", order.id)
            
            # Получаем пользователя
            stmt = select(User).where(User.id == order.user_id)
            result = await session.execute(stmt)
            user = result.unique().scalar_one_or_none()
            
            if not user or not user.private_key:
                logger.error("[LIMIT_ORDERS] User not found or no private key for order #%d", order.id)
                return False

            # Создаем обработчик транзакций
            tx_handler = UserTransactionHandler(
                private_key_str=user.private_key,
                compute_unit_price=1000000  # Default compute unit price
            )
            
            logger.info("[LIMIT_ORDERS] Created transaction handler for order #%d", order.id)

            try:
                # Выполняем транзакцию в зависимости от типа ордера
                if order.order_type == 'buy':
                    logger.info(
                        "[LIMIT_ORDERS] Executing buy order #%d: %.8f SOL for token %s", 
                        order.id, order.amount_sol, order.token_address
                    )
                    tx_hash = await tx_handler.buy_token(
                        token_address=order.token_address,
                        amount_sol=order.amount_sol,
                        slippage=order.slippage
                    )
                else:  # sell
                    logger.info(
                        "[LIMIT_ORDERS] Executing sell order #%d: %.8f tokens of %s", 
                        order.id, order.amount_tokens, order.token_address
                    )
                    tx_hash = await tx_handler.sell_token(
                        token_address=order.token_address,
                        sell_percentage=order.amount_tokens,
                        slippage=order.slippage
                    )

                if isinstance(tx_hash, Signature):
                    tx_hash = str(tx_hash)
                    # Обновляем статус ордера
                    order.status = 'executed'
                    order.transaction_hash = tx_hash
                    await session.commit()
                    logger.info("[LIMIT_ORDERS] Order #%d executed successfully. Hash: %s", order.id, tx_hash)
                    await self.show_success_limit_order(session, order.id)
                    return True
                
                logger.error("[LIMIT_ORDERS] Transaction failed for order #%d", order.id)
                order.status = 'error'
                await session.commit()
                await self.error_limit_order(session, order.id)
                return False

            except Exception as e:
                logger.error("[LIMIT_ORDERS] Transaction error for order #%d: %s", order.id, str(e))
                logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())
                order.status = 'error'
                await session.commit()
                await self.error_limit_order(session, order.id)
                return False

        except Exception as e:
            logger.error("[LIMIT_ORDERS] Error executing order #%d: %s", order.id, str(e))
            logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())
            return False

    async def check_and_execute_orders(self):
        """
        Проверяет все активные ордера и выполняет те, которые достигли целевой цены
        """
        async with self.session_factory() as session:
            try:
                # Получаем все активные ордера
                stmt = select(LimitOrder).where(LimitOrder.status == 'active')
                result = await session.execute(stmt)
                active_orders = result.unique().scalars().all()
                
                logger.info("[LIMIT_ORDERS] Checking active orders. Found %d orders", len(active_orders))

                for order in active_orders:
                    try:
                        # Получаем текущую цену токена используя token_info
                        token_info_result = token_info(order.token_address)
                        if not token_info_result or 'priceUsd' not in token_info_result:
                            logger.warning("[LIMIT_ORDERS] Failed to get price for token %s", order.token_address)
                            continue

                        current_price = float(token_info_result['priceUsd'])
                        if current_price <= 0:
                            logger.warning("[LIMIT_ORDERS] Invalid price (<=0) for token %s", order.token_address)
                            continue

                        logger.info(
                            "[LIMIT_ORDERS] Order #%d: Type=%s, Token=%s, Current Price=%.8f, Target Price=%.8f",
                            order.id, order.order_type, order.token_address, 
                            current_price, order.trigger_price_usd
                        )

                        # Проверяем условие срабатывания в зависимости от типа ордера
                        should_execute = False
                        if order.order_type == 'buy' and current_price <= order.trigger_price_usd:
                            should_execute = True
                            logger.info(
                                "[LIMIT_ORDERS] Buy order #%d triggered: Current price %.8f <= Target price %.8f",
                                order.id, current_price, order.trigger_price_usd
                            )
                        elif order.order_type == 'sell' and current_price >= order.trigger_price_usd:
                            should_execute = True
                            logger.info(
                                "[LIMIT_ORDERS] Sell order #%d triggered: Current price %.8f >= Target price %.8f",
                                order.id, current_price, order.trigger_price_usd
                            )

                        if should_execute:
                            success = await self.execute_order(order, session)
                            if success:
                                logger.info("[LIMIT_ORDERS] Successfully executed order #%d", order.id)
                            else:
                                logger.error("[LIMIT_ORDERS] Failed to execute order #%d", order.id)
                                order.status = 'error'
                                await session.commit()
                                await self.error_limit_order(session, order.id)

                    except Exception as e:
                        logger.error("[LIMIT_ORDERS] Error processing order #%d: %s", order.id, str(e))
                        logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())
                        order.status = 'error'
                        await session.commit()
                        await self.error_limit_order(session, order.id)

            except Exception as e:
                logger.error("[LIMIT_ORDERS] Error checking orders: %s", str(e))
                logger.error("[LIMIT_ORDERS] Traceback: %s", traceback.format_exc())

            logger.info("[LIMIT_ORDERS] Price check cycle completed. Waiting 15 seconds...")

    async def monitor_prices(self, interval: int = 20):
        """
        Асинхронный цикл мониторинга цен.
        :param interval: Интервал проверки цен (в секундах).
        """
        self._running = True
        logger.info("[LIMIT_ORDERS] Starting limit orders monitoring with interval %d seconds...", interval)

        while self._running:
            logger.info("[LIMIT_ORDERS] Running price check cycle...")
            await self.check_and_execute_orders()
            logger.info("[LIMIT_ORDERS] Price check cycle completed. Waiting %d seconds...", interval)
            await asyncio.sleep(interval)

        logger.info("[LIMIT_ORDERS] Limit orders monitoring stopped")

    async def stop(self):
        """
        Останавливает цикл мониторинга.
        """
        self._running = False
        logger.info("Stopping limit orders monitoring...")
        

def example_action_factory(target_price: float, action_type: str):
    """
    Возвращает функцию действия пользователя.
    :param target_price: Целевая цена.
    :param action_type: Тип действия ("buy" или "sell").
    :return: Функция, которую нужно вызвать при обновлении цены.
    """
    def action(current_price: float):
        if action_type == "buy" and current_price <= target_price:
            print(f"Покупка токена по цене {current_price} (цель: {target_price})")
        elif action_type == "sell" and current_price >= target_price:
            print(f"Продажа токена по цене {current_price} (цель: {target_price})")
    return action


async def main():
    # Создаём экземпляр класса
    limit_orders = AsyncLimitOrders(lambda: AsyncSession())

    # Запускаем сессию
    await limit_orders.start()

    # Запускаем мониторинг цен в отдельной задаче
    monitor_task = asyncio.create_task(limit_orders.monitor_prices(interval=20))

    # Через некоторое время (60 секунд) останавливаем мониторинг
    await asyncio.sleep(60)
    limit_orders.stop()

    # Дожидаемся завершения задачи мониторинга
    await monitor_task

    # Закрываем сессию
    await limit_orders.close()


if __name__ == "__main__":
    asyncio.run(main())