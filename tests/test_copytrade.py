# test_copy_trader.py


import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from solders.pubkey import Pubkey



# solana_module/solana_client.py

import asyncio
import json
import struct
import os
import sys
import logging
import traceback
import time
from solana.rpc.types import TokenAccountOpts
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.compute_budget import set_compute_unit_price
from solana.transaction import Transaction
import spl.token.instructions as spl_token
from spl.token.instructions import get_associated_token_address
from construct import Struct, Int64ul, Flag


# solana_module/utils.py

from solders.pubkey import Pubkey

def get_bonding_curve_address(mint: Pubkey, program_id: Pubkey) -> tuple[Pubkey, int]:
    """
    Вычисляет адрес кривой связывания для данного mint.
    """
    return Pubkey.find_program_address(
        [
            b"bonding-curve",
            bytes(mint)
        ],
        program_id
    )

def find_associated_bonding_curve(mint: Pubkey, bonding_curve: Pubkey) -> Pubkey:
    """
    Находит ассоциированную кривую связывания для данного mint и кривой связывания.
    """
    TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ATA_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

    derived_address, _ = Pubkey.find_program_address(
        [
            bytes(bonding_curve),
            bytes(TOKEN_PROGRAM_ID),
            bytes(mint), 
        ],
        ATA_PROGRAM_ID
    )
    return derived_address


from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception,
    RetryError
)

import httpx  # Используется в обработке исключений

# Configure Logging
logging.basicConfig(
    level=logging.INFO,  # Измените на DEBUG, чтобы видеть RPC ответы
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("script.log"),
    ]
)
logger = logging.getLogger(__name__)

# Константы
EXPECTED_DISCRIMINATOR = struct.pack("<Q", 6966180631402821399)
TOKEN_DECIMALS = 6
LAMPORTS_PER_SOL = 1_000_000_000

class BondingCurveState:
    _STRUCT = Struct(
        "virtual_token_reserves" / Int64ul,
        "virtual_sol_reserves" / Int64ul,
        "real_token_reserves" / Int64ul,
        "real_sol_reserves" / Int64ul,
        "token_total_supply" / Int64ul,
        "complete" / Flag
    )

    def __init__(self, data: bytes) -> None:
        parsed = self._STRUCT.parse(data[8:])  # Пропустить первые 8 байт (дискриминатор)
        self.__dict__.update(parsed)

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            current = time.monotonic()
            while len(self.calls) >= self.max_calls:
                if self.calls[0] <= current - self.period:
                    self.calls.pop(0)
                else:
                    wait_time = self.period - (current - self.calls[0])
                    logger.info(f"Rate limiter active. Sleeping for {wait_time:.2f} seconds.")
                    await asyncio.sleep(wait_time)
                    current = time.monotonic()
            self.calls.append(current)
            logger.debug(f"Current call count: {len(self.calls)} within {self.period} seconds.")

# Инициализация RateLimiter: например, max 3 вызова в секунду
rate_limiter = RateLimiter(max_calls=3, period=1.0)

async def send_request_with_rate_limit(client: AsyncClient, request_func, *args, **kwargs):
    await rate_limiter.acquire()
    return await request_func(*args, **kwargs)

def is_rate_limit_error(exception):
    """
    Функция-предикат для проверки, является ли исключение ошибкой HTTP 429.
    """
    return (
        isinstance(exception, httpx.HTTPStatusError) and
        exception.response.status_code == 429
    )

