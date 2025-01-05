# solana_module/config.py

import os
from solders.keypair import Keypair

keypair_json = {
    "secret_key": [23,126,47,63,174,147,233,169,64,1,91,60,184,8,225,162,100,84,9,131,39,20,95,134,212,39,136,246,112,112,13,173,25,69,19,135,243,83,199,24,138,151,87,104,44,107,166,156,105,174,26,78,125,184,68,26,234,4,37,128,213,207,130,25]
}

#Private key
PRIVATE_KEY = Keypair.from_bytes(bytes(keypair_json["secret_key"]))
print(PRIVATE_KEY)

# Получение Compute Unit Price (аналог gas fee в Ethereum) из переменных окружения или установка значения по умолчанию
COMPUTE_UNIT_PRICE = int(os.getenv("COMPUTE_UNIT_PRICE", "10000"))  # Значение по умолчанию: 10,000,000 лампортов за вычислительную единицу