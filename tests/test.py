from requests import post


def get_mints_with_balance(data):
    """Извлекает все mint и их балансы (lamports), добавляет только те, у которых баланс > 0."""
    mints_with_balance = []

    for account in data.get("result", {}).get("value", []):
        try:
            mint = account["account"]["data"]["parsed"]["info"]["mint"]
            lamports = account["account"]["lamports"]

            if lamports > 0:
                mints_with_balance.append({"mint": mint, "lamports": lamports})

        except KeyError:
            continue  # Если нет нужных ключей, пропускаем

    return mints_with_balance


def main(address: str) -> None:
    # Solana API endpoint
    api_key = "07ee36f7-718d-4cca-bf06-4e5d062e1cab"
    solana_endpoint = f'https://mainnet.helius-rpc.com/?api-key={api_key}'

    # Запрос к API
    response = post(
        solana_endpoint,
        headers={"Content-Type": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"}
            ]
        }
    )

    if response.status_code != 200:
        print("Ошибка запроса:", response.status_code, response.text)
        return

    data = response.json()

    # Извлекаем все mint'ы с ненулевым балансом
    filtered_mints = get_mints_with_balance(data)

    print("Токены с ненулевым балансом:", filtered_mints)


main("3cLY4cPHdsDh1v7UyawbJNkPSYkw26GE7jkV8Zq1z3di")