class SolanaClient:
    def __init__(self, compute_unit_price: int = COMPUTE_UNIT_PRICE):
        self.rpc_endpoint = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
        self.payer = self.load_keypair()
        self.client = AsyncClient(self.rpc_endpoint)
        self.compute_unit_price = compute_unit_price

        # Адреса программ
        self.PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
        self.PUMP_GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
        self.PUMP_EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
        self.PUMP_FEE = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
        self.SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
        self.SYSTEM_TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        self.SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
        self.SYSTEM_RENT = Pubkey.from_string("SysvarRent111111111111111111111111111111111")
        self.SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")

    def load_keypair(self) -> Keypair:
        """
        Загружает ключевую пару из конфигурации.
        """
        return PRIVATE_KEY

    async def create_associated_token_account(self, mint: Pubkey) -> Pubkey:
        """
        Создаёт ассоциированный токен аккаунт для данного mint, если он ещё не существует.
        """
        associated_token_account = get_associated_token_address(self.payer.pubkey(), mint)
        account_info = await send_request_with_rate_limit(self.client, self.client.get_account_info, associated_token_account)
        if account_info.value is None:
            logger.info("Создание ассоциированного токен аккаунта...")
            create_ata_ix = spl_token.create_associated_token_account(
                payer=self.payer.pubkey(),
                owner=self.payer.pubkey(),
                mint=mint
            )
            compute_budget_ix = set_compute_unit_price(self.compute_unit_price)
            tx_ata = Transaction().add(create_ata_ix).add(compute_budget_ix)
            tx_ata.recent_blockhash = (await send_request_with_rate_limit(self.client, self.client.get_latest_blockhash)).value.blockhash
            tx_ata.fee_payer = self.payer.pubkey()
            tx_ata.sign(self.payer)
            try:
                tx_ata_signature = await send_request_with_rate_limit(
                    self.client,
                    self.client.send_transaction,
                    tx_ata,
                    self.payer,
                    opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
                )
                logger.info(f"ATA Transaction отправлен: https://explorer.solana.com/tx/{tx_ata_signature.value}")
                await self.confirm_transaction_with_delay(tx_ata_signature.value)
                logger.info(f"Ассоциированный токен аккаунт создан: {associated_token_account}")
            except Exception as e:
                logger.error(f"Не удалось отправить ATA транзакцию: {e}")
                logger.error(traceback.format_exc())
                raise
        else:
            logger.info(f"Ассоциированный токен аккаунт уже существует: {associated_token_account}")
        return associated_token_account

    @retry(
        retry=retry_if_exception(is_rate_limit_error),
        wait=wait_exponential(multiplier=2, min=10, max=120),
        stop=stop_after_attempt(5),
        reraise=True
    )
    
    async def send_buy_transaction(self, params: dict, retries: int = 3):
        """
        Отправляет транзакцию покупки токенов.
        """
        accounts = [
            AccountMeta(pubkey=self.PUMP_GLOBAL, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.PUMP_FEE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=params['mint'], is_signer=False, is_writable=False),
            AccountMeta(pubkey=params['bonding_curve'], is_signer=False, is_writable=True),
            AccountMeta(pubkey=params['associated_bonding_curve'], is_signer=False, is_writable=True),
            AccountMeta(pubkey=params['associated_token_account'], is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.payer.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(pubkey=self.SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.SYSTEM_TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.SYSTEM_RENT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.PUMP_EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.PUMP_PROGRAM, is_signer=False, is_writable=False),
        ]

        discriminator = struct.pack("<Q", 16927863322537952870)
        token_amount_packed = struct.pack("<Q", int(params['token_amount'] * 10**6))
        max_amount_packed = struct.pack("<Q", params['max_amount_lamports'])
        data = discriminator + token_amount_packed + max_amount_packed

        buy_ix = Instruction(self.PUMP_PROGRAM, data, accounts)
        compute_budget_ix = set_compute_unit_price(self.compute_unit_price)

        for attempt in range(retries):
            try:
                logger.info(f"Попытка отправки Buy транзакции {attempt + 1} из {retries}")
                
                tx_buy = Transaction().add(buy_ix).add(compute_budget_ix)
                tx_buy.recent_blockhash = (await send_request_with_rate_limit(self.client, self.client.get_latest_blockhash)).value.blockhash
                tx_buy.fee_payer = self.payer.pubkey()
                tx_buy.sign(self.payer)

                tx_buy_signature = await send_request_with_rate_limit(
                    self.client,
                    self.client.send_transaction,
                    tx_buy,
                    self.payer,
                    opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
                )
                
                logger.info(f"Buy Transaction отправлен: https://explorer.solana.com/tx/{tx_buy_signature.value}")
                
                # Ожидание подтверждения с увеличенным таймаутом и повторными попытками
                await self.confirm_transaction_with_delay(
                    tx_buy_signature.value,
                    max_retries=15,
                    retry_delay=6
                )
                
                logger.info(f"Buy транзакция подтверждена: {tx_buy_signature.value}")
                return tx_buy_signature.value
                
            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"Не удалось отправить Buy транзакцию: {str(e)}")
                    raise
                
                logger.warning(f"Попытка транзакции {attempt + 1} не удалась: {str(e)}. Повторная попытка...")
                await asyncio.sleep(2 * (attempt + 1))  # Экспоненциальная задержка

        raise Exception("Не удалось отправить транзакцию после всех попыток")

    async def confirm_transaction_with_delay(self, signature: str, max_retries: int = 10, retry_delay: int = 5):
        """
        Ожидает подтверждения транзакции с задержкой и ограничением количества попыток.
        """
        logger.info(f"Ожидание подтверждения транзакции: {signature}")
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка подтверждения {attempt + 1} для транзакции {signature}")
                response = await send_request_with_rate_limit(self.client, self.client.get_signature_statuses, [signature])
                if not response.value or not response.value[0]:
                    logger.info("Статус подписи не найден. Повторная попытка...")
                    await asyncio.sleep(retry_delay)
                    continue

                signature_status = response.value[0]
                if signature_status.err is not None:
                    error_msg = f"Транзакция завершилась с ошибкой: {signature_status.err}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
                if signature_status.confirmation_status:
                    logger.info("Транзакция успешно подтверждена!")
                    logger.info(f"Текущий статус: {signature_status.confirmation_status}")
                    return True
                logger.info(f"Текущий статус: {signature_status.confirmation_status}. Ожидание финализации...")
                await asyncio.sleep(retry_delay)
                
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception(f"Не удалось подтвердить транзакцию после {max_retries} попыток: {str(e)}")
                logger.warning(f"Ошибка при проверке статуса транзакции: {str(e)}. Повторная попытка...")
                await asyncio.sleep(retry_delay)
        
        raise Exception(f"Время ожидания подтверждения транзакции истекло после {max_retries} попыток")

    async def buy_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey, amount: float, slippage: float = 0.25):
        """
        Выполняет покупку токенов.
        """
        try:
            associated_token_account = await self.create_associated_token_account(mint)
        except Exception as e:
            logger.error(f"Не удалось создать или проверить ассоциированный токен аккаунт: {e}")
            return

        amount_lamports = int(amount * LAMPORTS_PER_SOL)

        # Получение цены токена
        try:
            curve_state = await self.get_pump_curve_state(bonding_curve)
            token_price_sol = self.calculate_pump_curve_price(curve_state)
        except Exception as e:
            logger.error(f"Не удалось получить или вычислить цену токена: {e}")
            return

        token_amount = amount / token_price_sol

        # Расчет максимального количества SOL с учетом слиппейджа
        max_amount_lamports = int(amount_lamports * (1 + slippage))

        params = {
            'mint': mint,
            'bonding_curve': bonding_curve,
            'associated_bonding_curve': associated_bonding_curve,
            'associated_token_account': associated_token_account,
            'token_amount': token_amount,
            'max_amount_lamports': max_amount_lamports
        }

        try:
            await self.send_buy_transaction(params)
        except RetryError as re:
            logger.error(f"Не удалось выполнить Buy транзакцию после повторных попыток: {re}")
        except Exception as e:
            logger.error(f"Не удалось выполнить Buy транзакцию: {e}")

    async def get_pump_curve_state(self, curve_address: Pubkey) -> BondingCurveState:
        """
        Получает состояние кривой связывания по адресу.
        """
        logger.info(f"Получение состояния кривой связывания по адресу: {curve_address}")
        response = await send_request_with_rate_limit(self.client, self.client.get_account_info, curve_address)
        if not response.value or not response.value.data:
            logger.error("Недопустимое состояние кривой: Нет данных")
            raise ValueError("Недопустимое состояние кривой: Нет данных")

        data = response.value.data
        if data[:8] != EXPECTED_DISCRIMINATOR:
            logger.error("Неверный дискриминатор состояния кривой")
            raise ValueError("Неверный дискриминатор состояния кривой")

        return BondingCurveState(data)

    def calculate_pump_curve_price(self, curve_state: BondingCurveState) -> float:
        """
        Вычисляет цену токена на основе состояния кривой связывания.
        """
        if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
            raise ValueError("Недопустимое состояние резервов")

        price = (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)
        logger.info(f"Вычисленная цена токена: {price:.10f} SOL")
        return price

    async def get_account_tokens(self, account_pubkey: Pubkey) -> list:
        """
        Получает список токенов, принадлежащих аккаунту.
        """
        logger.info(f"Получение токенов для аккаунта: {account_pubkey}")
        response = await send_request_with_rate_limit(self.client, self.client.get_token_accounts_by_owner, account_pubkey, TokenAccountOpts(program_id=self.SYSTEM_TOKEN_PROGRAM))
        #pipip
        if not response.value:
            logger.info("Токены для этого аккаунта не найдены.")
            return []
        
        tokens = []
        for token_account in response.value:
            token_pubkey = token_account.pubkey
            token_info = await send_request_with_rate_limit(self.client, self.client.get_account_info, token_pubkey)
            if token_info.value and token_info.value.data:
                # Здесь можно распарсить информацию о токене по необходимости
                tokens.append(token_pubkey)
        logger.info(f"Найдено {len(tokens)} токенов для аккаунта {account_pubkey}")
        return tokens
    
    def derive_event_authority_pda(self, bonding_curve: Pubkey, mint: Pubkey) -> Pubkey:
        """
        Деривирует PDA для event_authority с использованием сидов.
        """
        seeds = [
            b"event_authority",  # Пример сидов, замените на реальные
            bytes(bonding_curve),
            bytes(mint)
        ]
        event_authority_pda, bump = Pubkey.find_program_address(
            seeds,
            self.PUMP_PROGRAM
        )
        return event_authority_pda

    @retry(
        retry=retry_if_exception(is_rate_limit_error),
        wait=wait_exponential(multiplier=2, min=10, max=120),
        stop=stop_after_attempt(5),
        reraise=True
    )
    async def send_sell_transaction(self, params: dict, retries: int = 3):
        """
        Отправляет транзакцию продажи токенов.
        """
        accounts = [
            AccountMeta(pubkey=self.PUMP_GLOBAL, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.PUMP_FEE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=params['mint'], is_signer=False, is_writable=False),
            AccountMeta(pubkey=params['bonding_curve'], is_signer=False, is_writable=True),
            AccountMeta(pubkey=params['associated_bonding_curve'], is_signer=False, is_writable=True),
            AccountMeta(pubkey=params['associated_token_account'], is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.payer.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(pubkey=self.SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.SYSTEM_TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.PUMP_EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=self.PUMP_PROGRAM, is_signer=False, is_writable=False),
        ]

        # Замените на правильный дискриминатор для продажи
        
        resp = await self.client.get_token_account_balance(params['associated_token_account'])
        token_balance = int(resp.value.amount)
        token_balance_decimal = token_balance / 10**TOKEN_DECIMALS
        curve_state = await self.get_pump_curve_state(params['bonding_curve'])
        token_price_sol = self.calculate_pump_curve_price(curve_state)
        amount = params['token_amount']
        min_sol_output = float(token_balance_decimal) * float(token_price_sol)
        slippage_factor = 1 - 0.3
        min_sol_output = int((min_sol_output * slippage_factor) * LAMPORTS_PER_SOL)
        
        print(f"Selling {token_balance_decimal} tokens")
        print(f"Minimum SOL output: {min_sol_output / LAMPORTS_PER_SOL:.10f} SOL")

        for attempt in range(retries):
            try:
                logger.info(f"Попытка отправки Sell транзакции {attempt + 1} из {retries}")
                discriminator = struct.pack("<Q", 12502976635542562355)
                data = discriminator + struct.pack("<Q", amount) + struct.pack("<Q", min_sol_output)
                sell_ix = Instruction(self.PUMP_PROGRAM, data, accounts)
                
                recent_blockhash = await self.client.get_latest_blockhash()
                transaction = Transaction()
                transaction.add(sell_ix).add(set_compute_unit_price(self.compute_unit_price))
                transaction.recent_blockhash = recent_blockhash.value.blockhash

                tx_sell_signature = await self.client.send_transaction(
                    transaction,
                    self.payer,
                    opts=TxOpts(skip_preflight=True, preflight_commitment=Confirmed),
                )

                print(f"Transaction sent: https://explorer.solana.com/tx/{tx_sell_signature.value}")

                await self.confirm_transaction_with_delay(
                    tx_sell_signature.value,
                    max_retries=15,
                    retry_delay=6
                )
                
                logger.info(f"Sell транзакция подтверждена: {tx_sell_signature.value}")
                return tx_sell_signature.value
                
            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"Не удалось отправить Sell транзакцию: {str(e)}")
                    raise
                
                logger.warning(f"Попытка транзакции {attempt + 1} не удалась: {str(e)}. Повторная попытка...")
                await asyncio.sleep(2 * (attempt + 1))  # Экспоненциальная задержка

        raise Exception("Не удалось отправить транзакцию после всех попыток")

    async def sell_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey, token_amount: float, min_amount: float = 0.25):
        """
        Выполняет продажу токенов.
        """
        try:
            associated_token_account = await self.create_associated_token_account(mint)
        except Exception as e:
            logger.error(f"Не удалось создать или проверить ассоциированный токен аккаунт: {e}")
            return

        # Здесь можно добавить логику для получения минимальной суммы в SOL, основываясь на слиппейдже и текущей цене токена

        params = {
            'mint': mint,
            'bonding_curve': bonding_curve,
            'associated_bonding_curve': associated_bonding_curve,
            'associated_token_account': associated_token_account,
            'token_amount': token_amount,
            'min_amount_lamports': int(min_amount * LAMPORTS_PER_SOL),
        }

        try:
            await self.send_sell_transaction(params)
        except RetryError as re:
            logger.error(f"Не удалось выполнить Sell транзакцию после повторных попыток: {re}")
        except Exception as e:
            logger.error(f"Не удалось выполнить Sell транзакцию: {e}")



