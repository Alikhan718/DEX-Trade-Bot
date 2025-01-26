import os
import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey as PublicKey
from solders.signature import Signature
from tenacity import retry, stop_after_attempt, wait_exponential
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY not found. Make sure it is set in the .env file.")

# Constants
BACKUP_RPC_URLS = [
    f"https://mainnet.helius-rpc.com/?api-key={API_KEY}",
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.projectserum.com",
    "https://api.metaplex.solana.com",
    "https://api.devnet.solana.com",
]
TARGET_MINT = "61V8vBaqAGMpgDQi4JcAwo1dmBGHsyhzodcPqnEVpump"
COINMARKETCAP_URL = "https://api.coinmarketcap.com/dexer/v3/dexer/search/main-site"

@dataclass
class TokenMetadata:
    name: str
    symbol: str
    address: str

@dataclass
class SmartTrader:
    wallet_address: str
    profit_usd: float
    roi_percentage: float
    first_trade_time: datetime
    token_trades_count: int

class SmartMoneyTracker:
    def __init__(self):
        """Initialize smart money tracker with multiple RPC clients and caching."""
        self.rpc_clients = [AsyncClient(url) for url in BACKUP_RPC_URLS]
        self.current_rpc_index = 0
        self.cache = {}
        self.cache_ttl = 300  # Cache Time-To-Live in seconds
        self.delay_between_requests = 1.0
        self.max_retries = 3  # Maximum retries for each RPC
        self.sol_price = self.get_sol_price()

    def get_sol_price(self) -> float:
        """Fetch the current price of SOL in USD."""
        # Implement a method to fetch the current SOL price.
        # For simplicity, we'll use a static value. You can integrate with an API like CoinGecko.
        return 20.0  # Example price

    async def _get_next_rpc_client(self) -> AsyncClient:
        """Get the next available RPC client using round-robin."""
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_clients)
        return self.rpc_clients[self.current_rpc_index]

    async def _make_rpc_request(self, method: str, *args, **kwargs) -> Optional[Dict]:
        """Make an RPC request with error handling and endpoint rotation."""
        for attempt in range(self.max_retries):
            rpc_client = await self._get_next_rpc_client()
            try:
                method_to_call = getattr(rpc_client, method)
                response = await method_to_call(*args, **kwargs)

                if response and hasattr(response, 'value'):
                    return response

            except Exception as e:
                logger.debug(f"RPC request failed for {rpc_client._provider.endpoint_uri}: {str(e)}")
                await asyncio.sleep(self.delay_between_requests)
                continue

            # Exponential backoff
            await asyncio.sleep(self.delay_between_requests)
            self.delay_between_requests *= 2

        logger.error(f"All RPC endpoints failed for method {method} with args {args}")
        return None

    async def _fetch_token_transactions(self, token_address: str) -> List[Dict]:
        """Fetch transactions related to a specific token."""
        transactions = []
        max_transactions = 100  # Increased transaction limit
        max_attempts = 3  # Maximum attempts
        attempt = 0

        try:
            while attempt < max_attempts:
                response = await self._make_rpc_request(
                    'get_signatures_for_address',
                    PublicKey.from_string(token_address),
                    limit=max_transactions
                )

                if not response or not response.value:
                    logger.warning(f"No signatures found for token {token_address} after attempt {attempt + 1}")
                    attempt += 1
                    continue

                signatures_found = False
                for sig_info in response.value[:max_transactions]:
                    try:
                        signature = str(sig_info.signature)
                        logger.debug(f"Processing transaction {signature}")

                        # Fetch the transaction with retry
                        tx_response = await self._make_rpc_request(
                            'get_transaction',
                            Signature.from_string(signature)
                        )

                        if tx_response and tx_response.value:
                            signatures_found = True
                            tx_data = self._extract_transaction_data(tx_response.value, signature)
                            if tx_data:
                                transactions.append(tx_data)
                                if len(transactions) >= 20:  # Stop after 20 successful transactions
                                    return transactions

                    except Exception as e:
                        logger.debug(f"Failed to process transaction {signature}: {str(e)}")
                        continue

                if signatures_found:
                    break

                attempt += 1

            logger.info(f"Finished fetching transactions. Found: {len(transactions)}")
            return transactions

        except Exception as e:
            logger.error(f"Failed to fetch transactions: {str(e)}")
            return transactions

    def _extract_transaction_data(self, tx_value: Dict, signature: str) -> Optional[Dict]:
        """Extract relevant data from a transaction."""
        try:
            tx_data = {
                'signature': signature,
                'block_time': getattr(tx_value, 'blockTime', None),
                'accounts': [],
                'pre_balances': [],
                'post_balances': []
            }

            if hasattr(tx_value, 'transaction'):
                tx = tx_value.transaction
                if hasattr(tx, 'message'):
                    message = tx.message
                    if hasattr(message, 'accountKeys'):
                        tx_data['accounts'] = [str(key) for key in message.accountKeys]

            if hasattr(tx_value, 'meta'):
                meta = tx_value.meta
                if hasattr(meta, 'preBalances'):
                    tx_data['pre_balances'] = list(meta.preBalances)
                if hasattr(meta, 'postBalances'):
                    tx_data['post_balances'] = list(meta.postBalances)

            return tx_data if tx_data['accounts'] and tx_data['pre_balances'] else None

        except Exception as e:
            logger.debug(f"Error extracting transaction data: {str(e)}")
            return None

    async def _analyze_trader_transactions(self, transactions: List[Dict]) -> List[SmartTrader]:
        """Analyze transactions to identify smart money traders."""
        traders = {}

        for tx in transactions:
            try:
                if not tx.get('accounts') or not tx.get('pre_balances') or not tx.get('post_balances'):
                    continue

                if len(tx['pre_balances']) != len(tx['post_balances']) or not tx['pre_balances']:
                    continue

                # Get trader address (first account in transaction)
                trader_address = tx['accounts'][0]
                timestamp = datetime.fromtimestamp(tx['block_time']) if tx.get('block_time') else datetime.now()

                # Calculate balance change
                pre_balance = float(tx['pre_balances'][0]) / 1e9  # Convert lamports to SOL
                post_balance = float(tx['post_balances'][0]) / 1e9
                balance_change = post_balance - pre_balance

                # Skip very small changes
                if abs(balance_change) < 0.001:
                    continue

                # Update or create trader stats
                if trader_address not in traders:
                    traders[trader_address] = SmartTrader(
                        wallet_address=trader_address,
                        profit_usd=0.0,
                        roi_percentage=0.0,
                        first_trade_time=timestamp,
                        token_trades_count=0
                    )

                trader = traders[trader_address]
                trader.token_trades_count += 1

                # Calculate USD value
                usd_change = balance_change * self.sol_price
                trader.profit_usd += usd_change

                # Calculate ROI
                if trader.token_trades_count > 1:
                    initial_value = abs(usd_change)
                    if initial_value > 0:
                        trader.roi_percentage = (trader.profit_usd / initial_value) * 100

            except Exception as e:
                logger.debug(f"Error analyzing transaction: {str(e)}")
                continue

        # Sort traders by absolute profit value
        sorted_traders = sorted(
            traders.values(),
            key=lambda x: abs(x.profit_usd),
            reverse=True
        )[:20]  # Top-20 traders

        return sorted_traders

    async def get_token_analysis(self, token_address: str) -> Tuple[TokenMetadata, List[SmartTrader]]:
        """Get token metadata and top traders, utilizing caching."""
        try:
            cache_key = f"analysis_{token_address}"
            current_time = datetime.now().timestamp()

            if cache_key in self.cache:
                cached_data, cache_time = self.cache[cache_key]
                if current_time - cache_time < self.cache_ttl:
                    logger.info("Returning cached data.")
                    return cached_data

            # Fetch token metadata
            metadata = await self._fetch_token_metadata(token_address)
            if not metadata:
                metadata = TokenMetadata(
                    name="Unknown Token",
                    symbol="???",
                    address=token_address
                )

            # Fetch and analyze transactions
            transactions = await self._fetch_token_transactions(token_address)
            if not transactions:
                logger.warning("No transactions found for analysis")
                return metadata, []

            top_traders = await self._analyze_trader_transactions(transactions)

            # Cache the results
            result = (metadata, top_traders)
            self.cache[cache_key] = (result, current_time)

            return result

        except Exception as e:
            logger.error(f"Error getting token analysis: {str(e)}")
            return TokenMetadata(
                name="Error",
                symbol="ERR",
                address=token_address
            ), []

    async def _fetch_token_metadata(self, mint: str) -> Optional[TokenMetadata]:
        """Fetch token metadata from CoinMarketCap or another API."""
        params = {
            "keyword": mint,
            "all": "false"
        }

        timestamp = int(time.time())
        user_agent = f"Custom/{timestamp}"

        headers = {
            "User-Agent": user_agent
        }

        try:
            response = requests.get(COINMARKETCAP_URL, params=params, headers=headers)

            if response.status_code == 200:
                data = response.json()
                pair = data['data']['pairs'][0]
                return TokenMetadata(
                    name=pair.get('name', 'Unknown Token'),
                    symbol=pair.get('symbol', '???'),
                    address=mint
                )
            else:
                logger.warning(f"Error fetching token metadata: {response.status_code}, {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request exception while fetching token metadata: {e}")
            return None

    def format_smart_money_message(self, metadata: TokenMetadata, traders: List[SmartTrader]) -> str:
        """Format the analysis results into a readable message."""
        lines = [
            f"🔍️ 📈 - ({metadata.name}) Smart Money Information",
            f"`{metadata.address}`\n",
            "*Top Addresses: Profit (ROI)*"
        ]

        if not traders:
            lines.append("_No trader data available_")
        else:
            for trader in traders:
                wallet = f"{trader.wallet_address[:4]}...{trader.wallet_address[-4:]}"
                profit = self._format_money(abs(trader.profit_usd))
                roi = f"{trader.roi_percentage:.2f}%"

                if trader.profit_usd >= 0:
                    lines.append(f"`{wallet}`  : ${profit} ({roi})")
                else:
                    lines.append(f"`{wallet}`  : -${profit} ({roi})")

        return "\n".join(lines)

    @staticmethod
    def _format_money(amount: float) -> str:
        """Format a monetary amount into a readable string."""
        if amount >= 1_000_000:
            return f"{amount / 1_000_000:.2f}M"
        elif amount >= 1_000:
            return f"{amount / 1_000:.1f}K"
        elif amount >= 1:
            return f"{amount:.2f}"
        return f"{amount:.2f}"

    async def close_clients(self):
        """Close all RPC clients."""
        for client in self.rpc_clients:
            await client.close()

