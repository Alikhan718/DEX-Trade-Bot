import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def parse_table_with_links(page_content: str):
    """Парсит HTML-таблицу и извлекает данные вместе со ссылками."""
    soup = BeautifulSoup(page_content, "html.parser")
    
    # На Dune элементы таблицы могут отличаться.
    # Допустим, у нас есть <thead> с <th><button>...</button></th>
    headers = [th.text.strip() for th in soup.select("thead th button")]

    rows_data = []
    for row in soup.select("tbody tr"):
        values = []
        for td in row.find_all("td"):
            link = td.find("a")
            if link:
                values.append({"text": link.text.strip(), "url": link["href"]})
            else:
                values.append(td.text.strip())
        
        row_dict = dict(zip(headers, values))
        rows_data.append(row_dict)

    return rows_data

async def scrape_dune_wallet_data(token_address: str):
    """Асинхронно открывает браузер Playwright, загружает страницу Dune и возвращает распарсенные данные."""
    url = f"https://dune.com/sunnypost/solana-multi-token-top-traders?token_address_tdfefc={token_address}"

    async with async_playwright() as p:
        # Можем выбрать другой движок: p.firefox, p.webkit
        browser = await p.chromium.launch(
            headless=False  # Для видимого запуска; True - для headless
        )
        page = await browser.new_page()
          # Логируем исходящие запросы
        page.on("request", lambda request: print(f">> {request.method} {request.url}"))

        # Логируем ответы
        page.on("response", lambda response: print(f"<< {response.status} {response.url}"))
        
        # Переходим на нужный URL
        await page.goto(url)
        
        # Ждём, когда появится таблица.  
        # Селектор ".table_table__FDV2P" — это класс, который вы используете в Selenium.
        await page.wait_for_selector(".table_table__FDV2P", timeout=40_000)

        # Получаем HTML-код таблицы целиком
        # Можно взять через page.locator('.table_table__FDV2P').inner_html()
        # или взять весь HTML страницы.
        table_html = await page.locator(".table_table__FDV2P").inner_html()
        
        await browser.close()

    # Парсим таблицу с помощью BeautifulSoup
    data = await parse_table_with_links(table_html)
    return data

async def main():
    # Пример: пробуем получить данные для конкретного token_address
    token_address = "55YanwmkJQrk2SiZRKNKVbLVz7Ht33zg6RU7uYvipump"
    data = await scrape_dune_wallet_data(token_address)
    
    print("Всего строк:", len(data))
    print("Первые 3 записи:", data[:3])

if __name__ == "__main__":
    asyncio.run(main())