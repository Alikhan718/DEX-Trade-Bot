import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey as PublicKey
from solders.signature import Signature

from ..utils.config import Config

logger = logging.getLogger(__name__)

# Обновляем список RPC эндпоинтов
BACKUP_RPC_URLS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.projectserum.com",
    "https://api.metaplex.solana.com",
    "https://api.devnet.solana.com",
]

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
        """Initialize smart money tracker"""
        self.rpc_clients = [AsyncClient(url) for url in BACKUP_RPC_URLS]
        self.current_rpc_index = 0
        self.cache = {}
        self.cache_ttl = 300
        self.delay_between_requests = 1.0
        self.max_retries = 3  # Максимальное количество попыток для каждого RPC
        
    async def _get_next_rpc_client(self):
        """Gets the next available RPC client"""
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_clients)
        return self.rpc_clients[self.current_rpc_index]

    async def _make_rpc_request(self, method: str, *args, **kwargs) -> Optional[Dict]:
        """Делает RPC запрос с обработкой ошибок и ротацией эндпоинтов"""
        for _ in range(self.max_retries):
            for client in self.rpc_clients:
                try:
                    method_to_call = getattr(client, method)
                    response = await method_to_call(*args, **kwargs)
                    
                    if response and hasattr(response, 'value'):
                        return response
                        
                except Exception as e:
                    logger.debug(f"RPC request failed for {client._provider.endpoint_uri}: {str(e)}")
                    await asyncio.sleep(self.delay_between_requests)
                    continue
            
            # Если все эндпоинты не сработали, увеличиваем задержку
            self.delay_between_requests *= 2
            
        return None

    async def _fetch_token_transactions(self, token_address: str) -> List[Dict]:
        """Fetches all transactions for a token"""
        transactions = []
        max_transactions = 100  # Увеличиваем лимит транзакций
        max_attempts = 3  # Максимальное количество попыток
        attempt = 0
        
        try:
            while attempt < max_attempts:
                # Получаем подписи транзакций
                response = await self._make_rpc_request(
                    'get_signatures_for_address',
                    PublicKey.from_string(token_address),
                    limit=max_transactions
                )
                
                if not response or not response.value:
                    logger.warning(f"No signatures found for token {token_address} after attempt {attempt + 1}")
                    attempt += 1
                    continue
                
                # Если нашли подписи, обрабатываем их
                signatures_found = False
                for sig_info in response.value[:max_transactions]:
                    try:
                        signature = str(sig_info.signature)
                        logger.debug(f"Processing transaction {signature}")
                        
                        # Получаем транзакцию
                        tx_response = await self._make_rpc_request(
                            'get_transaction',
                            Signature.from_string(signature)
                        )
                        
                        if tx_response and tx_response.value:
                            signatures_found = True
                            tx_data = self._extract_transaction_data(tx_response.value, signature)
                            if tx_data:
                                transactions.append(tx_data)
                                if len(transactions) >= 20:  # Останавливаемся после 20 успешных транзакций
                                    return transactions
                                
                    except Exception as e:
                        logger.debug(f"Failed to process transaction {signature}: {str(e)}")
                        continue
                
                # Если нашли хотя бы одну подпись, прерываем цикл
                if signatures_found:
                    break
                    
                attempt += 1
                
            logger.info(f"Finished fetching transactions. Found: {len(transactions)}")
            return transactions
                    
        except Exception as e:
            logger.error(f"Failed to fetch transactions: {str(e)}")
            return transactions

    def _extract_transaction_data(self, tx_value: Dict, signature: str) -> Optional[Dict]:
        """Extracts relevant data from transaction"""
        try:
            # Extract basic transaction data
            tx_data = {
                'signature': signature,
                'block_time': getattr(tx_value, 'blockTime', None),
                'accounts': [],
                'pre_balances': [],
                'post_balances': []
            }
            
            # Extract accounts
            if hasattr(tx_value, 'transaction'):
                tx = tx_value.transaction
                if hasattr(tx, 'message'):
                    message = tx.message
                    if hasattr(message, 'accountKeys'):
                        tx_data['accounts'] = [str(key) for key in message.accountKeys]
            
            # Extract balances
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
        """Analyzes transactions to find smart money traders"""
        traders = {}
        sol_price = 20  # Примерная цена SOL в USD
        
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
                usd_change = balance_change * sol_price
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
        )[:20]  # Увеличиваем до топ-20
        
        return sorted_traders

    async def get_token_analysis(self, token_address: str) -> Tuple[TokenMetadata, List[SmartTrader]]:
        """Gets token metadata and top traders"""
        try:
            # Check cache
            cache_key = f"analysis_{token_address}"
            current_time = datetime.now().timestamp()
            
            if cache_key in self.cache:
                cached_data, cache_time = self.cache[cache_key]
                if current_time - cache_time < self.cache_ttl:
                    return cached_data
            
            # Get fresh data
            metadata = TokenMetadata(
                name="Unknown Token",  # Используем Unknown Token вместо хардкода
                symbol="???",
                address=token_address
            )
            
            transactions = await self._fetch_token_transactions(token_address)
            if not transactions:
                logger.warning("No transactions found for analysis")
                return metadata, []
                
            top_traders = await self._analyze_trader_transactions(transactions)
            
            # Cache results
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

    def format_smart_money_message(self, metadata: TokenMetadata, traders: List[SmartTrader]) -> str:
        """Форматирует сообщение с результатами анализа"""
        lines = [
            f"🔍️ 📈 - ({metadata.name}) Smart Money information",
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
                    lines.append(f"`{wallet}`  : $-{profit} ({roi})")
        
        return "\n".join(lines)

    @staticmethod
    def _format_money(amount: float) -> str:
        """Форматирует денежную сумму в читаемый вид"""
        if amount >= 1_000_000:
            return f"{amount/1_000_000:.2f}M"
        elif amount >= 1_000:
            return f"{amount/1_000:.1f}K"
        elif amount >= 1:
            return f"{amount:.2f}"
        return f"{amount:.2f}" 