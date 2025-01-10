import asyncio
import requests
import time
import logging
import struct
from threading import Thread
from typing import List, Optional, Dict, Set
from solders.signature import Signature  # Импортируем Signature
from .solana_client import SolanaClient
import hashlib
import base58

def sighash(namespace: str, name: str) -> bytes:
    """
    Вычисляет дискриминатор на основе пространства имен и имени операции.

    Args:
        namespace (str): Пространство имен (например, "global").
        name (str): Имя операции (например, "buy").

    Returns:
        bytes: Первые 8 байт SHA256-хэша от строки "<namespace>:<name>".
    """
    preimage = f"{namespace}:{name}".encode("utf-8")
    full_hash = hashlib.sha256(preimage).digest()
    return full_hash[:8]

# Пример использования
buy_discriminator = sighash("global", "buy")
sell_discriminator = sighash("global", "sell")

print(f"BUY_DISCRIMINATOR: {buy_discriminator.hex()}")
print(f"SELL_DISCRIMINATOR: {sell_discriminator.hex()}")


# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class SolanaMonitor:
    def __init__(self, solana_client: SolanaClient, solana_url: str = "https://api.mainnet-beta.solana.com", request_timeout: int = 10):
        self.solana_url = solana_url
        self.request_timeout = request_timeout
        self.leader_follower_map: Dict[str, Set[str]] = {}
        self.seen_signatures: Dict[str, Set[str]] = {}
        self.total_transactions_processed = 0  # Счётчик обработанных транзакций
        self.monitor_thread = None
        self.is_monitoring = False
        self.solana_client = solana_client

    async def get_signatures_for_address(self, account: str, before: Optional[str] = None, limit: int = 1) -> List[dict]:
        """
        Получение списка транзакций для аккаунта.
        """
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                account,
                {"limit": limit, "before": before} if before else {"limit": limit}
            ]
        }

        try:
            response = requests.post(self.solana_url, json=payload, headers=headers, timeout=self.request_timeout)
            response.raise_for_status()
            return response.json().get("result", [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе списка транзакций для {account}: {e}")
            return []

    async def process_leader_transaction(self, leader: str, signature: str) -> None:
        """
        Обработка транзакции лидера и уведомление фолловеров.
        """
        self.total_transactions_processed += 1
        followers = self.leader_follower_map.get(leader, set())
        logger.info(
            f"[Лидер: {leader}] Обнаружена транзакция: {signature}. "
            f"Всего фолловеров: {len(followers)}. "
            f"Общее количество обработанных транзакций: {self.total_transactions_processed}."
        )

        for follower in followers:
            await self.process_follower_reaction(follower, leader, signature)

    async def process_follower_reaction(self, follower: str, leader: str, signature: str) -> None:
        """
        Реакция фолловера на транзакцию лидера.
        """
        try:
            logger.info(
                f"[Фолловер: {follower}] Реагирует на транзакцию {signature} лидера {leader}. "
                f"Время реакции: {time.strftime('%Y-%m-%d %H:%M:%S')}."
            )

            # Определяем действие (покупка или продажа)
            if await self.should_buy(signature):
                logger.info(f"[Фолловер: {follower}] Покупает токены на основе транзакции {signature}")
                self.solana_client.buy_token_by_signature(signature)
            elif await self.should_sell(signature):
                logger.info(f"[Фолловер: {follower}] Продаёт токены на основе транзакции {signature}")
                self.solana_client.sell_token_by_signature(signature)
            else:
                logger.info(f"[Фолловер: {follower}] Не выполняет никаких действий для транзакции {signature}")

        except Exception as e:
            logger.error(f"[Ошибка] Фолловер {follower} не смог обработать транзакцию {signature}: {e}")

    async def should_buy(self, signature: str) -> bool:
        """
        Логика определения, нужно ли покупать токены.
        """
        try:
            signature = Signature.from_string(signature)
            # Получаем информацию о транзакции
            transaction_info = await self.solana_client.client.get_transaction(signature)
            if not transaction_info or not transaction_info.value or not transaction_info.value.transaction:
                logger.error(f"Транзакция {signature} отсутствует или пуста.")
                return False

            encoded_transaction = transaction_info.value.transaction.transaction
            if not hasattr(encoded_transaction, "message"):
                logger.error(f"Неверная структура данных для транзакции {signature}: {encoded_transaction}")
                return False

            message = encoded_transaction.message
            if not hasattr(message, "instructions") or not hasattr(message, "account_keys"):
                logger.error(f"Отсутствуют ключи instructions или account_keys в транзакции {signature}.")
                return False

            # Дискриминатор для покупки
            BUY_DISCRIMINATOR = sighash("global", "buy")

            # Проверяем инструкции на наличие дискриминатора покупки
            for ix in message.instructions:
                program_id = str(message.account_keys[ix.program_id_index]).split('\n')[0]
                if program_id == str(self.solana_client.PUMP_PROGRAM):
                    data = base58.b58decode(ix.data).hex()
                    if data.startswith(BUY_DISCRIMINATOR.hex()):
                        return True
            return False
        except Exception as e:
            logger.error(f"Ошибка анализа транзакции для покупки: {e}")
            return False


    async def should_sell(self, signature: str) -> bool:
        """
        Логика определения, нужно ли продавать токены.
        """
        try:
            signature = Signature.from_string(signature)
            # Получаем информацию о транзакции
            transaction_info = await self.solana_client.client.get_transaction(signature)
            if not transaction_info or not transaction_info.value or not transaction_info.value.transaction:
                logger.error(f"Транзакция {signature} отсутствует или пуста.")
                return False

            encoded_transaction = transaction_info.value.transaction.transaction
            if not hasattr(encoded_transaction, "message"):
                logger.error(f"Неверная структура данных для транзакции {signature}: {encoded_transaction}")
                return False

            message = encoded_transaction.message
            if not hasattr(message, "instructions") or not hasattr(message, "account_keys"):
                logger.error(f"Отсутствуют ключи instructions или account_keys в транзакции {signature}.")
                return False

            # Дискриминатор для продажи
            SELL_DISCRIMINATOR = sighash("global", "sell")

            # Проверяем инструкции на наличие дискриминатора продажи
            for ix in message.instructions:
                program_id = message.account_keys[ix.program_id_index]
                if program_id == str(self.solana_client.PUMP_PROGRAM):
                    data = base58.b58decode(ix.data).hex()
                    print(data)
                    print(SELL_DISCRIMINATOR.hex())
                    exit()
                    if data.startswith(SELL_DISCRIMINATOR.hex()):
                        return True
            return False
        except Exception as e:
            logger.error(f"Ошибка анализа транзакции для продажи: {e}")
            return False

    async def monitor_leaders(self, interval: int = 5) -> None:
        """
        Основной цикл мониторинга лидеров.
        """
        self.is_monitoring = True
        while self.is_monitoring:
            for leader in list(self.leader_follower_map.keys()):
                try:
                    signatures = await self.get_signatures_for_address(leader)
                    if leader not in self.seen_signatures:
                        self.seen_signatures[leader] = set()

                    for tx in signatures:
                        signature = tx["signature"]
                        if signature not in self.seen_signatures[leader]:
                            self.seen_signatures[leader].add(signature)
                            logger.info(f"[Мониторинг] Новая транзакция для лидера {leader}: {signature}.")
                            await self.process_leader_transaction(leader, signature)
                except Exception as e:
                    logger.error(f"Ошибка мониторинга для лидера {leader}: {e}")
                
                await asyncio.sleep(interval)

    def add_leader(self, leader: str) -> None:
        if leader not in self.leader_follower_map:
            self.leader_follower_map[leader] = set()
            logger.info(f"[Добавление лидера] Лидер {leader} добавлен для мониторинга.")

    def add_relationship(self, leader: str, follower: str) -> None:
        if leader not in self.leader_follower_map:
            self.add_leader(leader)
        self.leader_follower_map[leader].add(follower)
        logger.info(f"[Связь] Фолловер {follower} добавлен к лидеру {leader}.")

    async def start_monitoring(self, interval: int = 5) -> None:
        await self.monitor_leaders(interval)


if __name__ == "__main__":
    solana_client = SolanaClient(100000)
    monitor = SolanaMonitor(solana_client)

    # Добавление лидеров и фолловеров
    monitor.add_leader("3cLY4cPHdsDh1v7UyawbJNkPSYkw26GE7jkV8Zq1z3di")
    monitor.add_relationship("3cLY4cPHdsDh1v7UyawbJNkPSYkw26GE7jkV8Zq1z3di", "follower1")

    asyncio.run(monitor.start_monitoring(interval=5))