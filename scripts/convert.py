import base64
import base58

# Приватный ключ как массив чисел
key_array = [
    
]

# Преобразование в байтовый формат
key_bytes = bytes(key_array)

# Преобразование в Base58
key_base58 = base58.b58encode(key_bytes).decode()

# Преобразование в Base64
key_base64 = base64.b64encode(key_bytes).decode()

print("Base58:", key_base58)
print("Base64:", key_base64)