import asyncio
import struct
import logging
from typing import Set, List
from solana.rpc.async_api import AsyncClient
from solana.rpc.core import RPCException
from solana.transaction import Transaction
from solders.pubkey import Pubkey
from solders.signature import Signature

# Импортируем ваш SolanaClient и связанные объекты

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Дискриминаторы (из вашего кода для Buy/Sell)
BUY_DISCRIMINATOR = struct.pack("<Q", 16927863322537952870)
SELL_DISCRIMINATOR = struct.pack("<Q", 12502976635542562355)

class CopyTrader:
    """
    Класс CopyTrader «слушает» (или регулярно опрашивает) транзакции мастера и повторяет их 
    через ваш SolanaClient.
    """
    def __init__(
        self,
        master_pubkey: Pubkey,
        solana_client: SolanaClient,
        polling_interval: int = 5
    ):
        """
        :param master_pubkey: Pubkey мастера (кошелёк), за которым следим.
        :param solana_client: Экземпляр вашего SolanaClient.
        :param polling_interval: Период (в секундах) опроса новых транзакций.
        """
        self.master_pubkey: Pubkey = master_pubkey
        self.solana_client: SolanaClient = solana_client
        self.rpc_client: AsyncClient = solana_client.client
        self.polling_interval: int = polling_interval

        # Чтобы не обрабатывать одну и ту же транзакцию несколько раз
        self.seen_signatures: Set[str] = set()

    async def start_copytrade(self):
        """
        Запуск постоянного мониторинга транзакций мастера.
        Можно вызывать это внутри asyncio.run(...) или из любого другого корутино-менеджера.
        """
        logger.info(f"Запуск copytrade для мастера: {self.master_pubkey}")
        while True:
            try:
                await self.check_new_transactions()
            except Exception as e:
                logger.error(f"Ошибка в процессе мониторинга: {e}")
            # Ждём заданный интервал и повторяем
            await asyncio.sleep(self.polling_interval)

    async def check_new_transactions(self):
        """
        Опрашивает последние подписи (tx) мастера и обрабатывает те, 
        которые ещё не видели (не в seen_signatures).
        """
        # Метод get_signatures_for_address возвращает список { signature, slot, ... }, начиная с самых новых
        sigs_info = await self.rpc_client.get_signatures_for_address(
            self.master_pubkey,
            limit=10  # пример: берём последние 10; можно увеличить
        )

        if not sigs_info.value:
            return

        # Сортируем сигнатуры от старых к новым (чтобы воспроизводить операции в хронологическом порядке)
        # По умолчанию может быть [новые ... старые]
        sorted_sigs = list(reversed(sigs_info.value))

        new_sigs: List[str] = []
        for tx_info in sorted_sigs:
            signature = tx_info.signature
            if signature not in self.seen_signatures:
                new_sigs.append(signature)

        if not new_sigs:
            return

        # Помечаем их сразу как «увиденные», чтобы не дублить
        for sig in new_sigs:
            self.seen_signatures.add(sig)

        # Теперь постараемся последовательно обработать эти транзакции
        for sig in new_sigs:
            await self.process_transaction(sig)

    async def process_transaction(self, signature: str):
        """
        Получаем детали транзакции, вычленяем инструкции, которые относятся к PUMP_PROGRAM,
        проверяем их дискриминатор (BUY/SELL) и копируем.
        """
        logger.info(f"Обрабатываем транзакцию мастера: {signature}")
        try:
            tx_resp = await self.rpc_client.get_transaction(
                signature,
                max_supported_transaction_version=2  # можно и 1, в зависимости от версии
            )
        except RPCException as e:
            logger.error(f"Не удалось получить транзакцию {signature}: {e}")
            return

        if not tx_resp.value or not tx_resp.value.transaction:
            logger.warning(f"Нет данных о транзакции {signature}")
            return

        # В transaction.message лежат инструкции
        message = tx_resp.value.transaction.message
        instructions = message.instructions

        for ix in instructions:
            # Проверяем, что инструкция адресована вашей программе PUMP_PROGRAM
            if ix.program_id == self.solana_client.PUMP_PROGRAM:
                data_bytes = bytes(ix.data)
                disc = data_bytes[:8]  # первые 8 байт — дискриминатор

                if disc == BUY_DISCRIMINATOR:
                    # Это buy-инструкция
                    await self.handle_buy_ix(ix, data_bytes[8:])
                elif disc == SELL_DISCRIMINATOR:
                    # Это sell-инструкция
                    await self.handle_sell_ix(ix, data_bytes[8:])
                else:
                    logger.debug("Инструкция в PUMP_PROGRAM, но дискриминатор не buy/sell")

    async def handle_buy_ix(self, ix, data: bytes):
        """
        Разбираем данные buy-инструкции:
          buy = discriminator (8 байт) + token_amount (Q) + max_amount_lamports (Q)
        Здесь data = всё, кроме первых 8 байт дискриминатора.
        """
        if len(data) < 16:
            logger.warning("Недостаточная длина данных buy-инструкции")
            return

        token_amount_lamports = struct.unpack("<Q", data[:8])[0]   # int
        max_amount_lamports = struct.unpack("<Q", data[8:16])[0]   # int

        token_amount_float = token_amount_lamports / 1e6  # у вас в коде это означает (token_amount * 10**6)
        logger.info(f"[CopyTrade] BUY: token_amount={token_amount_float}, max_amount_lamports={max_amount_lamports}")

        # Теперь важно понять, какие Pubkey являются mint, bonding_curve, associated_bonding_curve и т.д.
        # У вас в send_buy_transaction() передаются:
        #   accounts = [
        #       PUMP_GLOBAL, PUMP_FEE, mint, bonding_curve, associated_bonding_curve, ...
        #   ]
        # Смотрим индексы в ix.accounts — это объекты типа `CompiledInstructionAccount`.
        # В Solana Py, чтобы получить Pubkey, нужно смотреть на:
        #   message.accountKeys[ix.accounts[idx].index]
        # Но в solana-py 0.x по-другому, в solders своё... 
        # В упрощённом случае возьмём "напрямую" (зная порядок).

        # Пример (аккуратно — индексы могут отличаться от примера в вашем коде!):
        # ix.accounts[2] -> mint
        # ix.accounts[3] -> bonding_curve
        # ix.accounts[4] -> associated_bonding_curve
        # ...
        # В transaction.message.account_keys — массив всех pubkeys, 
        #   ix.accounts[x].pubkey — тоже должен быть.
        # Для solana-py 0.18+ / solders может быть слегка по-другому, иллюстрирую идею:

        message = await self._get_message_from_ix(ix)
        if not message:
            logger.warning("Не удалось извлечь message для handle_buy_ix.")
            return

        # Пытаемся достать pubkey по индексам. 
        # Пример (посмотрите реальный порядок в вашем send_buy_transaction!):
        mint_pubkey = message.account_keys[ix.accounts[2].index]
        bonding_curve_pubkey = message.account_keys[ix.accounts[3].index]
        associated_bonding_curve_pubkey = message.account_keys[ix.accounts[4].index]

        # buy_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey, amount: float, slippage: float = 0.25)
        # Но у нас нет параметра slippage. Можно брать дефолт (0.25) или ваш.

        # В вашем send_buy_transaction вы вычисляете 'token_amount' как (amount / token_price_sol).
        # Мы же сейчас копируем 1-в-1:
        #   token_amount_float = <кол-во токенов, которое хочет купить мастер>
        #   max_amount_lamports = <сколько SOL (в лампортах) готов потратить мастер c учётом slippage>
        #
        # Но метод buy_token(...) у вас принимает 'amount' как кол-во SOL, которое тратим,
        #   а не кол-во токенов напрямую! 
        #
        # То есть в вашем коде внутри buy_token:
        #   amount_lamports = int(amount * LAMPORTS_PER_SOL)
        #   token_amount = amount / token_price_sol
        #
        # Так что, чтобы «синхронно» копировать, нужно:
        #   1) Либо вызвать напрямую send_buy_transaction(params) — имитируя те же параметры.
        #   2) Либо перевести token_amount_float обратно в кол-во SOL. 
        #
        # Самый простой путь — вызвать напрямую send_buy_transaction(...), 
        #   но у нас нет под рукой готового словаря `params`. 
        # Нужно собрать его аналогично вашему коду.
        # 
        # Для примера ниже я покажу вызов `send_buy_transaction`, но уже "напрямую":
        # (Только не забудьте, что этот метод требует `token_amount` (float) и `max_amount_lamports` (int)).

        params = {
            "mint": mint_pubkey,
            "bonding_curve": bonding_curve_pubkey,
            "associated_bonding_curve": associated_bonding_curve_pubkey,
            "associated_token_account": None,  # Заполнится внутри (можем сами создать/найти),
            "token_amount": token_amount_float,
            "max_amount_lamports": max_amount_lamports,
        }

        logger.info("[CopyTrade] Отправляем buy-транзакцию от имени нашего SolanaClient...")
        try:
            await self.solana_client.send_buy_transaction(params)
            logger.info("[CopyTrade] BUY скопирован успешно!")
        except Exception as e:
            logger.error(f"[CopyTrade] Ошибка при копировании BUY: {e}")

    async def handle_sell_ix(self, ix, data: bytes):
        """
        Разбираем данные sell-инструкции:
          sell = discriminator (8 байт) + amount(Q) + min_sol_output(Q)
        """
        if len(data) < 16:
            logger.warning("Недостаточная длина данных sell-инструкции")
            return

        sell_amount_lamports = struct.unpack("<Q", data[:8])[0]
        min_sol_output = struct.unpack("<Q", data[8:16])[0]

        # В вашем коде:
        #   sell_amount -> token_amount
        #   resp = await self.client.get_token_account_balance(...)
        # и т.д.
        #
        # Но мы берём "сырые" данные: sell_amount_lamports = (token_amount * 10**6)
        sell_amount_float = sell_amount_lamports / 1e6

        logger.info(f"[CopyTrade] SELL: token_amount={sell_amount_float}, min_sol_output={min_sol_output}")

        message = await self._get_message_from_ix(ix)
        if not message:
            logger.warning("Не удалось извлечь message для handle_sell_ix.")
            return

        mint_pubkey = message.account_keys[ix.accounts[2].index]
        bonding_curve_pubkey = message.account_keys[ix.accounts[3].index]
        associated_bonding_curve_pubkey = message.account_keys[ix.accounts[4].index]

        # У вас есть метод send_sell_transaction(params),
        #   которому нужны: mint, bonding_curve, associated_bonding_curve, associated_token_account, token_amount, ...
        # Сформируем params:
        params = {
            "mint": mint_pubkey,
            "bonding_curve": bonding_curve_pubkey,
            "associated_bonding_curve": associated_bonding_curve_pubkey,
            # associated_token_account получится внутри (можем передать None, 
            #   ваш метод всё равно создаёт ATA при необходимости)
            "associated_token_account": None,
            "token_amount": sell_amount_lamports,  # В вашем коде это int, 
                                                  # хотя в sell_token(...) вы передаёте float.
                                                  # Внимательно проверьте совместимость!
                                                  # Если нужно float, то передайте sell_amount_float.
            "min_amount_lamports": min_sol_output,
        }

        logger.info("[CopyTrade] Отправляем sell-транзакцию от имени нашего SolanaClient...")
        try:
            await self.solana_client.send_sell_transaction(params)
            logger.info("[CopyTrade] SELL скопирован успешно!")
        except Exception as e:
            logger.error(f"[CopyTrade] Ошибка при копировании SELL: {e}")

    async def _get_message_from_ix(self, ix) -> Transaction:
        """
        Вспомогательный метод, чтобы получить "message" транзакции и account_keys.
        В зависимости от версии solana-py / solders структура может отличаться.
        """
        # В объекте ix может не быть прямого поля message, 
        # а `transaction.message` мы забираем через self.process_transaction(...). 
        # Но у нас уже есть `message` внутри process_transaction, 
        #   можно было бы передавать его параметром.
        # Чтобы не менять структуру слишком сильно, сделаем упрощённый вариант:

        # Если ix родом из tx_resp.value.transaction.message,
        #   то у ix может быть ссылка на parent message?
        #   В solders/solana-py 0.27+ обычно можно:
        #       parent_message = ix.parent
        # Но зависит от реализации.

        # Упростим: мы запоминаем глобальную копию message (которую взяли выше),
        #   и будем просто возвращать её. 
        # Для демонстрации сделаем так:
        if not hasattr(ix, "parent_instruction"):
            return None
        return ix.parent_instruction  # Теоретически, это может вернуть None или TransactionMessage

        # В некоторых реализациях solana-py:
        #    ix.parent_instruction.account_keys -> список pubkeys
        # или
        #    ix.parent_instruction.message.account_keys

        # В реальном проекте лучше сразу в process_transaction() передавать message при вызове handle_buy_ix/handle_sell_ix.


