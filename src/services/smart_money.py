# /path/to/async_script_with_mint_filter.py

import os
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from tenacity import retry, stop_after_attempt, wait_exponential
import requests
import time

# Load environment variables
load_dotenv()
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY not found. Make sure it is set in the .env file.")

# Constants
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
TARGET_MINT = "61V8vBaqAGMpgDQi4JcAwo1dmBGHsyhzodcPqnEVpump"


# URL для запроса
url = "https://api.coinmarketcap.com/dexer/v3/dexer/search/main-site"

# Параметры запроса
def token_info(mint: str):
    params = {
        "keyword": mint,
        "all": "false"
    }

    timestamp = int(time.time())
    user_agent = f"Custom/{timestamp}"

    # Заголовки запроса
    headers = {
        "User-Agent": user_agent
    }

    try:
        # Выполнение GET-запроса
        response = requests.get(url, params=params, headers=headers)
        
        # Проверка успешности запроса
        if response.status_code == 200:
            # Вывод данных в формате JSON
            data = response.json()
            return data['data']['pairs'][0]
        else:
            print(f"Ошибка: {response.status_code}, {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Произошла ошибка при выполнении запроса: {e}")

class SmartMoneyTracker:
    def __init__(self):
        self.client = AsyncClient(RPC_URL)

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

    async def fetch_recent_transactions(self, account_address, target_mint, max_signatures=30, days_ago=7):
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
                print(f"Error processing transaction: {tx_data}")
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
    async def analyze_accounts(self, target_mint: Pubkey, days_ago=7):
        """Analyze largest accounts for the target mint."""
        accounts = []
        try:
            largest_accounts = (await self.client.get_token_largest_accounts(target_mint)).value
            ti = float(token_info(str(target_mint))['priceUsd'])
            for account in largest_accounts:
                info = await self.account_info(account.address, target_mint, days_ago=days_ago)
                if info:
                    balance, tx_count, avg_roi = info
                    if avg_roi > 0:
                        print(f"Account: {account.address}, Balance: {float(account.amount.ui_amount) * ti} $, Transactions: {tx_count}, Avg ROI: {avg_roi:.2f}%")
                        accounts.append({'address': account.address, 'balance': float(account.amount.ui_amount) * ti, 'transactions': tx_count, 'roi': avg_roi})
        except Exception as e:
            print(f"An error occurred: {e}")
        await self.client.close()
        return sorted(accounts, key=lambda x: x['roi'], reverse=True)
    

async def account_info():
    pass

# Run the async analyzer
if __name__ == "__main__":
    analyzer = SmartMoneyTracker()
    asyncio.run(analyzer.analyze_accounts(Pubkey.from_string(TARGET_MINT), days_ago=30))