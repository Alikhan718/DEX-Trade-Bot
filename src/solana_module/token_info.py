import requests
import time
import logging

logger = logging.getLogger(__name__)

# URL для запроса
url = "https://api.coinmarketcap.com/dexer/v3/dexer/search/main-site"

# Параметры запроса
def token_info(mint):
    params = {
        "keyword": mint,
        "all": "false"
    }

    timestamp = int(time.time())
    user_agent = f"Custom/{timestamp}"

    # Заголовки запроса с отключением кэширования
    headers = {
        "User-Agent": user_agent,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    try:
        # Выполнение GET-запроса с отключенным кэшированием
        response = requests.get(url, params=params, headers=headers)

        # Проверка успешности запроса
        if response.status_code == 200:
            # Вывод данных в формате JSON
            data = response.json()
            result = data['data']['pairs'][0]
            logger.info(f"[TOKEN_INFO] Got price for {mint}: ${result.get('priceUsd', 'N/A')}")
            return result
        else:
            logger.error(f"[TOKEN_INFO] Error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"[TOKEN_INFO] Request error: {str(e)}")
        return None
