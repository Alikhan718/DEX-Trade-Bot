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
            logger.info(f"Raw transaction data: {transaction}")
            
            result = transaction.get("params", {}).get("result", {}).get("value", {})
            signature = result.get("signature", "Unknown")
            logs = result.get("logs", [])

            logger.info(f"Processing transaction {signature}")
            logger.info(f"Transaction logs: {logs}")

            # Infer transaction type from logs
            tx_type = self.infer_type_from_logs(logs)

            if tx_type == "BUY":
                logger.info(f"BUY transaction detected: {signature}")
                
                # Call transaction callback with signature
                if self.transaction_callback:
                    logger.info(f"Calling transaction callback for BUY transaction")
                    try:
                        await self.transaction_callback(leader, tx_type, signature, None)
                        logger.info("Transaction callback completed successfully")
                    except Exception as e:
                        logger.error(f"Error in transaction callback: {e}")
                else:
                    logger.warning("No transaction callback set")

                # Notify followers
                followers = self.leader_follower_map.get(leader, set())
                for follower in followers:
                    logger.info(f"Notifying follower {follower} of transaction {signature} ({tx_type})")

        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            logger.error(f"Transaction data: {transaction}")
            raise

    def infer_type_from_logs(self, logs: list) -> str:
        """
        Infer transaction type from logs.
        """
        if not logs:
            return "UNKNOWN"

        for log in logs:
            if isinstance(log, str):
                if "Instruction: Buy" in log:
                    return "BUY"
                if "Instruction: Sell" in log:
                    return "SELL"

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