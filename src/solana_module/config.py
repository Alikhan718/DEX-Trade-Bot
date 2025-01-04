# solana_module/config.py

import os
from solders.keypair import Keypair

keypair_json = {
    "secret_key": [
        232, 180, 152, 108, 183, 236, 164, 6, 173, 8, 164, 67, 59, 100, 127, 180,
        113, 74, 29, 14, 40, 191, 87, 156, 93, 202, 188, 55, 133, 176, 82, 188,
        223, 244, 78, 239, 202, 94, 154, 38, 38, 135, 246, 124, 72, 56, 137, 94,
        35, 218, 118, 114, 232, 187, 28, 112, 151, 76, 208, 182, 193, 180, 210, 117
    ]
}

#Private key
PRIVATE_KEY = Keypair.from_bytes(bytes(keypair_json["secret_key"]))
print(PRIVATE_KEY)

# Получение Compute Unit Price (аналог gas fee в Ethereum) из переменных окружения или установка значения по умолчанию
COMPUTE_UNIT_PRICE = int(os.getenv("COMPUTE_UNIT_PRICE", "1000000"))  # Значение по умолчанию: 10,000,000 лампортов за вычислительную единицу