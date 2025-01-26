# solana_module/solana_client.py

import asyncio
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
from solders.signature import Signature
from solders.instruction import Instruction, AccountMeta
from solders.compute_budget import set_compute_unit_price
from solana.transaction import Transaction
import spl.token.instructions as spl_token
from spl.token.instructions import get_associated_token_address
from construct import Struct, Int64ul, Flag

from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception,
    RetryError
)

from dotenv import load_dotenv

import httpx  # Используется в обработке исключений

# COMPUTE_UNIT_PRICE  # todo change to select from bd

from typing import Optional, Dict, Union

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
rate_limiter = RateLimiter(max_calls=1, period=1.0)  # More conservative rate limit

# Добавляем глобальный rate limiter для всех клиентов
global_rate_limiter = RateLimiter(max_calls=5, period=1.0)

async def send_request_with_rate_limit(client: AsyncClient, request_func, *args, **kwargs):
    """Send request with both per-client and global rate limiting"""
    max_retries = 5
    base_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            await rate_limiter.acquire()
            await global_rate_limiter.acquire()
            return await request_func(*args, **kwargs)
        except Exception as e:
            if not is_rate_limit_error(e) or attempt == max_retries - 1:
                raise
            
            # Exponential backoff
            delay = base_delay * (2 ** attempt)
            logger.info(f"Rate limit hit, retrying in {delay:.2f} seconds...")
            await asyncio.sleep(delay)
    
    raise Exception("Failed after max retries")

def is_rate_limit_error(exception):
    """Check if the exception is a rate limit error"""
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code == 429
    # Also check for RPC node specific rate limit errors
    if isinstance(exception, Exception):
        error_msg = str(exception).lower()
        return any(msg in error_msg for msg in [
            "rate limit exceeded",
            "too many requests",
            "please slow down",
            "429"
        ])
    return False