# Для асинхронных тестов используем pytest-asyncio
@pytest.mark.asyncio
async def test_copy_trade_buy_transaction():
    """
    Проверяем, что при обнаружении buy-инструкции CopyTrader вызывает 
    solana_client.send_buy_transaction с корректными параметрами.
    """

    # 1. Создаём "фейкового" клиента и заглушаем его методы
    fake_solana_client = SolanaClient()
    fake_solana_client.send_buy_transaction = AsyncMock()
    fake_solana_client.send_sell_transaction = AsyncMock()

    # 2. Создаём copy_trader с "мастером"
    master_pubkey = Pubkey.from_string("MasterFakePublicKey11111111111111111111111111111")
    copy_trader = CopyTrader(master_pubkey, fake_solana_client, polling_interval=0)  # polling_interval=0 для ускорения теста

    # 3. Мокаем (подменяем) метод RPC get_signatures_for_address, чтобы вернуть фиктивную сигнатуру
    mock_sigs_for_address_value = [
        {
            "signature": "FakeSignatureBuy111",
            "slot": 123,
        }
    ]

    # Также нужно замокать get_transaction, чтобы вернуть транзакцию, содержащую BUY-инструкцию
    # По аналогии с вашим дискриминатором: 16927863322537952870 -> b'\x16\x93\xab...'
    # Но проще вставить байты напрямую.
    BUY_DISCRIMINATOR = b'\x16\x93\x1A\xEE\xCA\xFE\xBE\xEF'  # пример, любой 8-байтный набор
    # Ниже пример упрощённых данных, где:
    #  - 8 байт дискриминатора
    #  - 8 байт (token_amount_lamports)
    #  - 8 байт (max_amount_lamports)
    raw_ix_data = BUY_DISCRIMINATOR + (100_0000).to_bytes(8, "little") + (200_0000000).to_bytes(8, "little")
    # Здесь token_amount_lamports = 100_0000 (что = 1.0 токен, если считать 10**6), max_amount_lamports = 200_0000000

    # Создаём фиктивные инструкции
    # ix.program_id = PUMP_PROGRAM
    fake_pump_program = fake_solana_client.PUMP_PROGRAM
    compiled_ix = MagicMock()
    compiled_ix.program_id = fake_pump_program
    compiled_ix.data = raw_ix_data
    compiled_ix.accounts = []  # упростим; в реальном случае здесь есть индексы

    # Мокаем структуру, которую вернёт get_transaction
    mock_tx_resp = MagicMock()
    mock_tx_resp.value.transaction.message.instructions = [compiled_ix]

    # Подменяем в самом клиенте (AsyncClient):
    with patch.object(copy_trader.rpc_client, "get_signatures_for_address", return_value=MagicMock(value=mock_sigs_for_address_value)), \
         patch.object(copy_trader.rpc_client, "get_transaction", return_value=mock_tx_resp):
        
        # 4. Запускаем метод check_new_transactions() один раз
        await copy_trader.check_new_transactions()

    # 5. Проверяем, что вызван send_buy_transaction 
    #    (т.к. было обнаружено buy-инструкция)
    fake_solana_client.send_buy_transaction.assert_awaited_once()

    # А вот send_sell_transaction НЕ должен вызываться
    fake_solana_client.send_sell_transaction.assert_not_awaited()

    print("[TEST] test_copy_trade_buy_transaction пройден.")


