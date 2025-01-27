# /path/to/smart_money_tracker.py

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from dataclasses import dataclass

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey as PublicKey
from solders.signature import Signature
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY not found. Ensure it is set in the .env file.")

# Constants
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
COINMARKETCAP_URL = "https://api.coinmarketcap.com/dexer/v3/dexer/search/main-site"
TARGET_MINT = "61V8vBaqAGMpgDQi4JcAwo1dmBGHsyhzodcPqnEVpump"

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
    def __init__(self, rpc_url: str, target_mint: str):
        self.client = AsyncClient(rpc_url)
        self.target_mint = target_mint
        self.sol_price = self._fetch_sol_price()

    def _fetch_sol_price(self) -> float:
        """Fetch the current price of SOL in USD."""
        # Replace this stub with an actual API call or return a static value for testing.
        return 20.0  # Example price

    async def fetch_token_metadata(self) -> Optional[TokenMetadata]:
        """Fetch token metadata from CoinMarketCap or another API."""
        params = {"keyword": self.target_mint, "all": "false"}
        headers = {"User-Agent": f"Custom/{int(datetime.now().timestamp())}"}
        try:
            response = requests.get(COINMARKETCAP_URL, params=params, headers=headers)
            if response.status_code == 200:
                data = response.json()
                pair = data['data']['pairs'][0]
                return TokenMetadata(
                    name=pair.get('name', 'Unknown Token'),
                    symbol=pair.get('symbol', '???'),
                    address=self.target_mint
                )
            else:
                logger.warning(f"Error fetching token metadata: {response.status_code}, {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request exception while fetching token metadata: {e}")
            return None

    async def fetch_recent_transactions(self, days_ago: int = 7) -> List[dict]:
        """Fetch recent transactions involving the target mint."""
        cutoff_time = datetime.now() - timedelta(days=days_ago)
        try:
            account_info = await self.client.get_signatures_for_address(
                PublicKey.from_string(self.target_mint),
                commitment="finalized"
            )
            if not account_info.value:
                return []

            filtered_signatures = [
                sig_info.signature
                for sig_info in account_info.value
                if sig_info.block_time and datetime.fromtimestamp(sig_info.block_time) >= cutoff_time
            ]
            tasks = [self._fetch_transaction_with_retry(sig) for sig in filtered_signatures]
            transactions = await asyncio.gather(*tasks, return_exceptions=True)
            return [tx for tx in transactions if not isinstance(tx, Exception)]
        except Exception as e:
            logger.error(f"Error fetching recent transactions: {e}")
            return []

    async def _fetch_transaction_with_retry(self, signature: str) -> Optional[dict]:
        """Fetch a transaction with retries."""
        try:
            tx = await self.client.get_transaction(
                Signature.from_string(signature),
                max_supported_transaction_version=0
            )
            return tx.value if tx and tx.value else None
        except Exception as e:
            logger.error(f"Error fetching transaction {signature}: {e}")
            return None

async def analyze_traders(self, transactions: List[dict]) -> List[SmartTrader]:
    """Analyze transactions to identify smart traders."""
    traders = {}
    for tx in transactions:
        try:
            if tx is None:
                logger.warning("Skipping a None transaction")
                continue

            # Validate the expected structure of the transaction
            pre_balances = tx.get("preTokenBalances", [])
            post_balances = tx.get("postTokenBalances", [])
            if not pre_balances or not post_balances:
                logger.warning(f"Skipping transaction with missing balances: {tx}")
                continue

            for balance in pre_balances:
                wallet_address = balance.get("owner")
                if wallet_address:
                    traders[wallet_address] = traders.get(wallet_address, 0) + 1
        except Exception as e:
            logger.error(f"Error analyzing transaction: {e}")
    return sorted(
        [SmartTrader(wallet_address=k, profit_usd=v, roi_percentage=0.0, first_trade_time=datetime.now(), token_trades_count=v)
         for k, v in traders.items()],
        key=lambda x: x.profit_usd,
        reverse=True
    )

    async def close(self):
        """Close the Solana client connection."""
        await self.client.close()

# Main execution
async def main():
    tracker = SmartMoneyTracker(RPC_URL, TARGET_MINT)
    try:
        metadata = await tracker.fetch_token_metadata()
        transactions = await tracker.fetch_recent_transactions(days_ago=30)
        traders = await tracker.analyze_traders(transactions)

        if metadata:
            print(f"Token: {metadata.name} ({metadata.symbol})")
        print(f"Top Traders: {len(traders)} found")
        for trader in traders[:10]:  # Top 10 traders
            print(f"Wallet: {trader.wallet_address}, Trades: {trader.token_trades_count}, Profit: ${trader.profit_usd:.2f}")
    finally:
        await tracker.close()

if __name__ == "__main__":
    asyncio.run(main())