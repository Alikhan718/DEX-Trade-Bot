import asyncio
import json
import logging
import websockets
from typing import Set, Dict
from dotenv import load_dotenv
import os

load_dotenv()

# Helius WebSocket Configuration
WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={os.getenv('API_KEY')}"

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class SolanaMonitor:
    def __init__(self):
        self.leader_follower_map: Dict[str, Set[str]] = {}
        self.total_transactions_processed = 0
        self.is_monitoring = False
        self.tasks: Dict[str, asyncio.Task] = {}  # Map leader to its monitoring task

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
            result = transaction.get("params", {}).get("result", {}).get("value", {})
            signature = result.get("signature", "Unknown")
            logs = result.get("logs", [])

            # Infer transaction type from logs
            tx_type = self.infer_type_from_logs(logs)

            logger.info(f"Transaction {signature} of type {tx_type} for leader {leader}")

            # Notify followers based on transaction type
            if tx_type in {"BUY", "SELL"}:
                followers = self.leader_follower_map.get(leader, set())
                for follower in followers:
                    logger.info(f"Notifying follower {follower} of transaction {signature} ({tx_type})")

        except Exception as e:
            logger.error(f"Error processing transaction: {e}")

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

# Run the monitor
if __name__ == "__main__":
    monitor = SolanaMonitor()

    async def main():
        try:
            # Add initial leaders and followers
            monitor.add_leader("2heHTw2ywe7kzA21F1XBF4unFEWrkMRogcHpT3uEyp56")
            monitor.add_relationship("3cLY4cPHdsDh1v7UyawbJNkPSYkw26GE7jkV8Zq1z3di", "follower1")

            # Start monitoring
            await monitor.start_monitoring()
            await asyncio.sleep(100)
        except KeyboardInterrupt:
            logger.info("Interrupted! Stopping monitor...")
        finally:
            await monitor.stop_monitoring()

    asyncio.run(main())