import aiohttp
import asyncio
from typing import Callable


class AsyncLimitOrders:
    def __init__(self, token: str):
        """
        Инициализация класса.
        :param token: Идентификатор токена для отслеживания.
        """
        self.token = token
        self.url = "https://api.coinmarketcap.com/dexer/v3/dexer/search/main-site"
        self.users = []  # Список пользователей с их настройками

    async def fetch_token_info(self) -> float:
        """
        Асинхронно получает текущую цену токена.
        :return: Цена токена.
        """
        params = {
            "keyword": self.token,
            "all": "false"
        }
        headers = {
            "User-Agent": f"Custom/{int(asyncio.get_event_loop().time())}"
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return float(data['data']['pairs'][0]['priceUsd'])
                    else:
                        print(f"Ошибка API: {response.status}, {await response.text()}")
            except aiohttp.ClientError as e:
                print(f"Ошибка сети: {e}")
        return 0.0  # Возвращаем 0 в случае ошибки

    def add_user(self, user_name: str, action: Callable[[float], None]):
        """
        Добавляет пользователя с его настройками.
        :param user_name: Имя пользователя.
        :param action: Функция действия, которая принимает текущую цену токена.
        """
        self.users.append({"name": user_name, "action": action})

    async def monitor_prices(self, interval: int = 20):
        """
        Асинхронный бесконечный цикл мониторинга цен.
        :param interval: Интервал проверки цен в секундах.
        """
        print("Начало мониторинга цен токена...")
        while True:
            current_price = await self.fetch_token_info()
            if current_price:
                print(f"Текущая цена токена: {current_price}")
                for user in self.users:
                    try:
                        user['action'](current_price)  # Выполнение действия пользователя
                    except Exception as e:
                        print(f"Ошибка выполнения действия для {user['name']}: {e}")
            else:
                print("Не удалось получить цену токена.")

            await asyncio.sleep(interval)


# Пример использования
def example_action_factory(target_price: float, action_type: str):
    """
    Возвращает функцию действия пользователя.
    :param target_price: Целевая цена токена.
    :param action_type: Тип действия ("buy" или "sell").
    :return: Функция действия.
    """
    def action(current_price: float):
        if action_type == "buy" and current_price <= target_price:
            print(f"Покупка токена по цене {current_price} (цель: {target_price})")
        elif action_type == "sell" and current_price >= target_price:
            print(f"Продажа токена по цене {current_price} (цель: {target_price})")
    return action


async def main():
    # Создаем экземпляр класса
    limit_orders = AsyncLimitOrders("4q9fJRXnGLNJiavjaySmvrg9gkFaGW77Ci19x29dpump")

    # Добавляем пользователей
    limit_orders.add_user("User1", example_action_factory(1.5, "buy"))
    limit_orders.add_user("User2", example_action_factory(0.000001, "sell"))

    # Запускаем мониторинг
    await limit_orders.monitor_prices()


# Запуск программы
if __name__ == "__main__":
    asyncio.run(main())