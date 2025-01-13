import asyncio
import logging
import sys
import os

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.solana_module.solana_client import SolanaClient
from src.solana_module.solana_monitor import SolanaMonitor

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Тестовый адрес известного трейдера pump.fun
TEST_WALLET = "3cLY4cPHdsDh1v7UyawbJNkPSYkw26GE7jkV8Zq1z3di"

async def test_monitor():
    """
    Тестирование мониторинга транзакций pump.fun.
    """
    try:
        # Инициализация клиента и монитора
        solana_client = SolanaClient(100000)  # compute_unit_price = 100000
        monitor = SolanaMonitor(solana_client)
        
        # Добавляем тестовый кошелек для отслеживания
        monitor.add_leader(TEST_WALLET)
        
        # Добавляем тестового фолловера
        test_follower = "test_follower_1"
        monitor.add_relationship(TEST_WALLET, test_follower)
        
        logger.info(f"Starting monitoring for pump.fun trader: {TEST_WALLET}")
        logger.info("Press Ctrl+C to stop monitoring")
        
        # Запускаем мониторинг
        await monitor.start_monitoring()
        
        # Держим мониторинг активным
        while True:
            await asyncio.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.error(f"Error during monitoring: {e}")
    finally:
        # Останавливаем мониторинг
        await monitor.stop_monitoring()
        # Закрываем клиент
        if solana_client:
            await solana_client.close()

if __name__ == "__main__":
    try:
        asyncio.run(test_monitor())
    except KeyboardInterrupt:
        print("\nTest stopped by user") 