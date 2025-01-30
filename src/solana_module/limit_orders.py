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
        self.url = "https://public-api.birdeye.so/public/price"
        self.headers = {"X-API-KEY": "f5b0a449b5914cf3bc0e1238db0a5b3f"}
        self.bot = bot

    async def start(self):
        """Инициализация HTTP сессии"""
        self.session = aiohttp.ClientSession()
        logger.info("HTTP session initialized")

    async def close(self):
        """Закрытие HTTP сессии"""
        if self.session:
            await self.session.close()
            logger.info("HTTP session closed")
            
    async def show_success_limit_order(self, session: AsyncSession, order_id: int):
        """Отправить уведомление об успешном лимитном ордере"""
        stmt = (
            select(LimitOrder)
            .where(LimitOrder.id == order_id)
        )
        result = await session.execute(stmt)
        order = result.unique().scalar_one_or_none()
        
        logger.info(f"Sending success limit order notification for order {order_id}")

        if not order:
            return

        # Получаем информацию о пользователе
        stmt = select(User).where(User.id == order.user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()
        
        logger.info(f"User ID: {user.telegram_id}")

        if not user:
            return
        
        token_info_service = TokenInfoService()

        # Получаем информацию о токене
        token_info = await token_info_service.get_token_info(order.token_address)
        
        logger.info(f"Token ID: {token_info.address}")
        
        if not token_info:
            return
        # Отправляем уведомление
        await self.bot.send_message(
            user.telegram_id,
            f"✅ Успешный лимитный ордер #{order_id}\n"
            f"💰 Сумма: {_format_price(order.amount_sol)} SOL\n"
            f"📉 Триггер: {order.trigger_price_percent}% (${_format_price(order.trigger_price_usd)})\n"
            f"🔖 Токен: {token_info.symbol}\n"
            f"⚙️ Slippage: {order.slippage}%\n"
            f"🕒 Создан: {order.created_at.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)"
        )
    
    async def error_limit_order(self, session: AsyncSession, order_id: int):
        """Отправить уведомление об ошибочном лимитном ордере"""
        stmt = (
            select(LimitOrder)
            .where(LimitOrder.id == order_id)
        )
        result = await session.execute(stmt)
        order = result.unique().scalar_one_or_none()

        if not order:
            return

        # Получаем информацию о пользователе
        stmt = select(User).where(User.id == order.user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            return
        
        token_info_service = TokenInfoService()

        # Получаем информацию о токене
        token_info = await token_info_service.get_token_info(order.token_address)
        
        if not token_info:
            return
        
        # Отправляем уведомление
        await self.bot.send_message(
            user.telegram_id,
            f"❌ Ошибочный лимитный ордер #{order_id}\n"
            f"💰 Сумма: {_format_price(order.amount_sol)} SOL\n"
            f"📉 Триггер: {order.trigger_price_percent}% (${_format_price(order.trigger_price_usd)})\n"
            f"🔖 Токен: {token_info.symbol}\n"
            f"⚙️ Slippage: {order.slippage}%\n"
            f"🕒 Создан: {order.created_at.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)"
        )

    async def execute_order(self, order: LimitOrder, session: AsyncSession) -> bool:
        """
        Выполняет лимитный ордер
        :param order: Объект лимитного ордера
        :param session: Сессия SQLAlchemy
        :return: True если ордер успешно выполнен, False в противном случае
        """
        try:
            # Получаем пользователя
            stmt = select(User).where(User.id == order.user_id)
            result = await session.execute(stmt)
            user = result.unique().scalar_one_or_none()
            
            if not user or not user.private_key:
                logger.error(f"User not found or no private key for order {order.id}")
                return False

            # Создаем обработчик транзакций
            tx_handler = UserTransactionHandler(
                private_key_str=user.private_key,
                compute_unit_price=1000000  # Default compute unit price
            )

            # Выполняем транзакцию в зависимости от типа ордера
            if order.order_type == 'buy':
                tx_hash = await tx_handler.buy_token(
                    token_address=order.token_address,
                    amount_sol=order.amount_sol,
                    slippage=order.slippage
                )
            else:  # sell
                tx_hash = await tx_handler.sell_token(
                    token_address=order.token_address,
                    amount_tokens=order.amount_tokens,
                    slippage=order.slippage
                )
                
            if isinstance(tx_hash, Signature):
                tx_hash = str(tx_hash)
                # Обновляем статус ордера
                order.status = 'executed'
                order.transaction_hash = tx_hash
                await session.commit()
                logger.info(f"Order {order.id} executed successfully. Hash: {tx_hash}")
                await self.show_success_limit_order(session, order.id)
                return True
            order.status = 'error'
            await session.commit()
            await self.error_limit_order(session, order.id)
            logger.error(f"Failed to execute order {order.id}")
            return False

        except Exception as e:
            order.status = 'error'
            await session.commit()
            await self.error_limit_order(session, order.id)
            logger.error(f"Error executing order {order.id}: {str(e)}")
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

                for order in active_orders:
                    # Получаем текущую цену токена
                    current_price = float(token_info(order.token_address)['priceUsd'])
                    if current_price is None:
                        continue

                    # Проверяем условие срабатывания в зависимости от типа ордера
                    should_execute = False
                    if order.order_type == 'buy' and current_price <= order.trigger_price_usd:
                        should_execute = True
                        logger.info(f"Buy order {order.id} triggered at price {current_price} <= {order.trigger_price_usd}")
                    elif order.order_type == 'sell' and current_price >= order.trigger_price_usd:
                        should_execute = True
                        logger.info(f"Sell order {order.id} triggered at price {current_price} >= {order.trigger_price_usd}")

                    if should_execute:
                        await self.execute_order(order, session)

            except Exception as e:
                logger.error(f"Error checking orders: {str(e)}")

    async def monitor_prices(self, interval: int = 20):
        """
        Асинхронный цикл мониторинга цен.
        :param interval: Интервал проверки цен (в секундах).
        """
        self._running = True
        logger.info("Starting limit orders monitoring...")

        while self._running:
            await self.check_and_execute_orders()
            await asyncio.sleep(interval)

        logger.info("Limit orders monitoring stopped")

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