# solana_module/config.py

import os
from dotenv import load_dotenv

load_dotenv()

# Константы конфигурации
COMPUTE_UNIT_PRICE = int(os.getenv("COMPUTE_UNIT_PRICE", "10000"))  # Значение по умолчанию: 10,000 лампортов за вычислительную единицу