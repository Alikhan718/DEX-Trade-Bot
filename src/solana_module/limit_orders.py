import aiohttp
import asyncio
from typing import Callable, Optional


class AsyncLimitOrders:
    def __init__(self, token: str):
        """
        Инициализация класса.
        :param token: Идентификатор токена для отслеживания.
        """
        self.token = token
        self.url = "https://api.coinmarketcap.com/dexer/v3/dexer/search/main-site"
        self.users = []  # Список пользователей и их настроек
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = False

    async def start(self):
        """
        Асинхронный метод для инициализации (например, открытия ClientSession).
        """
        self.session = aiohttp.ClientSession()

    async def close(self):
        """
        Асинхронный метод для освобождения ресурсов (например, закрытия ClientSession).
        """
        if self.session:
            await self.session.close()

    async def fetch_token_info(self) -> Optional[float]:
        """
        Асинхронно получает текущую цену токена.
        :return: Цена токена (float) или None в случае ошибки.
        """
        if not self.session:
            raise RuntimeError("Сессия не запущена. Сначала вызовите метод 'start()'.")

        params = {
            "keyword": self.token,
            "all": "false"
        }
        headers = {
            "User-Agent": f"Custom/{int(asyncio.get_event_loop().time())}"
        }

        try:
            async with self.session.get(self.url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()

                    # Проверяем, что нужные ключи присутствуют
                    if (
                        "data" in data and
                        "pairs" in data["data"] and
                        len(data["data"]["pairs"]) > 0 and
                        "priceUsd" in data["data"]["pairs"][0]
                    ):
                        return float(data["data"]["pairs"][0]["priceUsd"])
                    else:
                        print("Неверный формат ответа API:", data)
                        return None
                else:
                    print(f"Ошибка API: {response.status}, {await response.text()}")
                    return None
        except aiohttp.ClientError as e:
            print(f"Сетевая ошибка: {e}")
            return None

    def add_user(self, user_name: str, action: Callable[[float], None]):
        """
        Добавляет пользователя с его действием в список.
        :param user_name: Имя пользователя.
        :param action: Функция действия, которая принимает текущую цену токена.
        """
        self.users.append({"name": user_name, "action": action})

    async def monitor_prices(self, interval: int = 20):
        """
        Асинхронный цикл мониторинга цен.
        :param interval: Интервал проверки цен (в секундах).
        """
        if not self.session:
            raise RuntimeError("Сессия не запущена. Сначала вызовите метод 'start()'.")

        self._running = True
        print("Начало мониторинга цен токена...")

        while self._running:
            current_price = await self.fetch_token_info()
            if current_price is not None:
                print(f"Текущая цена токена: {current_price}")
                for user in self.users:
                    try:
                        user['action'](current_price)
                    except Exception as e:
                        print(f"Ошибка при выполнении действия для {user['name']}: {e}")
            else:
                print("Не удалось получить цену токена.")

            await asyncio.sleep(interval)

    def stop(self):
        """
        Останавливает цикл мониторинга.
        """
        self._running = False


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
    limit_orders = AsyncLimitOrders("4q9fJRXnGLNJiavjaySmvrg9gkFaGW77Ci19x29dpump")

    # Запускаем сессию
    await limit_orders.start()

    # Добавляем пользователей с лимитными действиями
    limit_orders.add_user("User1", example_action_factory(8, "buy"))
    limit_orders.add_user("User2", example_action_factory(7, "sell"))

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