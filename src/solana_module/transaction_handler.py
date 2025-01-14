import logging
from typing import Optional
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from .config import COMPUTE_UNIT_PRICE
from .solana_client import SolanaClient
from .utils import get_bonding_curve_address, find_associated_bonding_curve

logger = logging.getLogger(__name__)

class UserTransactionHandler:
    """Handles Solana transactions for specific user"""
    
    def __init__(self, private_key_str: str, compute_unit_price: int = COMPUTE_UNIT_PRICE):
        """
        Initialize handler with user's private key
        
        Args:
            private_key_str: String representation of private key array from database
            compute_unit_price: Price per compute unit in lamports
        """
        try:
            logger.info("[HANDLER] Initializing transaction handler")
            logger.debug(f"[HANDLER] Private key string starts with: {private_key_str[:20] if private_key_str else 'None'}...")
            
            # Initialize SolanaClient with user's keypair
            self.client = SolanaClient(compute_unit_price=compute_unit_price, private_key=private_key_str)
            
            # Try to load keypair immediately to verify it works
            try:
                payer = self.client.load_keypair()
                logger.info(f"[HANDLER] Successfully loaded keypair. Public key: {payer.pubkey()}")
            except Exception as e:
                logger.error(f"[HANDLER] Failed to load keypair: {str(e)}")
                raise
                
        except Exception as e:
            logger.error(f"[HANDLER] Error initializing transaction handler: {e}")
            raise ValueError("Invalid private key format")
    
    async def buy_token(
        self,
        token_address: str,
        amount_sol: float,
        slippage: float = 1.0,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Buy token for specified amount of SOL
        
        Args:
            token_address: Token mint address
            amount_sol: Amount of SOL to spend
            slippage: Slippage tolerance in percentage
            max_retries: Maximum number of retry attempts
            
        Returns:
            Transaction signature if successful, None otherwise
        """
        try:
            logger.info(f"Starting buy_token for address: {token_address}")
            
            # Convert token address to Pubkey
            mint = Pubkey.from_string(token_address)
            logger.info(f"Converted to Pubkey: {mint}")
            
            # Get bonding curve addresses
            bonding_curve_address, _ = get_bonding_curve_address(mint, self.client.PUMP_PROGRAM)
            associated_bonding_curve = find_associated_bonding_curve(mint, bonding_curve_address)
            logger.info(f"Got bonding curve: {bonding_curve_address}")
            logger.info(f"Got associated bonding curve: {associated_bonding_curve}")
            
            # Execute buy transaction using SolanaClient
            logger.info(f"Executing buy transaction for {amount_sol} SOL with {slippage}% slippage")
            tx_signature = await self.client.buy_token(
                mint=mint,
                bonding_curve=bonding_curve_address,
                associated_bonding_curve=associated_bonding_curve,
                amount=amount_sol,
                slippage=slippage/100  # Convert percentage to decimal
            )
            
            if tx_signature:
                logger.info(f"Buy transaction successful: {tx_signature}")
            else:
                logger.error("Buy transaction failed: no signature returned")
                
            return tx_signature
            
        except Exception as e:
            logger.error(f"Error buying token: {e}")
            return None
    
    async def sell_token(
        self,
        token_address: str,
        amount_tokens: float = None,
        sell_percentage: float = None,
        slippage: float = 1.0,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Sell specified amount of tokens
        
        Args:
            token_address: Token mint address
            amount_tokens: Exact amount of tokens to sell (optional)
            sell_percentage: Percentage of tokens to sell (optional)
            slippage: Slippage tolerance in percentage
            max_retries: Maximum number of retry attempts
            
        Returns:
            Transaction signature if successful, None otherwise
        """
        try:
            # Convert token address to Pubkey
            mint = Pubkey.from_string(token_address)
            
            # Get bonding curve addresses
            bonding_curve_address, _ = get_bonding_curve_address(mint, self.client.PUMP_PROGRAM)
            associated_bonding_curve = find_associated_bonding_curve(mint, bonding_curve_address)
            
            # Get token balance if selling percentage
            if sell_percentage is not None:
                # Get associated token account
                associated_token_account = await self.client.create_associated_token_account(mint)
                # Get token balance
                resp = await self.client.client.get_token_account_balance(associated_token_account)
                token_balance = int(resp.value.amount)
                # Calculate amount to sell
                amount_tokens = (token_balance * sell_percentage) / 100
            
            if amount_tokens is None:
                raise ValueError("Must specify either amount_tokens or sell_percentage")
            
            # Execute sell transaction using SolanaClient
            return await self.client.sell_token(
                mint=mint,
                bonding_curve=bonding_curve_address,
                associated_bonding_curve=associated_bonding_curve,
                token_amount=amount_tokens,
                min_amount=slippage/100  # Convert percentage to decimal
            )
            
        except Exception as e:
            logger.error(f"Error selling token: {e}")
            return None 