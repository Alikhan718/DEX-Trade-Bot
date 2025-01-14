import asyncio
import json
import logging
import websockets
from typing import Set, Dict
from dotenv import load_dotenv
import os
from .solana_client import SolanaClient
from solders.keypair import Keypair

load_dotenv()

# Helius WebSocket Configuration
WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={os.getenv('API_KEY')}"

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class SolanaMonitor:
    def __init__(self):
        self.client = SolanaClient(compute_unit_price=os.getenv('COMPUTE_UNIT_PRICE'))
        self.leader_follower_map: Dict[str, Set[str]] = {}
        self.total_transactions_processed = 0
        self.is_monitoring = False
        self.tasks: Dict[str, asyncio.Task] = {}  # Map leader to its monitoring task
        self.transaction_callback = None  # Add callback field

    async def connect_and_subscribe(self, address: str):
        """
        Connect to Helius WebSocket and subscribe to logs for the given address.
        Reconnects automatically if the connection is lost.
        """
        while self.is_monitoring:
            try:
                logger.info(f"Connecting to Helius WebSocket for address: {address}")
                async with websockets.connect(WS_URL) as websocket:
                    # Prepare the subscription payload
                    subscribe_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [address]},
                            {"commitment": "finalized"}
                        ]
                    }
                    await websocket.send(json.dumps(subscribe_payload))
                    logger.info(f"Subscribed to logs for address: {address}")

                    # Process incoming logs
                    while self.is_monitoring:
                        response = await websocket.recv()
                        data = json.loads(response)
                        await self.process_transaction(address, data)
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection for {address} closed: {e}. Reconnecting...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in WebSocket connection for {address}: {e}. Retrying...")
                await asyncio.sleep(5)

    async def process_transaction(self, leader: str, transaction: dict):
        """
        Process a single transaction from the WebSocket stream.
        """
        self.total_transactions_processed += 1

        try:
            logger.info(f"[MONITOR] Processing transaction for leader {leader}")
            # logger.info(f"[MONITOR] Raw transaction data: {json.dumps(transaction, indent=2)}")
            
            result = transaction.get("params", {}).get("result", {}).get("value", {})
            signature = result.get("signature", "Unknown")
            logs = result.get("logs", [])

            logger.info(f"[MONITOR] Extracted signature: {signature}")
            logger.info(f"[MONITOR] Transaction logs: {json.dumps(logs, indent=2)}")

            # Infer transaction type from logs
            tx_type = self.infer_type_from_logs(logs)
            logger.info(f"[MONITOR] Inferred transaction type: {tx_type}")

            if tx_type == "BUY":
                logger.info(f"[MONITOR] BUY transaction detected: {signature}")
                
                # Extract token address from transaction
                token_address = None
                try:
                    tx_info = await self.client.get_transaction(signature)
                    print("tx_infooooooo", tx_info)
                    if tx_info:
                        # Get mint address from accounts[2] (third account in instruction)
                        token_address = tx_info["token_address"]
                        logger.info(f"[MONITOR] Extracted token address: {token_address}")
                except Exception as e:
                    logger.error(f"[MONITOR] Error extracting token address: {str(e)}")
                
                # Call transaction callback with signature
                if self.transaction_callback:
                    logger.info(f"[MONITOR] Calling transaction callback for BUY transaction")
                    try:
                        await self.transaction_callback(leader, tx_type, signature, token_address)
                        logger.info("[MONITOR] Transaction callback completed successfully")
                    except Exception as e:
                        logger.error(f"[MONITOR] Error in transaction callback: {str(e)}")
                        logger.error(f"[MONITOR] Error details: {type(e).__name__}")
                        import traceback
                        logger.error(f"[MONITOR] Traceback: {traceback.format_exc()}")
                else:
                    logger.warning("[MONITOR] No transaction callback set")

                # Notify followers
                followers = self.leader_follower_map.get(leader, set())
                logger.info(f"[MONITOR] Notifying {len(followers)} followers for leader {leader}")
                for follower in followers:
                    logger.info(f"[MONITOR] Notifying follower {follower} of transaction {signature} ({tx_type})")

            if tx_type == "SELL":
                logger.info(f"[MONITOR] SELL transaction detected: {signature}")
                # Extract token address from transaction
                token_address = None
                try:
                    tx_info = await self.client.get_transaction(signature)
                    print("tx_infooooooo", tx_info)
                    if tx_info:
                        # Get mint address from accounts[2] (third account in instruction)
                        token_address = tx_info["token_address"]
                        logger.info(f"[MONITOR] Extracted token address: {token_address}")
                except Exception as e:
                    logger.error(f"[MONITOR] Error extracting token address: {str(e)}")
                
                # Call transaction callback with signature
                if self.transaction_callback:
                    logger.info(f"[MONITOR] Calling transaction callback for SELL transaction")
                    try:
                        await self.transaction_callback(leader, tx_type, signature, token_address)
                        logger.info("[MONITOR] Transaction callback completed successfully")
                    except Exception as e:
                        logger.error(f"[MONITOR] Error in transaction callback: {str(e)}")
                        logger.error(f"[MONITOR] Error details: {type(e).__name__}")
                        import traceback
                        logger.error(f"[MONITOR] Traceback: {traceback.format_exc()}")
                else:
                    logger.warning("[MONITOR] No transaction callback set")

                # Notify followers
                followers = self.leader_follower_map.get(leader, set())
                logger.info(f"[MONITOR] Notifying {len(followers)} followers for leader {leader}")
                for follower in followers:
                    logger.info(f"[MONITOR] Notifying follower {follower} of transaction {signature} ({tx_type})")

        except Exception as e:
            logger.error(f"[MONITOR] Error processing transaction: {str(e)}")
            logger.error(f"[MONITOR] Error type: {type(e).__name__}")
            logger.error(f"[MONITOR] Transaction data: {json.dumps(transaction, indent=2)}")
            import traceback
            logger.error(f"[MONITOR] Traceback: {traceback.format_exc()}")
            raise

    def infer_type_from_logs(self, logs: list) -> str:
        """
        Infer transaction type from logs.
        """
        if not logs:
            logger.warning("[MONITOR] No logs found in transaction")
            return "UNKNOWN"

        logger.info(f"[MONITOR] Analyzing {len(logs)} logs")
        for log in logs:
            if isinstance(log, str):
                logger.info(f"[MONITOR] Analyzing log: {log}")
                if "Instruction: Buy" in log:
                    logger.info("[MONITOR] Found BUY instruction")
                    return "BUY"
                if "Instruction: Sell" in log:
                    logger.info("[MONITOR] Found SELL instruction")
                    return "SELL"
                # Добавляем дополнительные проверки для определения типа транзакции
                if "Program log: Executing sell" in log:
                    logger.info("[MONITOR] Found SELL in program log")
                    return "SELL"
                if "Program log: Executing buy" in log:
                    logger.info("[MONITOR] Found BUY in program log")
                    return "BUY"

        logger.warning("[MONITOR] Could not determine transaction type from logs")
        return "UNKNOWN"

    def add_leader(self, leader: str):
        """
        Add a leader to monitor. Starts monitoring immediately if monitoring is active.
        """
        if leader not in self.leader_follower_map:
            self.leader_follower_map[leader] = set()
            logger.info(f"Added leader {leader} for monitoring.")

            # Start monitoring the new leader if the monitor is active
            if self.is_monitoring and leader not in self.tasks:
                task = asyncio.create_task(self.connect_and_subscribe(leader))
                self.tasks[leader] = task
                logger.info(f"Started monitoring leader {leader}.")

    def add_relationship(self, leader: str, follower: str):
        """
        Add a follower for a specific leader.
        """
        if leader not in self.leader_follower_map:
            self.add_leader(leader)
        self.leader_follower_map[leader].add(follower)
        logger.info(f"Added follower {follower} for leader {leader}.")

    async def start_monitoring(self):
        """
        Start monitoring all leaders in separate WebSocket connections.
        """
        self.is_monitoring = True
        for leader in self.leader_follower_map.keys():
            if leader not in self.tasks:
                task = asyncio.create_task(self.connect_and_subscribe(leader))
                self.tasks[leader] = task
        logger.info(f"Started monitoring {len(self.tasks)} leaders.")

    async def stop_monitoring(self):
        """
        Stop all monitoring tasks gracefully.
        """
        self.is_monitoring = False
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        logger.info("Stopped all monitoring tasks.")
        self.tasks.clear()

    def set_transaction_callback(self, callback):
        """Set the callback function to be called when a transaction is detected."""
        self.transaction_callback = callback
        logger.info("Transaction callback set: " + callback.__name__)