# solana_module/config.py

import os
from dotenv import load_dotenv
from solders.keypair import Keypair
from cryptography.fernet import Fernet

load_dotenv()

def get_encrypted_keypair() -> Keypair:
    """Получает зашифрованный keypair из переменных окружения"""
    try:
        # Получаем ключ шифрования
        encryption_key = os.getenv('ENCRYPTION_KEY')
        if not encryption_key:
            raise ValueError("ENCRYPTION_KEY not found in environment variables")
            
        # Получаем зашифрованный приватный ключ
        encrypted_key = os.getenv('ENCRYPTED_PRIVATE_KEY')
        if not encrypted_key:
            raise ValueError("ENCRYPTED_PRIVATE_KEY not found in environment variables")
            
        # Расшифровываем ключ
        cipher_suite = Fernet(encryption_key.encode())
        decrypted_data = cipher_suite.decrypt(encrypted_key.encode())
        
        # Преобразуем в keypair
        return Keypair.from_bytes(eval(decrypted_data.decode()))
        
    except Exception as e:
        raise ValueError(f"Error loading encrypted keypair: {e}")

# Константы конфигурации
COMPUTE_UNIT_PRICE = int(os.getenv("COMPUTE_UNIT_PRICE", "10000"))  # Значение по умолчанию: 10,000,000 лампортов за вычислительную единицу

# Получаем keypair безопасным способом
try:
    PRIVATE_KEY = get_encrypted_keypair()
except ValueError as e:
    print(f"Error: {e}")
    print("\nPlease set up your environment variables:")
    print("1. Generate a new encryption key:")
    print("   ENCRYPTION_KEY = Fernet.generate_key()")
    print("2. Encrypt your private key:")
    print("   cipher_suite = Fernet(ENCRYPTION_KEY)")
    print("   encrypted_key = cipher_suite.encrypt(str(private_key_bytes).encode())")
    print("3. Add both to your .env file:")
    print("   ENCRYPTION_KEY=your_encryption_key")
    print("   ENCRYPTED_PRIVATE_KEY=your_encrypted_private_key")
    raise