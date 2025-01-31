import requests
import struct
import base64
import os
import time
from typing import Optional
from enum import Enum
from dataclasses import dataclass

from solders.pubkey import Pubkey
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.message import MessageV0
from solders.system_program import (
    CreateAccountWithSeedParams,
    create_account_with_seed,
)
from solders.transaction import VersionedTransaction
from solders.transaction_status import TransactionConfirmationStatus

from solana.rpc.commitment import Processed, Confirmed
from solana.rpc.types import TokenAccountOpts, TxOpts

from spl.token.client import Token
from spl.token.instructions import (
    CloseAccountParams,
    InitializeAccountParams,
    create_associated_token_account,
    get_associated_token_address,
    initialize_account,
    close_account,
)

from src.solana_module.raydium.constants import (
    WSOL,
    TOKEN_PROGRAM_ID,
    RAYDIUM_AMM_V4,
    DEFAULT_QUOTE_MINT,
    SOL_DECIMAL,
    ACCOUNT_LAYOUT_LEN,
)

MINIMUM_TRANSACTION_FEE = 5000
from solana.rpc.api import Client as SomeSolanaClient
import requests
from dotenv import load_dotenv
from solders.keypair import Keypair

load_dotenv()

import os

def get_pool_info_by_id(pool_id: str) -> dict:
    base_url = "https://api-v3.raydium.io/pools/info/ids"
    params = {"ids": pool_id}
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch pool info: {e}"}

def get_pool_info_by_mint(mint: str, pool_type: str = "all", sort_field: str = "default", 
                              sort_type: str = "desc", page_size: int = 100, page: int = 1) -> dict:
    base_url = "https://api-v3.raydium.io/pools/info/mint"
    params = {
        "mint1": mint,
        "poolType": pool_type,
        "poolSortField": sort_field,
        "sortType": sort_type,
        "pageSize": page_size,
        "page": page
    }

    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch pair address: {e}"}


def get_pool(mint):
    pool_id = "5phQt8oA1fwKDq1pLJ2E2swozfs7dgDH78iLuoUjAYhM"
    pool_info = get_pool_info_by_id(pool_id)

    if 'data' in pool_info and pool_info['data']:
        pool = pool_info['data'][0]
        print(f"Pool Info for: {pool_id}")
        print(f" - Pool ID: {pool.get('id', 'N/A')}")
        print(f" - Mint A Address: {pool['mintA'].get('address', 'N/A')}")
        print(f" - Mint B Address: {pool['mintB'].get('address', 'N/A')}")
    else:
        print("No data found for the given pool ID.")

    print("------------------------------------")
    pool_info = get_pool_info_by_mint(mint)

    if 'data' in pool_info and 'data' in pool_info['data']:
        print(f"Pools for Mint: {mint}")
        for pool in pool_info['data']['data']:
            if pool.get('type', 'N/A') == 'Standard':
                print(pool.get('id', 'N/A'))
                return pool.get('id', 'N/A')
    else:
        print(f"No pools found for the mint address: {mint}")