@pytest.mark.asyncio
async def test_copy_trade_sell_transaction():
    """
    Проверяем, что при обнаружении sell-инструкции CopyTrader вызывает 
    solana_client.send_sell_transaction с корректными параметрами.
    """
    fake_solana_client = SolanaClient()
    fake_solana_client.send_buy_transaction = AsyncMock()
    fake_solana_client.send_sell_transaction = AsyncMock()

    master_pubkey = Pubkey.from_string("MasterFakePublicKey22222222222222222222222222222")
    copy_trader = CopyTrader(master_pubkey, fake_solana_client, polling_interval=0)

    # Фиктивная сигнатура
    mock_sigs_for_address_value = [
        {
            "signature": "FakeSignatureSell222",
            "slot": 456,
        }
    ]

    SELL_DISCRIMINATOR = b'\xDE\xAD\xBE\xEF\x01\x02\x03\x04'  # пример, любой 8-байтный набор
    # Допустим, sell_amount_lamports = 500_0000 (5.0 токенов), min_sol_output = 1234567
    raw_ix_data = SELL_DISCRIMINATOR + (500_0000).to_bytes(8, "little") + (1234567).to_bytes(8, "little")

    fake_pump_program = fake_solana_client.PUMP_PROGRAM
    compiled_ix = MagicMock()
    compiled_ix.program_id = fake_pump_program
    compiled_ix.data = raw_ix_data
    compiled_ix.accounts = []  # упрощённо

    mock_tx_resp = MagicMock()
    mock_tx_resp.value.transaction.message.instructions = [compiled_ix]

    with patch.object(copy_trader.rpc_client, "get_signatures_for_address", return_value=MagicMock(value=mock_sigs_for_address_value)), \
         patch.object(copy_trader.rpc_client, "get_transaction", return_value=mock_tx_resp):
        await copy_trader.check_new_transactions()

    fake_solana_client.send_sell_transaction.assert_awaited_once()
    fake_solana_client.send_buy_transaction.assert_not_awaited()

    print("[TEST] test_copy_trade_sell_transaction пройден.")