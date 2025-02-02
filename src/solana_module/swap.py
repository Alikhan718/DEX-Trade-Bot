from decimal import Decimal
from typing import Dict, Tuple, NamedTuple
import json
from solders.signature import Signature
from solana.rpc.api import Client
from solders.rpc.responses import GetTransactionResp
from solders.transaction_status import UiTransactionTokenBalance
from solders.pubkey import Pubkey
from src.solana_module.amm4_solana_client import RaydiumAmmV4

class TokenBalance(NamedTuple):
    amount: Decimal
    decimals: int
    mint: Pubkey
    owner: Pubkey

def parse_token_balance(balance_data: UiTransactionTokenBalance) -> TokenBalance:
    """Parse token balance data from transaction."""
    return TokenBalance(
        amount=Decimal(balance_data.ui_token_amount.amount),
        decimals=balance_data.ui_token_amount.decimals,
        mint=balance_data.mint,
        owner=balance_data.owner
    )

def format_amount(amount: Decimal, decimals: int) -> str:
    """Format token amount with proper decimal places."""
    return f"{amount / Decimal(10**decimals):.{decimals}f}"

def analyze_token_changes(pre_balances: list, post_balances: list) -> Dict:
    """Analyze changes in token balances before and after transaction."""
    changes = {}
    
    # Create lookup tables for pre and post balances
    pre_by_mint = {b.mint: b for b in map(parse_token_balance, pre_balances)}
    post_by_mint = {b.mint: b for b in map(parse_token_balance, post_balances)}
    
    # Calculate changes for each token
    for mint in set(pre_by_mint.keys()) | set(post_by_mint.keys()):
        pre = pre_by_mint.get(mint)
        post = post_by_mint.get(mint)
        
        if pre and post:
            change = post.amount - pre.amount
            if change != 0:
                changes[mint] = {
                    'pre_balance': format_amount(pre.amount, pre.decimals),
                    'post_balance': format_amount(post.amount, post.decimals),
                    'change': format_amount(change, pre.decimals),
                    'decimals': pre.decimals,
                    'owner': pre.owner
                }
    
    return changes

def analyze_transaction(tx_data: GetTransactionResp) -> None:
    """Analyze a Solana transaction and print token exchanges."""
    # Parse transaction data
    
    # Extract token balances
    meta = tx_data.value.transaction.meta
    pre_token_balances = meta.pre_token_balances
    post_token_balances = meta.post_token_balances
    
    # Analyze changes
    changes = analyze_token_changes(pre_token_balances, post_token_balances)
    
    # Print results
    print("Token Exchange Analysis:")
    print("-" * 50)
    
    for mint, change_data in changes.items():
        print(f"\nToken: {mint}")
        print(f"Owner: {change_data['owner']}")
        print(f"Pre-balance:  {change_data['pre_balance']}")
        print(f"Post-balance: {change_data['post_balance']}")
        print(f"Change:       {change_data['change']}")
        change_data['change'] = float(change_data['change'])
        if 'So11111111111111111111111111111111111111112' in mint:
            if change_data['change'] > 0:
                return 'BUY'
            else:
                return 'SELL'
        else:
            if change_data['change'] < 0:
                return 'BUY'
            else:
                return 'SELL'

# Example usage
def swap_type(signature: str) -> str:
    sig = Signature.from_string(signature)
    # Sample transaction data would go here
    client = Client('https://api.mainnet-beta.solana.com')
    tx_data = client.get_transaction(sig, max_supported_transaction_version=0)
    try:
        ans = analyze_transaction(tx_data)
        return ans
    except Exception as e:
        print(f"Error analyzing transaction: {str(e)}")
        return 'UNKNOWN'