class RaydiumAmmV4:
    """
    Example class for managing Raydium AMM v4 operations.
    Certain references (client, payer_keypair, etc.) are declared as class/static
    attributes here, but you should replace them with your actual Solana client/keypair.
    """

    # -------------------------------------------------------------------------
    # Demonstration placeholders for client, payer, etc.
    # Replace them with your real Solana client & payer keypair in practice.
    # -------------------------------------------------------------------------
    client = SomeSolanaClient("https://api.mainnet-beta.solana.com")

    payer_keypair = None
    UNIT_BUDGET = 1_400_000  # Example compute budget
    UNIT_PRICE = 200_000          # Example unit price

    # -------------------------------------------------------------------------
    # Layouts for AMM V4 decoding
    # -------------------------------------------------------------------------
    from src.solana_module.layouts.amm_v4 import LIQUIDITY_STATE_LAYOUT_V4, MARKET_STATE_LAYOUT_V3

    @dataclass
    class AmmV4PoolKeys:
        amm_id: Pubkey
        base_mint: Pubkey
        quote_mint: Pubkey
        base_decimals: int
        quote_decimals: int
        open_orders: Pubkey
        target_orders: Pubkey
        base_vault: Pubkey
        quote_vault: Pubkey
        market_id: Pubkey
        market_authority: Pubkey
        market_base_vault: Pubkey
        market_quote_vault: Pubkey
        bids: Pubkey
        asks: Pubkey
        event_queue: Pubkey
        ray_authority_v4: Pubkey
        open_book_program: Pubkey
        token_program_id: Pubkey

    @staticmethod
    def get_pool_info_by_id(pool_id: str) -> dict:
        """Fetch Raydium pool info by pool ID."""
        base_url = "https://api-v3.raydium.io/pools/info/ids"
        params = {"ids": pool_id}
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"Failed to fetch pool info: {e}"}

    @staticmethod
    def get_pool_info_by_mint(
        mint: str,
        pool_type: str = "all",
        sort_field: str = "default",
        sort_type: str = "desc",
        page_size: int = 100,
        page: int = 1
    ) -> dict:
        """Fetch Raydium pool info by mint."""
        base_url = "https://api-v3.raydium.io/pools/info/mint"
        params = {
            "mint1": mint,
            "poolType": pool_type,
            "poolSortField": sort_field,
            "sortType": sort_type,
            "pageSize": page_size,
            "page": page
        }
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"Failed to fetch pair address: {e}"}

    # -------------------------------------------------------------------------
    # Fetch AmmV4 Pool Keys
    # -------------------------------------------------------------------------
    @staticmethod
    def fetch_amm_v4_pool_keys(pair_address: str) -> Optional['RaydiumAmmV4.AmmV4PoolKeys']:
        """
        Fetch on-chain data for an AMM V4 pool and decode the layouts.
        """
        def bytes_of(value: int) -> bytes:
            if not (0 <= value < 2**64):
                raise ValueError("Value must be in the range of a u64 (0 to 2^64 - 1).")
            return struct.pack('<Q', value)

        try:
            if not RaydiumAmmV4.client:
                raise ValueError("RaydiumAmmV4.client is not set to a valid Solana client.")

            amm_id = Pubkey.from_string(pair_address)
            amm_info = RaydiumAmmV4.client.get_account_info_json_parsed(
                amm_id, commitment=Processed
            ).value

            if not amm_info or not amm_info.data:
                raise ValueError("AMM account data is missing or invalid.")

            amm_data = amm_info.data
            amm_data_decoded = RaydiumAmmV4.LIQUIDITY_STATE_LAYOUT_V4.parse(amm_data)
            market_id = Pubkey.from_bytes(amm_data_decoded.serumMarket)

            market_info_resp = RaydiumAmmV4.client.get_account_info_json_parsed(
                market_id, commitment=Processed
            ).value
            if not market_info_resp or not market_info_resp.data:
                raise ValueError("Market account data is missing or invalid.")

            market_decoded = RaydiumAmmV4.MARKET_STATE_LAYOUT_V3.parse(market_info_resp.data)
            vault_signer_nonce = market_decoded.vault_signer_nonce

            # Hard-coded Raydium addresses
            ray_authority_v4 = Pubkey.from_string("5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1")
            open_book_program = Pubkey.from_string("srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX")
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

            pool_keys = RaydiumAmmV4.AmmV4PoolKeys(
                amm_id=amm_id,
                base_mint=Pubkey.from_bytes(market_decoded.base_mint),
                quote_mint=Pubkey.from_bytes(market_decoded.quote_mint),
                base_decimals=amm_data_decoded.coinDecimals,
                quote_decimals=amm_data_decoded.pcDecimals,
                open_orders=Pubkey.from_bytes(amm_data_decoded.ammOpenOrders),
                target_orders=Pubkey.from_bytes(amm_data_decoded.ammTargetOrders),
                base_vault=Pubkey.from_bytes(amm_data_decoded.poolCoinTokenAccount),
                quote_vault=Pubkey.from_bytes(amm_data_decoded.poolPcTokenAccount),
                market_id=market_id,
                market_authority=Pubkey.create_program_address(
                    seeds=[bytes(market_id), bytes_of(vault_signer_nonce)],
                    program_id=open_book_program
                ),
                market_base_vault=Pubkey.from_bytes(market_decoded.base_vault),
                market_quote_vault=Pubkey.from_bytes(market_decoded.quote_vault),
                bids=Pubkey.from_bytes(market_decoded.bids),
                asks=Pubkey.from_bytes(market_decoded.asks),
                event_queue=Pubkey.from_bytes(market_decoded.event_queue),
                ray_authority_v4=ray_authority_v4,
                open_book_program=open_book_program,
                token_program_id=token_program_id
            )
            return pool_keys

        except Exception as e:
            print(f"Error fetching pool keys: {e}")
            return None

    # -------------------------------------------------------------------------
    # Make AMM V4 Swap Instruction
    # -------------------------------------------------------------------------
    @staticmethod
    def make_amm_v4_swap_instruction(
        amount_in: int,
        minimum_amount_out: int,
        token_account_in: Pubkey,
        token_account_out: Pubkey,
        accounts: 'RaydiumAmmV4.AmmV4PoolKeys',
        owner: Pubkey
    ):
        """
        Creates the Instruction object for swapping on Raydium AMM V4.
        """
        from solders.instruction import AccountMeta, Instruction

        try:
            keys = [
                AccountMeta(pubkey=accounts.token_program_id, is_signer=False, is_writable=False),
                AccountMeta(pubkey=accounts.amm_id, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.ray_authority_v4, is_signer=False, is_writable=False),
                AccountMeta(pubkey=accounts.open_orders, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.target_orders, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.base_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.quote_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.open_book_program, is_signer=False, is_writable=False),
                AccountMeta(pubkey=accounts.market_id, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.bids, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.asks, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.event_queue, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.market_base_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.market_quote_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=accounts.market_authority, is_signer=False, is_writable=False),
                AccountMeta(pubkey=token_account_in, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_account_out, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
            ]

            # Instruction data layout:
            #   [0] = discriminator (u8) = 9 for Raydium AMM V4 swap
            #   [1..8] = amount_in (u64)
            #   [9..16] = min_amount_out (u64)
            data = bytearray()
            discriminator = 9
            data.extend(struct.pack('<B', discriminator))
            data.extend(struct.pack('<Q', amount_in))
            data.extend(struct.pack('<Q', minimum_amount_out))

            swap_instruction = Instruction(
                program_id=RAYDIUM_AMM_V4,
                data=bytes(data),
                accounts=keys
            )
            return swap_instruction

        except Exception as e:
            print(f"Error occurred: {e}")
            return None

    # -------------------------------------------------------------------------
    # Get AmmV4 Reserves
    # -------------------------------------------------------------------------
    @staticmethod
    def get_amm_v4_reserves(pool_keys: 'RaydiumAmmV4.AmmV4PoolKeys') -> tuple:
        """
        Fetch vault balances from the pool vault accounts.
        Returns (base_reserve, quote_reserve, token_decimal).
        """
        try:
            if not RaydiumAmmV4.client:
                raise ValueError("RaydiumAmmV4.client is not set to a valid Solana client.")

            quote_vault = pool_keys.quote_vault
            quote_decimal = pool_keys.quote_decimals
            quote_mint = pool_keys.quote_mint

            base_vault = pool_keys.base_vault
            base_decimal = pool_keys.base_decimals
            base_mint = pool_keys.base_mint

            balances_response = RaydiumAmmV4.client.get_multiple_accounts_json_parsed(
                [quote_vault, base_vault],
                Processed
            )
            if not balances_response or not balances_response.value:
                raise ValueError("Failed to fetch vault account balances.")

            quote_account = balances_response.value[0]
            base_account = balances_response.value[1]

            quote_account_balance = (
                quote_account.data.parsed['info']['tokenAmount']['uiAmount']
                if quote_account and quote_account.data else None
            )
            base_account_balance = (
                base_account.data.parsed['info']['tokenAmount']['uiAmount']
                if base_account and base_account.data else None
            )

            if quote_account_balance is None or base_account_balance is None:
                print("Error: One of the account balances is None.")
                return None, None, None

            # If the base mint is WSOL, interpret base_vault as actually holding SOL.
            if base_mint == WSOL:
                base_reserve = quote_account_balance
                quote_reserve = base_account_balance
                token_decimal = quote_decimal
            else:
                base_reserve = base_account_balance
                quote_reserve = quote_account_balance
                token_decimal = base_decimal

            print(f"Base Mint: {base_mint} | Quote Mint: {quote_mint}")
            print(f"Base Reserve: {base_reserve} | Quote Reserve: {quote_reserve} | Token Decimal: {token_decimal}")
            return base_reserve, quote_reserve, token_decimal

        except Exception as e:
            print(f"Error occurred: {e}")
            return None, None, None

    # -------------------------------------------------------------------------
    # Simplistic constant-product math helpers for local estimates
    # -------------------------------------------------------------------------
    @staticmethod
    def sol_for_tokens(sol_amount: float, base_vault_balance: float, quote_vault_balance: float, swap_fee: float = 0.25):
        """
        Approx how many base tokens we'd get for a given sol_amount,
        using a constant-product model with a swap_fee %.
        """
        effective_sol_used = sol_amount - (sol_amount * (swap_fee / 100))
        constant_product = base_vault_balance * quote_vault_balance
        updated_base_vault_balance = constant_product / (quote_vault_balance + effective_sol_used)
        tokens_received = base_vault_balance - updated_base_vault_balance
        return round(tokens_received, 9)

    @staticmethod
    def tokens_for_sol(token_amount: float, base_vault_balance: float, quote_vault_balance: float, swap_fee: float = 0.25):
        """
        Approx how many SOL we'd get for a given token_amount,
        using a constant-product model with a swap_fee %.
        """
        effective_tokens_sold = token_amount * (1 - (swap_fee / 100))
        constant_product = base_vault_balance * quote_vault_balance
        updated_quote_vault_balance = constant_product / (base_vault_balance + effective_tokens_sold)
        sol_received = quote_vault_balance - updated_quote_vault_balance
        return round(sol_received, 9)

    # -------------------------------------------------------------------------
    # Utility for retrieving token balance by mint
    # -------------------------------------------------------------------------
    @staticmethod
    def get_token_balance(mint_str: str) -> Optional[float]:
        """
        Return the first account balance for the given mint, if it exists.
        """
        if not RaydiumAmmV4.client or not RaydiumAmmV4.payer_keypair:
            raise ValueError("RaydiumAmmV4.client or RaydiumAmmV4.payer_keypair is not defined.")

        response = RaydiumAmmV4.client.get_token_accounts_by_owner_json_parsed(
            RaydiumAmmV4.payer_keypair.pubkey(),
            TokenAccountOpts(mint=Pubkey.from_string(mint_str)),
            commitment=Processed
        )

        if response.value:
            accounts = response.value
            if accounts:
                token_amount = accounts[0].account.data.parsed['info']['tokenAmount']['uiAmount']
                if token_amount:
                    return float(token_amount)
        return None

    # -------------------------------------------------------------------------
    # Confirm transaction with retries
    # -------------------------------------------------------------------------
    @staticmethod
    def confirm_txn(txn_sig: str, max_retries: int = 40, retry_interval: int = 3) -> bool:
        if not RaydiumAmmV4.client:
            raise ValueError("RaydiumAmmV4.client is not set to a valid Solana client.")

        retries = 0
        while retries < max_retries:
            try:
                status_res = RaydiumAmmV4.client.get_signature_statuses([txn_sig])
                status = status_res.value[0]
                if status:
                    print(f"Transaction status: {status}")
                    if status.confirmation_status == TransactionConfirmationStatus.Finalized:
                        print("Transaction finalized.")
                        return True
                    elif status.err:
                        print(f"Transaction failed with error: {status.err}")
                        return False
            except Exception as e:
                print(f"Error checking signature status: {e}")
            retries += 1
            print(f"Retry {retries}/{max_retries}...")
            time.sleep(retry_interval)
        print("Transaction not confirmed within the retry limit.")
        return False

    # -------------------------------------------------------------------------
    # Example "buy" function (swapping SOL -> some token)
    # -------------------------------------------------------------------------
    @staticmethod
    def buy(pair_address: str, sol_in: float = 0.01, slippage: int = 5) -> bool:
        """
        Buys the 'other' token side from the pool using SOL as input (wrapped as WSOL).
        If base_mint == WSOL, we interpret that we are actually buying the quote_mint, otherwise base_mint.
        """
        try:
            print(f"Starting buy transaction for pair address: {pair_address}")

            if not RaydiumAmmV4.client or not RaydiumAmmV4.payer_keypair:
                raise ValueError("client or payer_keypair not set on RaydiumAmmV4.")

            print("Fetching pool keys...")
            pool_keys = RaydiumAmmV4.fetch_amm_v4_pool_keys(pair_address)
            if pool_keys is None:
                print("No pool keys found...")
                return False
            print("Pool keys fetched successfully.")

            # Decide which mint we are actually buying
            mint = (
                pool_keys.base_mint
                if pool_keys.base_mint != WSOL
                else pool_keys.quote_mint
            )

            print("Calculating transaction amounts...")
            amount_in = int(sol_in * SOL_DECIMAL)

            base_reserve, quote_reserve, token_decimal = RaydiumAmmV4.get_amm_v4_reserves(pool_keys)
            if base_reserve is None or quote_reserve is None:
                print("Error fetching pool reserves.")
                return False

            amount_out_estimate = RaydiumAmmV4.sol_for_tokens(sol_in, base_reserve, quote_reserve)
            print(f"Estimated Amount Out: {amount_out_estimate}")

            slippage_adjustment = 1 - (slippage / 100)
            amount_out_with_slippage = amount_out_estimate * slippage_adjustment
            minimum_amount_out = int(amount_out_with_slippage * (10 ** token_decimal))
            print(f"Amount In (lamports): {amount_in} | Minimum Amount Out: {minimum_amount_out}")

            # Check if we already have an associated token account
            resp = RaydiumAmmV4.client.get_token_accounts_by_owner(
                RaydiumAmmV4.payer_keypair.pubkey(),
                TokenAccountOpts(mint=mint),
                Processed
            )
            if resp.value:
                token_account = resp.value[0].pubkey
                create_token_account_instruction = None
                print("Token account found.")
            else:
                token_account = get_associated_token_address(
                    RaydiumAmmV4.payer_keypair.pubkey(), mint
                )
                # Create associated token account for that mint
                create_token_account_instruction = create_associated_token_account(
                    RaydiumAmmV4.payer_keypair.pubkey(),
                    RaydiumAmmV4.payer_keypair.pubkey(),
                    mint
                )
                print("No existing token account found; creating associated token account.")

            # Create and initialize a WSOL account for the SOL we want to swap
            seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
            wsol_token_account = Pubkey.create_with_seed(
                RaydiumAmmV4.payer_keypair.pubkey(),
                seed,
                TOKEN_PROGRAM_ID
            )
            balance_needed = Token.get_min_balance_rent_for_exempt_for_account(RaydiumAmmV4.client)

            balance = RaydiumAmmV4.client.get_balance(RaydiumAmmV4.payer_keypair.pubkey()).value
            print(f"Wallet Balance: {balance} lamports")
            print(f"Rent-exempt min balance needed: {balance_needed} lamports")
            print("Total required (approx):", amount_in + balance_needed + MINIMUM_TRANSACTION_FEE)

            if balance < (amount_in + balance_needed + MINIMUM_TRANSACTION_FEE):
                print("Insufficient balance to complete the transaction.")
                return False

            create_wsol_account_instruction = create_account_with_seed(
                CreateAccountWithSeedParams(
                    from_pubkey=RaydiumAmmV4.payer_keypair.pubkey(),
                    to_pubkey=wsol_token_account,
                    base=RaydiumAmmV4.payer_keypair.pubkey(),
                    seed=seed,
                    lamports=int(balance_needed + amount_in),
                    space=ACCOUNT_LAYOUT_LEN,
                    owner=TOKEN_PROGRAM_ID,
                )
            )

            init_wsol_account_instruction = initialize_account(
                InitializeAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    mint=WSOL,
                    owner=RaydiumAmmV4.payer_keypair.pubkey(),
                )
            )

            swap_instruction = RaydiumAmmV4.make_amm_v4_swap_instruction(
                amount_in=amount_in,
                minimum_amount_out=minimum_amount_out,
                token_account_in=wsol_token_account,
                token_account_out=token_account,
                accounts=pool_keys,
                owner=RaydiumAmmV4.payer_keypair.pubkey(),
            )

            close_wsol_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    dest=RaydiumAmmV4.payer_keypair.pubkey(),
                    owner=RaydiumAmmV4.payer_keypair.pubkey(),
                )
            )

            instructions = [
                set_compute_unit_limit(RaydiumAmmV4.UNIT_BUDGET),
                set_compute_unit_price(RaydiumAmmV4.UNIT_PRICE),
                create_wsol_account_instruction,
                init_wsol_account_instruction,
            ]

            if create_token_account_instruction:
                instructions.append(create_token_account_instruction)

            instructions.extend([
                swap_instruction,
                close_wsol_account_instruction
            ])

            latest_blockhash = RaydiumAmmV4.client.get_latest_blockhash().value.blockhash
            compiled_message = compiled_message = MessageV0.try_compile(
                                RaydiumAmmV4.payer_keypair.pubkey(),  # Pubkey (payer)
                                instructions,                  # Список инструкций
                                [],                   # Список address lookup table аккаунтов (пока пуст)
                                latest_blockhash           # Текущий blockhash
                            )

            txn_sig = RaydiumAmmV4.client.send_transaction(
                txn=VersionedTransaction(compiled_message, [RaydiumAmmV4.payer_keypair]),
                opts=TxOpts(skip_preflight=True),
            ).value
            print("Transaction Signature:", txn_sig)

            confirmed = RaydiumAmmV4.confirm_txn(txn_sig)
            print("Transaction confirmed:", confirmed)
            return txn_sig

        except Exception as e:
            print("Error occurred during 'buy' transaction:", e)
            return False

    # -------------------------------------------------------------------------
    # Example "sell" function (swapping token -> SOL)
    # -------------------------------------------------------------------------
    @staticmethod
    def sell(pair_address: str, percentage: int = 100, slippage: int = 5) -> bool:
        """
        Sells the base token (if base != WSOL) or the quote token (if base == WSOL) for SOL.
        Wraps the SOL (WSOL) to do the actual swap, then closes the wrapped account.
        """
        try:
            print(f"Starting sell transaction for pair address: {pair_address}")

            if not RaydiumAmmV4.client or not RaydiumAmmV4.payer_keypair:
                raise ValueError("client or payer_keypair not set on RaydiumAmmV4.")

            if not (1 <= percentage <= 100):
                print("Percentage must be between 1 and 100.")
                return False

            print("Fetching pool keys...")
            pool_keys = RaydiumAmmV4.fetch_amm_v4_pool_keys(pair_address)
            if pool_keys is None:
                print("No pool keys found...")
                return False
            print("Pool keys fetched successfully.")

            mint = (
                pool_keys.base_mint
                if pool_keys.base_mint != WSOL
                else pool_keys.quote_mint
            )

            print("Retrieving token balance...")
            token_balance = RaydiumAmmV4.get_token_balance(str(mint))
            print("Token Balance:", token_balance)

            if not token_balance or token_balance <= 0:
                print("No token balance available to sell.")
                return False

            adjusted_balance = token_balance * (percentage / 100)
            print(f"Selling {percentage}% of the token balance = {adjusted_balance}")

            base_reserve, quote_reserve, token_decimal = RaydiumAmmV4.get_amm_v4_reserves(pool_keys)
            if base_reserve is None or quote_reserve is None:
                print("Error fetching pool reserves.")
                return False

            amount_out_estimate = RaydiumAmmV4.tokens_for_sol(
                adjusted_balance, base_reserve, quote_reserve
            )
            print(f"Estimated Amount Out (SOL): {amount_out_estimate}")

            slippage_adjustment = 1 - (slippage / 100)
            amount_out_with_slippage = amount_out_estimate * slippage_adjustment
            minimum_amount_out = int(amount_out_with_slippage * SOL_DECIMAL)
            amount_in = int(adjusted_balance * (10 ** token_decimal))
            print(f"Amount In (tokens): {amount_in} | Min SOL Out (lamports): {minimum_amount_out}")

            token_account = get_associated_token_address(
                RaydiumAmmV4.payer_keypair.pubkey(),
                mint
            )

            seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
            wsol_token_account = Pubkey.create_with_seed(
                RaydiumAmmV4.payer_keypair.pubkey(),
                seed,
                TOKEN_PROGRAM_ID
            )
            balance_needed = Token.get_min_balance_rent_for_exempt_for_account(RaydiumAmmV4.client)

            create_wsol_account_instruction = create_account_with_seed(
                CreateAccountWithSeedParams(
                    from_pubkey=RaydiumAmmV4.payer_keypair.pubkey(),
                    to_pubkey=wsol_token_account,
                    base=RaydiumAmmV4.payer_keypair.pubkey(),
                    seed=seed,
                    lamports=int(balance_needed),
                    space=ACCOUNT_LAYOUT_LEN,
                    owner=TOKEN_PROGRAM_ID,
                )
            )

            init_wsol_account_instruction = initialize_account(
                InitializeAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    mint=WSOL,
                    owner=RaydiumAmmV4.payer_keypair.pubkey(),
                )
            )

            swap_instruction = RaydiumAmmV4.make_amm_v4_swap_instruction(
                amount_in=amount_in,
                minimum_amount_out=minimum_amount_out,
                token_account_in=token_account,
                token_account_out=wsol_token_account,
                accounts=pool_keys,
                owner=RaydiumAmmV4.payer_keypair.pubkey(),
            )

            close_wsol_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    dest=RaydiumAmmV4.payer_keypair.pubkey(),
                    owner=RaydiumAmmV4.payer_keypair.pubkey(),
                )
            )

            instructions = [
                set_compute_unit_limit(RaydiumAmmV4.UNIT_BUDGET),
                set_compute_unit_price(RaydiumAmmV4.UNIT_PRICE),
                create_wsol_account_instruction,
                init_wsol_account_instruction,
                swap_instruction,
                close_wsol_account_instruction,
            ]

            # Optionally close the token account if selling 100%
            if percentage == 100:
                close_token_account_instruction = close_account(
                    CloseAccountParams(
                        program_id=TOKEN_PROGRAM_ID,
                        account=token_account,
                        dest=RaydiumAmmV4.payer_keypair.pubkey(),
                        owner=RaydiumAmmV4.payer_keypair.pubkey(),
                    )
                )
                instructions.append(close_token_account_instruction)

            latest_blockhash = RaydiumAmmV4.client.get_latest_blockhash().value.blockhash
            compiled_message = MessageV0.try_compile(
                RaydiumAmmV4.payer_keypair.pubkey(),
                instructions,
                [],
                latest_blockhash,
            )

            txn_sig = RaydiumAmmV4.client.send_transaction(
                txn=VersionedTransaction(compiled_message, [RaydiumAmmV4.payer_keypair]),
                opts=TxOpts(skip_preflight=True),
            ).value
            print("Transaction Signature:", txn_sig)

            confirmed = RaydiumAmmV4.confirm_txn(txn_sig)
            print("Transaction confirmed:", confirmed)
            return txn_sig

        except Exception as e:
            print("Error occurred during 'sell' transaction:", e)
            return False
        
    def buy_exec(self, mint: str, sol_in: float, slippage: int = 5) -> bool:
        try:
            pair_address = get_pool(mint)
            if not pair_address:
                print("No valid pool address returned.")
                return False

            # 2) Proceed with the usual Raydium buy flow
            print("Pool found!")
            pool_keys = RaydiumAmmV4.fetch_amm_v4_pool_keys(pair_address)
            if not pool_keys:
                print(f"Failed to fetch AMM v4 pool keys for {pair_address}.")
                return False
            print("Pool Keys fetched successfully!")
            print("AMM ID:", pool_keys.amm_id)
            print("Base Mint:", pool_keys.base_mint)
            print("Quote Mint:", pool_keys.quote_mint)

            res = RaydiumAmmV4.buy(pair_address=pair_address, sol_in=sol_in, slippage=slippage)
            if res:
                print("Транзакция на покупку прошла успешно")
                return res
            else:
                print("Транзакция на покупку не удалась")
                return False

        except Exception as e:
            print("Error occurred during 'buy' transaction:", e)
            return False

    def sell_exec(self, mint: str, percentage: int, slippage: int = 5) -> bool:
        try:
            pair_address = get_pool(mint)
            if not pair_address:
                print("No valid pool address returned.")
                return False

            # 2) Proceed with the usual Raydium sell flow
            print("Pool found!")
            pool_keys = RaydiumAmmV4.fetch_amm_v4_pool_keys(pair_address)
            if not pool_keys:
                print(f"Failed to fetch AMM v4 pool keys for {pair_address}.")
                return False
            print("Pool Keys fetched successfully!")
            print("AMM ID:", pool_keys.amm_id)
            print("Base Mint:", pool_keys.base_mint)
            print("Quote Mint:", pool_keys.quote_mint)

            res = RaydiumAmmV4.sell(pair_address=pair_address, percentage=percentage, slippage=slippage)
            if res:
                print("Транзакция на продажу прошла успешно")
                return res
            else:
                print("Транзакция на продажу не удалась")
                return False

        except Exception as e:
            print("Error occurred during 'sell' transaction:", e)
            return False


# -------------------------------------------------------------------------
# Example usage
# -------------------------------------------------------------------------
if __name__ == "__main__":
    mint = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
    percentage = 100  # 75% of the pool balance will be sold
    sol_in = 0.001
    slippage = 5  # 5% slippage allowed in the transaction
    success = RaydiumAmmV4().buy_exec(mint=mint, sol_in=sol_in, slippage=slippage)
    print(success)