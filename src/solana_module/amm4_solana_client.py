import os
import time
import base64
import struct
import asyncio

from typing import Optional
from dataclasses import dataclass

import httpx  # For async HTTP requests
from dotenv import load_dotenv

# Solder / Solana imports
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.message import MessageV0
from solders.system_program import (
    CreateAccountWithSeedParams,
    create_account_with_seed,
)
from solders.transaction import VersionedTransaction
from solders.transaction_status import TransactionConfirmationStatus

from solana.rpc.async_api import AsyncClient  # Asynchronous Solana RPC client
from solana.rpc.commitment import Processed
from solana.rpc.types import TokenAccountOpts, TxOpts
from solders.system_program import TransferParams, transfer
from spl.token.client import Token
from spl.token.instructions import (
    CloseAccountParams,
    InitializeAccountParams,
    create_associated_token_account,
    get_associated_token_address,
    initialize_account,
    close_account,
)

from src.solana_module.sdk.jito_jsonrpc_sdk import JitoJsonRpcSDK

# Local imports from your code (replace with your actual paths as needed)
# e.g. from src.solana_module.raydium.constants import ...
from src.solana_module.raydium.constants import (
    WSOL,
    TOKEN_PROGRAM_ID,
    RAYDIUM_AMM_V4,
    DEFAULT_QUOTE_MINT,
    SOL_DECIMAL,
    ACCOUNT_LAYOUT_LEN,
)

from src.solana_module.layouts.amm_v4 import (
    LIQUIDITY_STATE_LAYOUT_V4,
    MARKET_STATE_LAYOUT_V3,
)

load_dotenv()

MINIMUM_TRANSACTION_FEE = 5000  # Example constant transaction fee in lamports


def get_pool_info_by_id_sync(pool_id: str) -> dict:
    """
    A tiny helper that does synchronous GET (if you still need it outside the async class).
    You can remove or replace this. 
    """
    base_url = "https://api-v3.raydium.io/pools/info/ids"
    params = {"ids": pool_id}
    try:
        import requests
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": f"Failed to fetch pool info: {e}"}


def get_pool_info_by_mint_sync(mint: str, pool_type: str = "all", sort_field: str = "default",
                               sort_type: str = "desc", page_size: int = 100, page: int = 1) -> dict:
    """
    A tiny helper that does synchronous GET (if you still need it outside the async class).
    You can remove or replace this.
    """
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
        import requests
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": f"Failed to fetch pool info: {e}"}


