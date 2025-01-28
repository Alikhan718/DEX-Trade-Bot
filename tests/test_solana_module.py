# main.py

import asyncio
import logging
import traceback
from solders.pubkey import Pubkey
from src.solana_module import SolanaClient, get_bonding_curve_address, find_associated_bonding_curve
from solders.system_program import transfer, TransferParams
import os
from dotenv import load_dotenv

load_dotenv()



# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,  # Измените на DEBUG, чтобы видеть более подробные логи
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("main.log"),
    ]
)
logger = logging.getLogger(__name__)

async def main():
    solana_client = SolanaClient(compute_unit_price=1, private_key=os.getenv("SECRET_KEY"))
    
    # await solana_client.send_transfer_transaction(
    #     recipient_address="9pfjT74LHMtauADsjSEXfhvLcr6S1dpL6yF5yERJjarg",
    #     amount_sol=0.0001,
    # )
    await solana_client.get_tokens("3cLY4cPHdsDh1v7UyawbJNkPSYkw26GE7jkV8Zq1z3di")

if __name__ == "__main__":
    asyncio.run(main())