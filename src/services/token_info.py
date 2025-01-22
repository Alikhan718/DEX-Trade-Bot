import logging
import aiohttp
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from src.solana_module.solana_analyzer import token_info

logger = logging.getLogger(__name__)


@dataclass
class TokenInfo:
    name: str
    symbol: str
    price_usd: float
    market_cap: float
    is_renounced: bool
    is_burnt: bool
    address: str


class TokenInfoService:
    def __init__(self):
        self.session = None
        self.cache = {}
        self.cache_ttl = 300  # 5 минут

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def get_token_info(self, token_address: str) -> TokenInfo:
        """Получает информацию о токене"""
        try:
            # Проверяем кэш
            if token_address in self.cache:
                cached_data, timestamp = self.cache[token_address]
                if (datetime.now().timestamp() - timestamp) < self.cache_ttl:
                    return cached_data

            await self._ensure_session()

            # Получаем данные с pump.fun

            token_json = token_info(token_address)
            print(token_json)
            token_json_obj = TokenInfo(
                name=token_json.get("baseToken", []).get("name", "Unknown Token"),
                symbol=token_json.get("baseToken", []).get("symbol", "???"),
                price_usd=float(token_json.get("priceUsd", 0)),
                market_cap=float(token_json.get("marketCap", 0)),
                is_renounced=token_json.get("isRenounced", False),
                is_burnt=token_json.get("isBurnt", False),
                address=token_address
            )
            print(token_json_obj)
            # self.cache[token_address] = (token_info, datetime.now().timestamp())
            return token_json_obj

            # async with self.session.get(f"https://api.pump.fun/token/{token_address}") as response:
            #     if response.status == 200:
            #         data = await response.json()
            #
            #         token_info = TokenInfo(
            #             name=data.get("name", "Unknown Token"),
            #             symbol=data.get("symbol", "???"),
            #             price_usd=float(data.get("price", 0)),
            #             market_cap=float(data.get("marketCap", 0)),
            #             is_renounced=data.get("isRenounced", False),
            #             is_burnt=data.get("isBurnt", False),
            #             address=token_address
            #         )
            #
            #         # Кэшируем результат
            #         self.cache[token_address] = (token_info, datetime.now().timestamp())
            #
            #         return token_info
            #     else:
            #         logger.warning(f"Failed to get token info: {response.status}")
            #         return self._get_default_token_info(token_address)

        except Exception as e:
            logger.error(f"Error getting token info: {e}")
            return self._get_default_token_info(token_address)

    def _get_default_token_info(self, token_address: str) -> TokenInfo:
        """Возвращает информацию по умолчанию, если не удалось получить данные"""
        return TokenInfo(
            name="Unknown Token",
            symbol="???",
            price_usd=0.0,
            market_cap=0.0,
            is_renounced=False,
            is_burnt=False,
            address=token_address
        )

    async def close(self):
        """Закрывает сессию"""
        if self.session:
            await self.session.close()
            self.session = None
