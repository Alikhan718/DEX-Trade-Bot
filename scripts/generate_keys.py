from cryptography.fernet import Fernet
import base64

def generate_keys():
    """Генерирует ключи шифрования и шифрует приватный ключ"""
    # Генерируем ключ шифрования
    encryption_key = Fernet.generate_key()
    print(f"\nGenerated ENCRYPTION_KEY: {encryption_key.decode('ascii')}")
    
    # Создаем шифровальщик
    cipher_suite = Fernet(encryption_key)
    
    # Пример приватного ключа (замените на свой)
    private_key_bytes = [
        232, 180, 152, 108, 183, 236, 164, 6, 173, 8, 164, 67, 59, 100, 127, 180,
        113, 74, 29, 14, 40, 191, 87, 156, 93, 202, 188, 55, 133, 176, 82, 188,
        223, 244, 78, 239, 202, 94, 154, 38, 38, 135, 246, 124, 72, 56, 137, 94,
        35, 218, 118, 114, 232, 187, 28, 112, 151, 76, 208, 182, 193, 180, 210, 117
    ]
    
    # Шифруем приватный ключ
    encrypted_key = cipher_suite.encrypt(str(private_key_bytes).encode())
    print(f"Generated ENCRYPTED_PRIVATE_KEY: {encrypted_key.decode('ascii')}")
    
    print("\nAdd these lines to your .env file:")
    print(f"ENCRYPTION_KEY={encryption_key.decode('ascii')}")
    print(f"ENCRYPTED_PRIVATE_KEY={encrypted_key.decode('ascii')}")

if __name__ == "__main__":
    generate_keys() 