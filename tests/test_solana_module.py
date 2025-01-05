# main.py

import asyncio
import logging
import traceback
from solders.pubkey import Pubkey
from src.solana_module import SolanaClient, get_bonding_curve_address, find_associated_bonding_curve



# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,  # Измените на DEBUG, чтобы видеть более подробные логи
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("main.log"),
    ]
)
logger = logging.getLogger(__name__)

async def main():
    try:
        # Инициализация клиента с заданным compute_unit_price
        compute_unit_price = 100000  # Например, 15,000,000 лампортов за вычислительную единицу
        client = SolanaClient(compute_unit_price=compute_unit_price)

        # Определение адреса mint токена
        mint_address = '55YanwmkJQrk2SiZRKNKVbLVz7Ht33zg6RU7uYvipump'  # Замените на ваш mint адрес
        mint = Pubkey.from_string(mint_address)

        # Получение адреса кривой связывания
        bonding_curve_address, bump = get_bonding_curve_address(mint, client.PUMP_PROGRAM)
        associated_bonding_curve = find_associated_bonding_curve(mint, bonding_curve_address)

        # Параметры покупки токенов
        amount_sol = 0.0001  # Количество SOL для покупки
        slippage = 0.3      # Допустимый слиппейдж (30%)

        logger.info(f"Адрес кривой связывания: {bonding_curve_address}")
        logger.info(f"Покупка токенов на сумму {amount_sol:.6f} SOL с допустимым слиппейджем {slippage*100:.1f}%...")

        # Выполнение покупки токенов
        await client.buy_token(mint, bonding_curve_address, associated_bonding_curve, amount_sol, slippage)

        # Получение списка токенов аккаунта
        account_pubkey = client.payer.pubkey()
        tokens = await client.get_account_tokens(account_pubkey)
        logger.info(f"Аккаунт {account_pubkey} имеет {len(tokens)} токенов: {[str(token) for token in tokens]}")

        # Параметры продажи токенов
        token_amount = 10  # Количество токенов для продажи
        min_amount_sol = 0  # Минимальная сумма SOL, которую ожидаете получить

        logger.info(f"Продажа {token_amount} токенов за минимум {min_amount_sol} SOL...")
        
        # Выполнение продажи токенов
        await client.sell_token(mint, bonding_curve_address, associated_bonding_curve, token_amount, min_amount_sol)

    except Exception as e:
        logger.error(f"Произошла ошибка в main: {e}")
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(main())