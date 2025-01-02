import logging
from datetime import datetime
from typing import Optional

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey as PublicKey
import aiohttp

from ..utils.config import Config

logger = logging.getLogger(__name__)

class SolanaService:
    def __init__(self):
        """Initialize Solana service"""
        self.client = AsyncClient(Config.SOLANA_RPC_URL)
        self.sol_price = 0
        self.last_price_update = None
        self.price_update_interval = 300  # 5 minutes in seconds
        
    async def get_sol_price(self) -> float:
        """Get current SOL price with caching"""
        current_time = datetime.now()
        
        # Check if we need to update the price
        if (self.last_price_update is None or 
            (current_time - self.last_price_update).total_seconds() > self.price_update_interval):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd') as response:
                        if response.status == 200:
                            data = await response.json()
                            self.sol_price = data['solana']['usd']
                            self.last_price_update = current_time
                        else:
                            logger.error(f"Failed to fetch SOL price: {response.status}")
            except Exception as e:
                logger.error(f"Error fetching SOL price: {e}")
                if self.sol_price == 0:  # If we don't have any cached price
                    self.sol_price = 100  # Use a default value
        
        return self.sol_price

    async def get_wallet_balance(self, wallet_address: str) -> float:
        """Get wallet SOL balance"""
        try:
            pubkey = PublicKey.from_string(wallet_address)
            response = await self.client.get_balance(pubkey)
            if response.value is not None:
                return response.value / 1e9  # Convert lamports to SOL
            return 0
        except Exception as e:
            logger.error(f"Error getting wallet balance: {e}")
            return 0

    def validate_wallet_address(self, address: str) -> bool:
        """Validate Solana wallet address"""
        try:
            PublicKey.from_string(address)
            return True
        except Exception:
            return False 