class SolanaClient:
    def __init__(self, compute_unit_price: int, private_key: Optional[str] = None):
        self.rpc_endpoint = os.getenv("SOLANA_RPC_URL", "https://solana-mainnet.core.chainstack.com/1477348d5255a5a82def1ba221b5a610")
        self.compute_unit_price = compute_unit_price
        self.client = AsyncClient(self.rpc_endpoint)
        self._private_key = private_key
        self.payer = None  # Will be set on first use

        # Program addresses
        self.PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
        self.PUMP_GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
        self.PUMP_EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
        self.PUMP_FEE = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
        self.SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
        self.SYSTEM_TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        self.SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM = Pubkey.from_string(
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
        self.SYSTEM_RENT = Pubkey.from_string("SysvarRent111111111111111111111111111111111")
        self.SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")

    def load_keypair(self) -> Keypair:
        """
        Loads keypair from provided private key
        Raises ValueError if no private key was provided
        """
        try:
            if not self.payer:
                if not self._private_key:
                    logger.error("[CLIENT] No private key provided")
                    raise ValueError("Private key is required for transaction signing")

                logger.info("[CLIENT] Loading keypair from provided private key")
                logger.debug(
                    f"[CLIENT] Private key string: {self._private_key[:20]}...")  # Log first 20 chars for debugging
                logger.debug(f"[CLIENT] Private key string length: {len(self._private_key)}")

                try:
                    # Split and convert to integers
                    key_parts = self._private_key.split(',')
                    logger.debug(f"[CLIENT] Split private key into {len(key_parts)} parts")

                    key_bytes = [int(i) for i in key_parts]
                    logger.debug(f"[CLIENT] Converted to bytes array with length: {len(key_bytes)}")

                    if len(key_bytes) != 64:
                        logger.error(f"[CLIENT] Invalid key length: {len(key_bytes)} (expected 64)")
                        raise ValueError(f"Invalid private key length: {len(key_bytes)}")

                except Exception as e:
                    logger.error(f"[CLIENT] Failed to parse private key string: {str(e)}")
                    logger.error(f"[CLIENT] Key parts: {key_parts[:3]}... (showing first 3 parts)")
                    raise ValueError("Failed to parse private key string") from e

                try:
                    key_bytes_obj = bytes(key_bytes)
                    logger.debug(f"[CLIENT] Created bytes object with length: {len(key_bytes_obj)}")

                    self.payer = Keypair.from_bytes(key_bytes_obj)
                    logger.info(f"[CLIENT] Keypair loaded successfully. Public key: {self.payer.pubkey()}")
                except Exception as e:
                    logger.error(f"[CLIENT] Failed to create keypair from bytes: {str(e)}")
                    logger.error(f"[CLIENT] First few bytes: {key_bytes_obj[:10] if key_bytes_obj else None}")
                    raise ValueError("Failed to create keypair from bytes") from e

            return self.payer

        except Exception as e:
            logger.error(f"[CLIENT] Error loading keypair: {str(e)}")
            logger.error(f"[CLIENT] Error type: {type(e).__name__}")
            logger.error(traceback.format_exc())
            raise

    async def create_associated_token_account(self, mint: Pubkey) -> Pubkey:
        """Creates associated token account for given mint if it doesn't exist."""
        associated_token_account = get_associated_token_address(self.payer.pubkey(), mint)
        account_info = await send_request_with_rate_limit(self.client, self.client.get_account_info,
                                                          associated_token_account)
        if account_info.value is None:
            logger.info("Creating associated token account...")
            create_ata_ix = spl_token.create_associated_token_account(
                payer=self.payer.pubkey(),
                owner=self.payer.pubkey(),
                mint=mint
            )
            compute_budget_ix = set_compute_unit_price(int(self.compute_unit_price))
            tx_ata = Transaction().add(create_ata_ix).add(compute_budget_ix)
            tx_ata.recent_blockhash = (
                await send_request_with_rate_limit(self.client, self.client.get_latest_blockhash)).value.blockhash
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
                logger.info(f"Attempting to send Buy transaction {attempt + 1} of {retries}")

                discriminator = struct.pack("<Q", 16927863322537952870)
                token_amount_packed = struct.pack("<Q", int(params['token_amount'] * 10 ** TOKEN_DECIMALS))
                max_amount_packed = struct.pack("<Q", int(params['max_amount_lamports']))
                data = discriminator + token_amount_packed + max_amount_packed

                buy_ix = Instruction(self.PUMP_PROGRAM, data, accounts)
                compute_budget_ix = set_compute_unit_price(int(self.compute_unit_price))

                tx_buy = Transaction().add(buy_ix).add(compute_budget_ix)
                tx_buy.recent_blockhash = (
                    await send_request_with_rate_limit(self.client, self.client.get_latest_blockhash)).value.blockhash
                tx_buy.fee_payer = self.payer.pubkey()
                tx_buy.sign(self.payer)

                tx_buy_signature = await send_request_with_rate_limit(
                    self.client,
                    self.client.send_transaction,
                    tx_buy,
                    self.payer,
                    opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
                )

                logger.info(f"Buy Transaction sent: https://explorer.solana.com/tx/{tx_buy_signature.value}")

                # Ожидание подтверждения с увеличенным таймаутом и повторными попытками
                await self.confirm_transaction_with_delay(
                    tx_buy_signature.value,
                    max_retries=15,
                    retry_delay=6
                )

                logger.info(f"Buy transaction confirmed: {tx_buy_signature.value}")
                return tx_buy_signature.value

            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"Failed to send Buy transaction: {str(e)}")
                    raise

                logger.warning(f"Transaction attempt {attempt + 1} failed: {str(e)}. Retrying...")
                await asyncio.sleep(2 * (attempt + 1))  # Exponential backoff

        raise Exception("Failed to send transaction after all attempts")

    async def confirm_transaction_with_delay(self, signature: str, max_retries: int = 10, retry_delay: int = 5):
        """
        Waits for transaction confirmation with delay and retry limit.
        """
        logger.info(f"Waiting for transaction confirmation: {signature}")

        for attempt in range(max_retries):
            try:
                logger.info(f"Confirmation attempt {attempt + 1} for transaction {signature}")
                response = await send_request_with_rate_limit(self.client, self.client.get_signature_statuses,
                                                              [signature])
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

    async def buy_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey, amount: float,
                        slippage: float = 0.25):
        """Executes token purchase."""
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
        """Gets bonding curve state by address."""
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
        """Calculates token price based on bonding curve state."""
        if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
            raise ValueError("Invalid reserves state")

        price = (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (
                curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)
        logger.info(f"Calculated token price: {price:.10f} SOL")
        return price

    async def get_account_tokens(self, account_pubkey: Pubkey) -> list:
        """Gets list of tokens owned by the account."""
        logger.info(f"Getting tokens for account: {account_pubkey}")
        response = await send_request_with_rate_limit(self.client, self.client.get_token_accounts_by_owner,
                                                      account_pubkey,
                                                      TokenAccountOpts(program_id=self.SYSTEM_TOKEN_PROGRAM))

        if not response.value:
            logger.info("No tokens found for this account")
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
        """Sends sell transaction."""
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

        resp = await self.client.get_token_account_balance(params['associated_token_account'])
        token_balance = int(resp.value.amount)
        token_balance_decimal = token_balance / 10 ** TOKEN_DECIMALS
        curve_state = await self.get_pump_curve_state(params['bonding_curve'])
        token_price_sol = self.calculate_pump_curve_price(curve_state)

        # Convert token amount to integer with decimals
        amount = int(params['token_amount'] * 10 ** TOKEN_DECIMALS)
        min_sol_output = int(float(token_balance_decimal) * float(token_price_sol) * LAMPORTS_PER_SOL * (1 - 0.3))

        logger.info(f"Selling {token_balance_decimal} tokens")
        logger.info(f"Minimum SOL output: {min_sol_output / LAMPORTS_PER_SOL:.10f} SOL")

        for attempt in range(retries):
            try:
                logger.info(f"Attempting to send Sell transaction {attempt + 1} of {retries}")
                discriminator = struct.pack("<Q", 12502976635542562355)
                data = discriminator + struct.pack("<Q", amount) + struct.pack("<Q", min_sol_output)
                sell_ix = Instruction(self.PUMP_PROGRAM, data, accounts)

                recent_blockhash = await self.client.get_latest_blockhash()
                transaction = Transaction()
                transaction.add(sell_ix).add(set_compute_unit_price(int(self.compute_unit_price)))
                transaction.recent_blockhash = recent_blockhash.value.blockhash
                transaction.fee_payer = self.payer.pubkey()
                transaction.sign(self.payer)

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

                logger.info(f"Sell transaction confirmed: {tx_sell_signature.value}")
                return tx_sell_signature.value

            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"Failed to send Sell transaction: {str(e)}")
                    raise

                logger.warning(f"Transaction attempt {attempt + 1} failed: {str(e)}. Retrying...")
                await asyncio.sleep(2 * (attempt + 1))  # Exponential backoff

        raise Exception("Failed to send transaction after all attempts")

    async def sell_token(self, mint: Pubkey, bonding_curve: Pubkey, associated_bonding_curve: Pubkey,
                         token_amount: float, min_amount: float = 0.25):
        """Executes token sale."""
        try:
            associated_token_account = await self.create_associated_token_account(mint)
        except Exception as e:
            logger.error(f"Failed to create or verify associated token account: {e}")
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
            return await self.send_sell_transaction(params)
        except RetryError as re:
            logger.error(f"Failed to execute Sell transaction after retries: {re}")
        except Exception as e:
            logger.error(f"Failed to execute Sell transaction: {e}")

    async def get_token_balance(self, token_address: Pubkey) -> float:
        """
        Gets token balance for the associated token account.
        Returns balance in decimal format.
        """
        try:
            associated_token_account = await self.create_associated_token_account(token_address)
            response = await send_request_with_rate_limit(self.client, self.client.get_token_account_balance,
                                                          associated_token_account)
            if response.value:
                return float(response.value.amount) / 10 ** TOKEN_DECIMALS
            return 0
        except Exception as e:
            logger.error(f"Failed to get token balance: {e}")
            return 0

    async def buy_token_by_signature(self, signature: str):
        """
        Повторяет покупку токена, используя параметры из указанной транзакции.
        """
        try:
            # Получение информации о транзакции
            transaction_info = await send_request_with_rate_limit(self.client, self.client.get_transaction, signature)
            if not transaction_info or not transaction_info.value:
                logger.error("Не удалось получить данные транзакции.")
                return

            # Парсинг параметров транзакции
            transaction_data = transaction_info.value.transaction
            instructions = transaction_data.message.instructions

            for ix in instructions:
                if ix.program_id == self.PUMP_PROGRAM:
                    # Распаковка данных транзакции
                    discriminator, token_amount_packed, max_amount_packed = struct.unpack("<QQQ", bytes(ix.data))
                    if discriminator != 16927863322537952870:
                        logger.error("Подпись не соответствует ожидаемой транзакции покупки.")
                        return

                    token_amount = token_amount_packed / (10 ** TOKEN_DECIMALS)
                    max_amount = max_amount_packed

                    # Получение аккаунтов из транзакции
                    accounts = [AccountMeta(pubkey=Pubkey(acc), is_signer=False, is_writable=False) for acc in
                                ix.accounts]
                    mint = accounts[2].pubkey
                    bonding_curve = accounts[3].pubkey
                    associated_bonding_curve = accounts[4].pubkey

                    # Повторная покупка токенов
                    return await self.buy_token(
                        mint=mint,
                        bonding_curve=bonding_curve,
                        associated_bonding_curve=associated_bonding_curve,
                        amount=token_amount,
                        slippage=0.25
                    )

        except Exception as e:
            logger.error(f"Ошибка повторного выполнения покупки токенов: {e}")

    async def sell_token_by_signature(self, signature: str):
        """
        Повторяет продажу токена, используя параметры из указанной транзакции.
        """
        try:
            # Получение информации о транзакции
            transaction_info = await send_request_with_rate_limit(self.client, self.client.get_transaction, signature)
            if not transaction_info or not transaction_info.value:
                logger.error("Не удалось получить данные транзакции.")
                return

            # Парсинг параметров транзакции
            transaction_data = transaction_info.value.transaction
            instructions = transaction_data.message.instructions

            for ix in instructions:
                if ix.program_id == self.PUMP_PROGRAM:
                    # Распаковка данных транзакции
                    discriminator, token_amount_packed, min_sol_output_packed = struct.unpack("<QQQ", bytes(ix.data))
                    if discriminator != 12502976635542562355:
                        logger.error("Подпись не соответствует ожидаемой транзакции продажи.")
                        return

                    token_amount = token_amount_packed / (10 ** TOKEN_DECIMALS)
                    min_sol_output = min_sol_output_packed / LAMPORTS_PER_SOL

                    # Получение аккаунтов из транзакции
                    accounts = [AccountMeta(pubkey=Pubkey(acc), is_signer=False, is_writable=False) for acc in
                                ix.accounts]
                    mint = accounts[2].pubkey
                    bonding_curve = accounts[3].pubkey
                    associated_bonding_curve = accounts[4].pubkey

                    # Повторная продажа токенов
                    return await self.sell_token(
                        mint=mint,
                        bonding_curve=bonding_curve,
                        associated_bonding_curve=associated_bonding_curve,
                        token_amount=token_amount,
                        min_amount=min_sol_output
                    )

        except Exception as e:
            logger.error(f"Ошибка повторного выполнения продажи токенов: {e}")

    async def get_transaction(self, signature: Union[str, Signature]) -> Optional[Dict]:
        """
        Get transaction information by signature
        
        Args:
            signature: Transaction signature as string or Signature object
        """
        try:
            logger.info(f"[CLIENT] Getting transaction info for signature: {signature}")

            # Convert string signature to Signature object if needed
            if isinstance(signature, str):
                signature_obj = Signature.from_string(signature)
            else:
                signature_obj = signature  # Already a Signature object

            # Get transaction info
            tx_info = await send_request_with_rate_limit(
                self.client,
                self.client.get_transaction,
                signature_obj
            )

            if not tx_info or not tx_info.value:
                logger.error(f"[CLIENT] No transaction info found for signature: {signature}")
                return None

            logger.info(f"[CLIENT] Successfully retrieved transaction info")

            # Extract pre and post balances
            pre_balances = tx_info.value.transaction.meta.pre_balances
            post_balances = tx_info.value.transaction.meta.post_balances

            # Extract mint address from transaction
            token_address = None
            if tx_info.value.transaction.transaction.message.account_keys:
                # В BUY транзакции mint находится в account_keys[11]
                # Это можно увидеть из логов, где mint = "55YanwmkJQrk2SiZRKNKVbLVz7Ht33zg6RU7uYvipump"
                # token_address = str(tx_info.value.transaction.transaction.message.account_keys[11])
                # logger.info(f"[CLIENT] Extracted token address: {token_address}")

                for account_key in tx_info.value.transaction.transaction.message.account_keys:
                    #logger.info(f"[CLIENT] Account key: {account_key}")
                    if check_mint(account_key):
                        token_address = account_key
                        logger.info(f"[CLIENT] Found token address: {token_address}")
                        break
                    else:
                        #logger.info(f"[CLIENT] Account key is not a mint: {account_key}")
                        pass

            # Convert to dict before JSON serialization
            tx_info_dict = {
                "amount_sol": abs(pre_balances[0] - post_balances[0]) if pre_balances and post_balances else 0,
                "token_address": token_address,
                "raw_data": {
                    "pre_balances": pre_balances,
                    "post_balances": post_balances,
                    "slot": tx_info.value.slot,
                    "block_time": tx_info.value.block_time
                }
            }

            # logger.debug(f"[CLIENT] Transaction info: {json.dumps(tx_info_dict, indent=2)}")
            return tx_info_dict

        except Exception as e:
            logger.error(f"[CLIENT] Error getting transaction info: {str(e)}")
            logger.error(traceback.format_exc())
            return None

    async def get_sol_balance(self, wallet_address: str) -> float:
        """
        Get SOL balance for a wallet
        
        Args:
            wallet_address: The wallet address to check balance for
            
        Returns:
            float: Balance in SOL
        """
        try:
            logger.info(f"[CLIENT] Getting SOL balance for wallet: {wallet_address}")

            # Convert address string to Pubkey
            pubkey = Pubkey.from_string(wallet_address)

            # Get balance
            balance = await send_request_with_rate_limit(
                self.client,
                self.client.get_balance,
                pubkey
            )

            if balance is None:
                logger.error(f"[CLIENT] Failed to get balance for wallet: {wallet_address}")
                return 0

            # Convert lamports to SOL
            balance_sol = balance.value / LAMPORTS_PER_SOL
            logger.info(f"[CLIENT] Wallet {wallet_address} has {balance_sol} SOL")

            return balance_sol

        except Exception as e:
            logger.error(f"[CLIENT] Error getting SOL balance: {str(e)}")
            logger.error(f"[CLIENT] Error type: {type(e).__name__}")
            import traceback
            logger.error(f"[CLIENT] Traceback: {traceback.format_exc()}")
            return 0
        
    async def get_tokens(self, wallet_address) -> float:
        #sewallet_address = Pubkey.from_string(wallet_address)
        print(await self.client.get_account_info(wallet_address))


