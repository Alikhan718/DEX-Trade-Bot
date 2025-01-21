import requests
import time

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

    # Заголовки запроса
    headers = {
        "User-Agent": user_agent
    }

    try:
        # Выполнение GET-запроса
        response = requests.get(url, params=params, headers=headers)
        
        # Проверка успешности запроса
        if response.status_code == 200:
            # Вывод данных в формате JSON
            data = response.json()
            return data['data']['pairs'][0]
        else:
            print(f"Ошибка: {response.status_code}, {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Произошла ошибка при выполнении запроса: {e}")


print(token_info("4q9fJRXnGLNJiavjaySmvrg9gkFaGW77Ci19x29dpump"))