async def get_pool_info_by_id(pool_id: str) -> dict:
    """
    Asynchronous version of fetching Raydium pool info by pool ID.
    """
    base_url = "https://api-v3.raydium.io/pools/info/ids"
    params = {"ids": pool_id}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(base_url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": f"Failed to fetch pool info: {e}"}


async def get_pool_info_by_mint(
    mint: str,
    pool_type: str = "all",
    sort_field: str = "default",
    sort_type: str = "desc",
    page_size: int = 100,
    page: int = 1
) -> dict:
    """
    Asynchronous version of fetching Raydium pool info by mint.
    """
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
        async with httpx.AsyncClient() as client:
            response = await client.get(base_url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": f"Failed to fetch pool info: {e}"}


async def get_pool(mint: str) -> Optional[str]:
    """
    Example helper function to demonstrate how you'd fetch a pool ID
    given a mint address.
    """
    # Just an example of a known pool ID:
    pool_id = "5phQt8oA1fwKDq1pLJ2E2swozfs7dgDH78iLuoUjAYhM"
    pool_info = await get_pool_info_by_id(pool_id)

    if 'data' in pool_info and pool_info['data']:
        pool = pool_info['data'][0]
        print(f"Pool Info for: {pool_id}")
        print(f" - Pool ID: {pool.get('id', 'N/A')}")
        print(f" - Mint A Address: {pool['mintA'].get('address', 'N/A')}")
        print(f" - Mint B Address: {pool['mintB'].get('address', 'N/A')}")
    else:
        print("No data found for the given pool ID.")

    print("------------------------------------")

    # Now try by mint
    pool_info = await get_pool_info_by_mint(mint)

    if 'data' in pool_info and 'data' in pool_info['data']:
        print(f"Pools for Mint: {mint}")
        for pool in pool_info['data']['data']:
            if pool.get('type', 'N/A') == 'Standard':
                print(pool.get('id', 'N/A'))
                return pool.get('id', 'N/A')
    else:
        print(f"No pools found for the mint address: {mint}")

    return None


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


class RaydiumAmmV4:
    """
    Example class for managing Raydium AMM v4 operations in async style.

    Adjust or replace the placeholders (self.client, self.payer_keypair, etc.)
    with your actual Solana client and keypair logic in production.
    """

    def __init__(self, payer_keypair):
        # Load environment (for the SECRET_KEY, etc.)

        # Create an async Solana RPC client
        self.client = AsyncClient(os.getenv('SOLANA_RPC_URL') + "/?api-key=" + os.getenv('API_KEY_2'))

        # Example: read secret key from .env as comma-separated integers
        self.payer_keypair = payer_keypair

        # Example compute budget
        self.UNIT_BUDGET = 1_400_000
        self.UNIT_PRICE = 200_000
        self.sdk = JitoJsonRpcSDK(url="https://mainnet.block-engine.jito.wtf/api/v1")

        # Hard-coded addresses for Raydium
        self.ray_authority_v4 = Pubkey.from_string("5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1")
        self.open_book_program = Pubkey.from_string("srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX")
        self.token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

    async def fetch_amm_v4_pool_keys(self, pair_address: str) -> Optional[AmmV4PoolKeys]:
        """
        Fetch on-chain data for an AMM V4 pool and decode the layouts.
        """
        def u64_bytes(value: int) -> bytes:
            if not (0 <= value < 2**64):
                raise ValueError("Value must be in the range of a u64 (0 to 2^64 - 1).")
            return struct.pack('<Q', value)

        try:
            amm_id = Pubkey.from_string(pair_address)

            # Get AMM account info
            amm_info_resp = await self.client.get_account_info(amm_id, commitment=Processed)
            amm_info = amm_info_resp.value
            if not amm_info or not amm_info.data:
                raise ValueError("AMM account data is missing or invalid.")

            # Data is base64-encoded
            amm_data = amm_info.data
            amm_data_decoded = LIQUIDITY_STATE_LAYOUT_V4.parse(amm_data)

            # Serum market address
            market_id = Pubkey.from_bytes(amm_data_decoded.serumMarket)

            # Now fetch market account info
            market_info_resp = await self.client.get_account_info(market_id, commitment=Processed)
            market_info = market_info_resp.value
            if not market_info or not market_info.data:
                raise ValueError("Market account data is missing or invalid.")

            market_data = market_info.data
            market_decoded = MARKET_STATE_LAYOUT_V3.parse(market_data)
            vault_signer_nonce = market_decoded.vault_signer_nonce

            pool_keys = AmmV4PoolKeys(
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
                    seeds=[bytes(market_id), u64_bytes(vault_signer_nonce)],
                    program_id=self.open_book_program
                ),
                market_base_vault=Pubkey.from_bytes(market_decoded.base_vault),
                market_quote_vault=Pubkey.from_bytes(market_decoded.quote_vault),
                bids=Pubkey.from_bytes(market_decoded.bids),
                asks=Pubkey.from_bytes(market_decoded.asks),
                event_queue=Pubkey.from_bytes(market_decoded.event_queue),
                ray_authority_v4=self.ray_authority_v4,
                open_book_program=self.open_book_program,
                token_program_id=self.token_program_id
            )
            return pool_keys

        except Exception as e:
            print(f"Error fetching pool keys: {e}")
            return None

    def make_amm_v4_swap_instruction(
        self,
        amount_in: int,
        minimum_amount_out: int,
        token_account_in: Pubkey,
        token_account_out: Pubkey,
        accounts: AmmV4PoolKeys,
        owner: Pubkey
    ):
        """
        Creates the Instruction object for swapping on Raydium AMM V4.
        Note: no async needed here because it's a local, in-memory function.
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
            print(f"Error occurred while creating swap instruction: {e}")
            return None

    async def get_amm_v4_reserves(self, pool_keys: AmmV4PoolKeys) -> tuple:
        """
        Fetch vault balances from the pool vault accounts.
        Returns (base_reserve, quote_reserve, token_decimal).
        """
        try:
            # We need multiple account info calls
            accounts_resp = await self.client.get_multiple_accounts(
                [pool_keys.quote_vault, pool_keys.base_vault],
                commitment=Processed
            )
            quote_vault = pool_keys.quote_vault
            quote_decimal = pool_keys.quote_decimals
            quote_mint = pool_keys.quote_mint

            base_vault = pool_keys.base_vault
            base_decimal = pool_keys.base_decimals
            base_mint = pool_keys.base_mint
            
            balances_response = await self.client.get_multiple_accounts_json_parsed(
                [quote_vault, base_vault],
                Processed
            )
            if not balances_response or not balances_response.value:
                raise ValueError("Failed to fetch vault account balances.")

            # The JSON parsed approach from the standard `getProgramAccounts` or `getTokenAccountBalance`
            # is not used here. Instead, you can parse the 165-byte token account data layout yourself
            # or you can do a separate approach. 
            #
            # For brevity, let's do a simpler approach:
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
            print(f"Error occurred in get_amm_v4_reserves: {e}")
            return None, None, None

    # -------------------------------------------------------------------------
    # Simplistic constant-product math helpers for local estimates
    # (remain synchronous, no I/O here)
    # -------------------------------------------------------------------------
    def sol_for_tokens(self, sol_amount: float, base_vault_balance: float, quote_vault_balance: float, swap_fee: float = 0.25) -> float:
        """
        Approx how many base tokens we'd get for a given sol_amount,
        using a constant-product model with a swap_fee %.
        """
        effective_sol_used = sol_amount - (sol_amount * (swap_fee / 100))
        constant_product = base_vault_balance * quote_vault_balance
        updated_base_vault_balance = constant_product / (quote_vault_balance + effective_sol_used)
        tokens_received = base_vault_balance - updated_base_vault_balance
        return round(tokens_received, 9)

    def tokens_for_sol(self, token_amount: float, base_vault_balance: float, quote_vault_balance: float, swap_fee: float = 0.25) -> float:
        """
        Approx how many SOL we'd get for a given token_amount,
        using a constant-product model with a swap_fee %.
        """
        effective_tokens_sold = token_amount * (1 - (swap_fee / 100))
        constant_product = base_vault_balance * quote_vault_balance
        updated_quote_vault_balance = constant_product / (base_vault_balance + effective_tokens_sold)
        sol_received = quote_vault_balance - updated_quote_vault_balance
        return round(sol_received, 9)

    async def get_token_balance(self, mint_str: str) -> Optional[float]:
        """
        Return the first account balance for the given mint, if it exists.
        """
        try:
            resp = await self.client.get_token_accounts_by_owner_json_parsed(
                self.payer_keypair.pubkey(),
                TokenAccountOpts(mint=Pubkey.from_string(mint_str)),
                commitment=Processed
            )
            if resp.value:
                accounts = resp.value
                if accounts:
                    token_amount = accounts[0].account.data.parsed['info']['tokenAmount']['uiAmount']
                    if token_amount:
                        return float(token_amount)
                return None
        except Exception as e:
            print(f"Error in get_token_balance: {e}")
            return None

    async def confirm_txn(self, txn_sig: str, max_retries: int = 40, retry_interval: int = 3) -> bool:
        """
        Poll for transaction confirmation status until finalized or until max_retries is reached.
        """
        retries = 0
        while retries < max_retries:
            try:
                status_res = await self.client.get_signature_statuses([txn_sig])
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
            await asyncio.sleep(retry_interval)

        print("Transaction not confirmed within the retry limit.")
        return False

    async def buy(self, pair_address: str, sol_in: float = 0.01, slippage: int = 5, antimev=False) -> bool:
        """
        Buys the 'other' token side from the pool using SOL as input (wrapped as WSOL).
        If base_mint == WSOL, we interpret that we are actually buying the quote_mint, otherwise base_mint.
        """
        try:
            print(f"Starting buy transaction for pair address: {pair_address}")

            print("Fetching pool keys...")
            pool_keys = await self.fetch_amm_v4_pool_keys(pair_address)
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

            base_reserve, quote_reserve, token_decimal = await self.get_amm_v4_reserves(pool_keys)
            if base_reserve is None or quote_reserve is None:
                print("Error fetching pool reserves.")
                return False

            amount_out_estimate = self.sol_for_tokens(sol_in, base_reserve, quote_reserve)
            print(f"Estimated Amount Out: {amount_out_estimate}")

            slippage_adjustment = 1 - (slippage / 100)
            amount_out_with_slippage = amount_out_estimate * slippage_adjustment
            minimum_amount_out = int(amount_out_with_slippage * (10 ** token_decimal))
            print(f"Amount In (lamports): {amount_in} | Minimum Amount Out: {minimum_amount_out}")

            # Check if we already have an associated token account
            resp = await self.client.get_token_accounts_by_owner(
                self.payer_keypair.pubkey(),
                TokenAccountOpts(mint=mint),
                commitment=Processed
            )
            if resp.value:
                token_account = resp.value[0].pubkey
                create_token_account_instruction = None
                print("Token account found.")
            else:
                token_account = get_associated_token_address(
                    self.payer_keypair.pubkey(), mint
                )
                # Create associated token account for that mint
                create_token_account_instruction = create_associated_token_account(
                    self.payer_keypair.pubkey(),
                    self.payer_keypair.pubkey(),
                    mint
                )
                print("No existing token account found; creating associated token account.")

            # Create and initialize a WSOL account for the SOL we want to swap
            seed_bytes = os.urandom(24)
            seed_b64 = base64.urlsafe_b64encode(seed_bytes).decode("utf-8")
            # For Pubkey.create_with_seed in `solders`, we must replicate how seeds are used:
            wsol_token_account = Pubkey.create_with_seed(
                self.payer_keypair.pubkey(),
                seed_b64,
                TOKEN_PROGRAM_ID
            )

              # Sync method in SPL, might need adaptation
            # Alternatively, you can compute the rent-exempt min via:
            balance_needed = await self.client.get_minimum_balance_for_rent_exemption(ACCOUNT_LAYOUT_LEN)
            balance_needed = balance_needed.value
            print(f"Rent-exempt min balance needed: {balance_needed} lamports")
            #  But for brevity, we'll keep the example as is.

            balance_resp = await self.client.get_balance(self.payer_keypair.pubkey())
            balance = balance_resp.value

            print(f"Wallet Balance: {balance} lamports")
            print(f"Rent-exempt min balance needed: {balance_needed} lamports")
            print("Total required (approx):", amount_in + balance_needed + MINIMUM_TRANSACTION_FEE)

            if balance < (amount_in + balance_needed + MINIMUM_TRANSACTION_FEE):
                print("Insufficient balance to complete the transaction.")
                return False

            create_wsol_account_instruction = create_account_with_seed(
                CreateAccountWithSeedParams(
                    from_pubkey=self.payer_keypair.pubkey(),
                    to_pubkey=wsol_token_account,
                    base=self.payer_keypair.pubkey(),
                    seed=seed_b64,
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
                    owner=self.payer_keypair.pubkey(),
                )
            )

            swap_instruction = self.make_amm_v4_swap_instruction(
                amount_in=amount_in,
                minimum_amount_out=minimum_amount_out,
                token_account_in=wsol_token_account,
                token_account_out=token_account,
                accounts=pool_keys,
                owner=self.payer_keypair.pubkey(),
            )

            close_wsol_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    dest=self.payer_keypair.pubkey(),
                    owner=self.payer_keypair.pubkey(),
                )
            )
            
            recipient = Pubkey.from_string(os.getenv('FEE_MAIN_WALLET'))
            lamports = amount_in // 100
            transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=self.payer_keypair.pubkey(),
                        to_pubkey=recipient,
                        lamports=lamports
                    )
                )

            instructions = [
                set_compute_unit_limit(self.UNIT_BUDGET),
                set_compute_unit_price(self.UNIT_PRICE),
                create_wsol_account_instruction,
                init_wsol_account_instruction,
            ]
            
            if antimev:
                jito_tip_account = Pubkey.from_string(self.sdk.get_random_tip_account())
                print(f"Using antimev tip account: {jito_tip_account}")
                jito_tip_ix = transfer(TransferParams(
                        from_pubkey=self.payer.pubkey(),
                        to_pubkey=jito_tip_account,
                        lamports=100000
                    ))
                instructions.append(jito_tip_ix)

            if create_token_account_instruction:
                instructions.append(create_token_account_instruction)

            instructions.extend([
                swap_instruction,
                close_wsol_account_instruction,
                transfer_ix,  # Transfer SOL to recipient
            ])

            # Fetch latest blockhash
            latest_blockhash_resp = await self.client.get_latest_blockhash()
            latest_blockhash = latest_blockhash_resp.value.blockhash

            compiled_message = MessageV0.try_compile(
                payer=self.payer_keypair.pubkey(),
                instructions=instructions,
                address_lookup_table_accounts=[],
                recent_blockhash=latest_blockhash
            )

            txn = VersionedTransaction(compiled_message, [self.payer_keypair])
            send_resp = await self.client.send_transaction(
                txn=txn,
                opts=TxOpts(skip_preflight=True),
            )
            txn_sig = send_resp.value
            print("Transaction Signature:", txn_sig)

            confirmed = await self.confirm_txn(txn_sig)
            print("Transaction confirmed:", confirmed)
            return txn_sig if confirmed else False

        except Exception as e:
            print("Error occurred during 'buy' transaction:", e)
            return False

    async def sell(self, pair_address: str, percentage: int = 100, slippage: int = 5, antimev=False) -> bool:
        """
        Sells the base token (if base != WSOL) or the quote token (if base == WSOL) for SOL.
        Wraps the SOL (WSOL) to do the actual swap, then closes the wrapped account.
        """
        try:
            print(f"Starting sell transaction for pair address: {pair_address}")

            if not (1 <= percentage <= 100):
                print("Percentage must be between 1 and 100.")
                return False

            print("Fetching pool keys...")
            pool_keys = await self.fetch_amm_v4_pool_keys(pair_address)
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
            token_balance = await self.get_token_balance(str(mint))
            print("Token Balance:", token_balance)

            if not token_balance or token_balance <= 0:
                print("No token balance available to sell.")
                return False

            adjusted_balance = token_balance * (percentage / 100)
            print(f"Selling {percentage}% of the token balance = {adjusted_balance}")

            base_reserve, quote_reserve, token_decimal = await self.get_amm_v4_reserves(pool_keys)
            if base_reserve is None or quote_reserve is None:
                print("Error fetching pool reserves.")
                return False

            amount_out_estimate = self.tokens_for_sol(adjusted_balance, base_reserve, quote_reserve)
            print(f"Estimated Amount Out (SOL): {amount_out_estimate}")

            slippage_adjustment = 1 - (slippage / 100)
            amount_out_with_slippage = amount_out_estimate * slippage_adjustment
            minimum_amount_out = int(amount_out_with_slippage * SOL_DECIMAL)
            amount_in = int(adjusted_balance * (10 ** token_decimal))
            print(f"Amount In (tokens): {amount_in} | Min SOL Out (lamports): {minimum_amount_out}")

            token_account = get_associated_token_address(
                self.payer_keypair.pubkey(),
                mint
            )

            seed_bytes = os.urandom(24)
            seed_b64 = base64.urlsafe_b64encode(seed_bytes).decode("utf-8")
            wsol_token_account = Pubkey.create_with_seed(
                self.payer_keypair.pubkey(),
                seed_b64,
                TOKEN_PROGRAM_ID
            )

            balance_needed = await self.client.get_minimum_balance_for_rent_exemption(ACCOUNT_LAYOUT_LEN)
            balance_needed = balance_needed.value
            print(f"Rent-exempt min balance needed: {balance_needed} lamports")

            create_wsol_account_instruction = create_account_with_seed(
                CreateAccountWithSeedParams(
                    from_pubkey=self.payer_keypair.pubkey(),
                    to_pubkey=wsol_token_account,
                    base=self.payer_keypair.pubkey(),
                    seed=seed_b64,
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
                    owner=self.payer_keypair.pubkey(),
                )
            )

            swap_instruction = self.make_amm_v4_swap_instruction(
                amount_in=amount_in,
                minimum_amount_out=minimum_amount_out,
                token_account_in=token_account,
                token_account_out=wsol_token_account,
                accounts=pool_keys,
                owner=self.payer_keypair.pubkey(),
            )
            

            close_wsol_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    dest=self.payer_keypair.pubkey(),
                    owner=self.payer_keypair.pubkey(),
                )
            )
            
            recipient = Pubkey.from_string(os.getenv('FEE_MAIN_WALLET'))
            lamports = minimum_amount_out // 100
            transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=self.payer_keypair.pubkey(),
                        to_pubkey=recipient,
                        lamports=lamports
                    )
                )

            instructions = [
                set_compute_unit_limit(self.UNIT_BUDGET),
                set_compute_unit_price(self.UNIT_PRICE),
                create_wsol_account_instruction,
                init_wsol_account_instruction,
                swap_instruction,
                transfer_ix,
                close_wsol_account_instruction,
            ]
            
            if antimev:
                jito_tip_account = Pubkey.from_string(self.sdk.get_random_tip_account())
                print(f"Using antimev tip account: {jito_tip_account}")
                jito_tip_ix = transfer(TransferParams(
                        from_pubkey=self.payer.pubkey(),
                        to_pubkey=jito_tip_account,
                        lamports=100000
                    ))
                instructions.append(jito_tip_ix)

            # Optionally close the token account if selling 100%
            if percentage == 100:
                close_token_account_instruction = close_account(
                    CloseAccountParams(
                        program_id=TOKEN_PROGRAM_ID,
                        account=token_account,
                        dest=self.payer_keypair.pubkey(),
                        owner=self.payer_keypair.pubkey(),
                    )
                )
                instructions.append(close_token_account_instruction)

            latest_blockhash_resp = await self.client.get_latest_blockhash()
            latest_blockhash = latest_blockhash_resp.value.blockhash

            compiled_message = MessageV0.try_compile(
                payer=self.payer_keypair.pubkey(),
                instructions=instructions,
                address_lookup_table_accounts=[],
                recent_blockhash=latest_blockhash
            )

            txn = VersionedTransaction(compiled_message, [self.payer_keypair])
            send_resp = await self.client.send_transaction(
                txn=txn,
                opts=TxOpts(skip_preflight=True),
            )
            txn_sig = send_resp.value
            print("Transaction Signature:", txn_sig)

            confirmed = await self.confirm_txn(txn_sig)
            print("Transaction confirmed:", confirmed)
            return txn_sig if confirmed else False

        except Exception as e:
            print("Error occurred during 'sell' transaction:", e)
            return False

    async def buy_exec(self, mint: str, sol_in: float, slippage: int = 5, antimev=False) -> bool:
        """
        Wrapper that:
         1) Gets a valid AMM pool address by the given mint
         2) Calls self.buy() using that pool
        """
        try:
            pair_address = await get_pool(mint)
            if not pair_address:
                print("No valid pool address returned.")
                return False

            print("Pool found!")
            pool_keys = await self.fetch_amm_v4_pool_keys(pair_address)
            if not pool_keys:
                print(f"Failed to fetch AMM v4 pool keys for {pair_address}.")
                return False
            print("Pool Keys fetched successfully!")
            print("AMM ID:", pool_keys.amm_id)
            print("Base Mint:", pool_keys.base_mint)
            print("Quote Mint:", pool_keys.quote_mint)

            res = await self.buy(pair_address=pair_address, sol_in=sol_in, slippage=slippage, antimev=antimev)
            if res:
                print("Транзакция на покупку прошла успешно!")
                return res
            else:
                print("Транзакция на покупку не удалась.")
                return False

        except Exception as e:
            print("Error occurred during 'buy_exec' transaction:", e)
            return False

    async def sell_exec(self, mint: str, percentage: int, slippage: int = 5, antimev=False) -> bool:
        """
        Wrapper that:
         1) Gets a valid AMM pool address by the given mint
         2) Calls self.sell() using that pool
        """
        try:
            pair_address = await get_pool(mint)
            if not pair_address:
                print("No valid pool address returned.")
                return False

            print("Pool found!")
            pool_keys = await self.fetch_amm_v4_pool_keys(pair_address)
            if not pool_keys:
                print(f"Failed to fetch AMM v4 pool keys for {pair_address}.")
                return False
            print("Pool Keys fetched successfully!")
            print("AMM ID:", pool_keys.amm_id)
            print("Base Mint:", pool_keys.base_mint)
            print("Quote Mint:", pool_keys.quote_mint)

            res = await self.sell(pair_address=pair_address, percentage=percentage, slippage=slippage, antimev=antimev)
            if res:
                print("Транзакция на продажу прошла успешно!")
                return res
            else:
                print("Транзакция на продажу не удалась.")
                return False

        except Exception as e:
            print("Error occurred during 'sell_exec' transaction:", e)
            return False

    async def close(self):
        """ Call this to close the AsyncClient session cleanly if desired. """
        await self.client.close()


# -------------------------------------------------------------------------
# Example usage with an async entrypoint
# -------------------------------------------------------------------------
async def main():
    amm = RaydiumAmmV4()

    mint = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"  # Example
    percentage = 100  # 100% of token balance to sell
    sol_in = 0.001    # 0.001 SOL to buy with
    slippage = 5      # 5% slippage

    # Example buy
    buy_success = await amm.buy_exec(mint=mint, sol_in=sol_in, slippage=slippage)
    print("Buy success:", buy_success)

    # Example sell
    sell_success = await amm.sell_exec(mint=mint, percentage=percentage, slippage=slippage)
    print("Sell success:", sell_success)

    # Cleanly close the client session
    await amm.close()


if __name__ == "__main__":
    asyncio.run(main())