# SolanaAnalyzer integrated functionality
class SolanaAnalyzer:
    def __init__(self, rpc_url: str):
        self.client = AsyncClient(rpc_url)

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_transaction_with_retry(self, signature: str):
        """Fetch a transaction with retries."""
        return await self.client.get_transaction(signature, max_supported_transaction_version=0)

    def is_mint_in_transaction(self, tx_data, target_mint):
        """Check if the target mint is in the transaction."""
        if not tx_data or not tx_data.value:
            return False

        meta = tx_data.value.transaction.meta
        message = tx_data.value.transaction.transaction.message

        target_mint_str = str(target_mint)
        token_balances = (meta.pre_token_balances or []) + (meta.post_token_balances or [])
        for balance in token_balances:
            if str(balance.mint) == target_mint_str:
                return True

        return target_mint_str in [str(key) for key in message.account_keys]

    def calculate_roi(self, tx_data, target_mint):
        """Calculate the ROI for a specific mint in the transaction."""
        if not tx_data or not tx_data.value:
            return None

        transaction_meta = tx_data.value.transaction.meta
        target_mint_str = str(target_mint)

        initial_amount, final_amount = None, None
        for balance in (transaction_meta.pre_token_balances or []):
            if str(balance.mint) == target_mint_str:
                initial_amount = float(balance.ui_token_amount.ui_amount or 0)

        for balance in (transaction_meta.post_token_balances or []):
            if str(balance.mint) == target_mint_str:
                final_amount = float(balance.ui_token_amount.ui_amount or 0)

        if initial_amount is None or final_amount is None:
            return None
        return 100 if initial_amount == 0 else ((final_amount - initial_amount) / initial_amount) * 100

    async def fetch_recent_transactions(self, account_address, target_mint, max_signatures=40, days_ago=7):
        """Fetch recent transactions containing the target mint and calculate overall ROI."""
        cutoff_time = datetime.now() - timedelta(days=days_ago)
        account_info = await self.client.get_signatures_for_address(account_address, commitment="finalized")
        if not account_info.value:
            return []

        filtered_signatures = [
            sig_info.signature
            for sig_info in account_info.value[:max_signatures]
            if sig_info.block_time and datetime.fromtimestamp(sig_info.block_time) >= cutoff_time
        ]

        recent_transactions = []
        tasks = [self.fetch_transaction_with_retry(sig) for sig in filtered_signatures]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for tx_data in results:
            if isinstance(tx_data, Exception):
                logger.error(f"Error processing transaction: {tx_data}")
                continue

            if tx_data and self.is_mint_in_transaction(tx_data, target_mint):
                roi = self.calculate_roi(tx_data, target_mint)
                if roi is not None:
                    recent_transactions.append((tx_data, roi))
        return recent_transactions

    def calculate_average_roi(self, transactions):
        """Calculate the average ROI across all processed transactions."""
        roi_values = [roi for _, roi in transactions if roi is not None]
        return sum(roi_values) / len(roi_values) if roi_values else 0

    async def account_info(self, account, target_mint, days_ago=7):
        """Fetch account info and print relevant information."""
        account_info = await self.client.get_account_info(account)
        if account_info and account_info.value.lamports:
            balance = account_info.value.lamports / 1e6
            transactions = await self.fetch_recent_transactions(account, target_mint, days_ago=days_ago)
            average_roi = self.calculate_average_roi(transactions)
            return balance, len(transactions), average_roi
        return None

    @retry
    async def analyze_accounts(self, target_mint, days_ago=7):
        """Analyze largest accounts for the target mint."""
        accounts = []
        try:
            largest_accounts = (await self.client.get_token_largest_accounts(target_mint)).value
            for account in largest_accounts:
                info = await self.account_info(account.address, target_mint, days_ago=days_ago)
                if info:
                    balance, tx_count, avg_roi = info
                    # Assuming token_info function is integrated into SmartMoneyTracker
                    # You may need to adjust this part based on your token metadata fetching
                    token_price = 20.0  # Replace with actual price fetching
                    total_value = float(account.amount.ui_amount) * token_price
                    logger.info(f"Account: {account.address}, Balance: ${total_value:.2f}, "
                                f"Transactions: {tx_count}, Avg ROI: {avg_roi:.2f}%")
                    accounts.append((account.address, total_value, tx_count, avg_roi))
        except Exception as e:
            logger.error(f"An error occurred: {e}")
        finally:
            await self.client.close()
        return sorted(accounts, key=lambda x: x[1], reverse=True)

# Main execution
async def main():
    tracker = SmartMoneyTracker()
    try:
        token_address = TARGET_MINT
        metadata, top_traders = await tracker.get_token_analysis(token_address)
        message = tracker.format_smart_money_message(metadata, top_traders)
        print(message)
    finally:
        await tracker.close_clients()

if __name__ == "__main__":
    asyncio.run(main())