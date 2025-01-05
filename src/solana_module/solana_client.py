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
from .utils import get_bonding_curve_address, find_associated_bonding_curve

from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception,
    RetryError
)

from dotenv import load_dotenv

import httpx  # Используется в обработке исключений

from .config import COMPUTE_UNIT_PRICE
from .utils import get_bonding_curve_address, find_associated_bonding_curve

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

load_dotenv()

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
        return Keypair.from_bytes(bytes([int(i) for i in os.getenv('SECRET_KEY').split(',')]))

    async def create_associated_token_account(self, mint: Pubkey) -> Pubkey:
        """
        Creates an associated token account for the given mint if it doesn't exist.
        """
        associated_token_account = get_associated_token_address(self.payer.pubkey(), mint)
        account_info = await send_request_with_rate_limit(self.client, self.client.get_account_info, associated_token_account)
        if account_info.value is None:
            logger.info("Creating associated token account...")
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
                logger.info(f"ATA Transaction sent: https://explorer.solana.com/tx/{tx_ata_signature.value}")
                await self.confirm_transaction_with_delay(tx_ata_signature.value)
                logger.info(f"Associated token account created: {associated_token_account}")
            except Exception as e:
                logger.error(f"Failed to send ATA transaction: {e}")
                logger.error(traceback.format_exc())
                raise
        else:
            logger.info(f"Associated token account already exists: {associated_token_account}")
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

        for attempt in range(retries):
            try:
                discriminator = struct.pack("<Q", 16927863322537952870)
                token_amount_packed = struct.pack("<Q", int(params['token_amount'] * 10**6))
                max_amount_packed = struct.pack("<Q", params['max_amount_lamports'])
                data = discriminator + token_amount_packed + max_amount_packed

                buy_ix = Instruction(self.PUMP_PROGRAM, data, accounts)
                compute_budget_ix = set_compute_unit_price(self.compute_unit_price)
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
        Waits for transaction confirmation with delay and retry limit.
        """
        logger.info(f"Waiting for transaction confirmation: {signature}")
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Confirmation attempt {attempt + 1} for transaction {signature}")
                response = await send_request_with_rate_limit(self.client, self.client.get_signature_statuses, [signature])
                if not response.value or not response.value[0]:
                    logger.info("Signature status not found. Retrying...")
                    await asyncio.sleep(retry_delay)
                    continue

                signature_status = response.value[0]
                if signature_status.err is not None:
                    error_msg = f"Transaction failed with error: {signature_status.err}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
                if signature_status.confirmation_status:
                    logger.info("Transaction successfully confirmed!")
                    logger.info(f"Current status: {signature_status.confirmation_status}")
                    return True
                logger.info(f"Current status: {signature_status.confirmation_status}. Waiting for finalization...")
                await asyncio.sleep(retry_delay)
                
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception(f"Failed to confirm transaction after {max_retries} attempts: {str(e)}")
                logger.warning(f"Error checking transaction status: {str(e)}. Retrying...")
                await asyncio.sleep(retry_delay)
        
        raise Exception(f"Transaction confirmation timeout after {max_retries} attempts")

    async def buy_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey, amount: float, slippage: float = 0.25):
        """
        Executes token purchase.
        """
        try:
            associated_token_account = await self.create_associated_token_account(mint)
        except Exception as e:
            logger.error(f"Failed to create or verify associated token account: {e}")
            return

        amount_lamports = int(amount * LAMPORTS_PER_SOL)

        # Get token price
        try:
            curve_state = await self.get_pump_curve_state(bonding_curve)
            token_price_sol = self.calculate_pump_curve_price(curve_state)
        except Exception as e:
            logger.error(f"Failed to get or calculate token price: {e}")
            return

        token_amount = amount / token_price_sol

        # Calculate maximum SOL amount with slippage
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
            signature = await self.send_buy_transaction(params)
            return signature  # Return the transaction signature
        except RetryError as re:
            logger.error(f"Failed to execute Buy transaction after retries: {re}")
        except Exception as e:
            logger.error(f"Failed to execute Buy transaction: {e}")

    async def get_pump_curve_state(self, curve_address: Pubkey) -> BondingCurveState:
        """
        Gets bonding curve state by address.
        """
        logger.info(f"Getting bonding curve state for address: {curve_address}")
        response = await send_request_with_rate_limit(self.client, self.client.get_account_info, curve_address)
        if not response.value or not response.value.data:
            logger.error("Invalid curve state: No data")
            raise ValueError("Invalid curve state: No data")

        data = response.value.data
        if data[:8] != EXPECTED_DISCRIMINATOR:
            logger.error("Invalid curve state discriminator")
            raise ValueError("Invalid curve state discriminator")

        return BondingCurveState(data)

    def calculate_pump_curve_price(self, curve_state: BondingCurveState) -> float:
        """
        Calculates token price based on bonding curve state.
        """
        if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
            raise ValueError("Invalid reserves state")

        price = (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)
        logger.info(f"Calculated token price: {price:.10f} SOL")
        return price

    async def get_account_tokens(self, account_pubkey: Pubkey) -> list:
        """
        Gets list of tokens owned by the account.
        """
        logger.info(f"Getting tokens for account: {account_pubkey}")
        response = await send_request_with_rate_limit(self.client, self.client.get_token_accounts_by_owner, account_pubkey, TokenAccountOpts(program_id=self.SYSTEM_TOKEN_PROGRAM))
        
        if not response.value:
            logger.info("No tokens found for this account.")
            return []
        
        tokens = []
        for token_account in response.value:
            token_pubkey = token_account.pubkey
            token_info = await send_request_with_rate_limit(self.client, self.client.get_account_info, token_pubkey)
            if token_info.value and token_info.value.data:
                tokens.append(token_pubkey)
        logger.info(f"Found {len(tokens)} tokens for account {account_pubkey}")
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
        min_sol_output = int(float(token_balance_decimal) * float(token_price_sol) * (1 - 0.3)) #0.3 is slipage
        
        logger.info(f"Selling {token_balance_decimal} tokens")
        logger.info(f"Minimum SOL output: {min_sol_output / LAMPORTS_PER_SOL:.10f} SOL")

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

                logger.info(f"Transaction sent: https://explorer.solana.com/tx/{tx_sell_signature.value}")

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