def check_mint(account: Pubkey) -> bool:
    """
    Check if the given account is a token mint.
    This checks various conditions to identify token addresses in different transaction types.
    
    Args:
        account: The account to check
    
    Returns:
        bool: True if the account is likely a token mint, False otherwise
    """
    try:
        # Known non-token program IDs to exclude
        EXCLUDED_PROGRAMS = {
            "11111111111111111111111111111111",  # System Program
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token Program
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token Program
            "So11111111111111111111111111111111111111112",  # Wrapped SOL
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PUMP Program
            "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",  # PUMP Global
            "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",  # PUMP Event Authority
            "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM",  # PUMP Fee
            "SysvarRent111111111111111111111111111111111",  # Rent Program
        }

        # Convert account to string for comparison
        account_str = str(account)

        # Skip known program IDs
        if account_str in EXCLUDED_PROGRAMS:
            return False

        # Check if account ends with 'pump' (common for pump tokens)
        if account_str.lower().endswith('pump'):
            logger.info(f"[CLIENT] Found pump token: {account_str}")
            return True

        # Additional checks can be added here based on token patterns
        # For example, checking account string length, specific prefixes, etc.

        return False

    except Exception as e:
        logger.error(f"[CLIENT] Error in check_mint: {str(e)}")
        return False
