from dune_client.client import DuneClient
from dune_client.query import QueryBase
from dune_client.types import QueryParameter, ParameterType

# Ваш API-ключ Dune
API_KEY = '0S7bzLE4k8QAk82tsRpivUKLz4W9en9d'

# Идентификатор запроса
QUERY_ID = 4653829

def scrape_dune_wallet_data(val):
    dune = DuneClient(api_key=API_KEY)

    # Создание объекта запроса
    query = QueryBase(name="23", query_id=QUERY_ID, params=[QueryParameter(name="token_address", value=val, parameter_type=ParameterType.from_string('text'))])

    # Выполнение запроса и получение результато
    results = dune.refresh(query)
    ans = []
    # Вывод результатов
    for i in results.get_rows():
        ans.append(i)
    return ans