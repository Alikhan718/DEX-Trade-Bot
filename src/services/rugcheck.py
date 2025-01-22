import logging
import aiohttp
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TokenMetadata:
    name: str
    symbol: str
    uri: str
    mutable: bool
    update_authority: str


@dataclass
class TokenRisk:
    name: str
    description: str
    level: str
    score: int
    value: str


@dataclass
class RugCheckResult:
    mint: str
    score: int
    risks: List[TokenRisk]
    token_meta: TokenMetadata
    rugged: bool
    total_market_liquidity: float
    verification: bool


class RugCheckService:
    BASE_URL = "https://api.rugcheck.xyz/v1"
    JUPITER_API = "https://token.jup.ag/all"

    def __init__(self):
        self.session = None
        self._jupiter_tokens = None

    async def _ensure_session(self):
        """Ensures that the session is created"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def _get_jupiter_token_info(self, token_address: str) -> Optional[dict]:
        """Получает информацию о токене из Jupiter API"""
        try:
            if self._jupiter_tokens is None:
                async with self.session.get(self.JUPITER_API) as response:
                    if response.status == 200:
                        tokens = await response.json()
                        self._jupiter_tokens = {t["address"]: t for t in tokens}
                    else:
                        logger.error(f"Failed to fetch Jupiter tokens: {response.status}")
                        return None

            return self._jupiter_tokens.get(token_address)
        except Exception as e:
            logger.error(f"Error fetching Jupiter token info: {e}")
            return None

    async def check_token(self, token_address: str) -> RugCheckResult:
        """Проверяет токен через RugCheck API"""
        try:
            await self._ensure_session()

            # Сначала пробуем получить информацию из Jupiter API
            jupiter_info = await self._get_jupiter_token_info(token_address)

            async with self.session.get(f"{self.BASE_URL}/tokens/{token_address}/report/summary") as response:
                if response.status != 200:
                    logger.error(f"RugCheck API error: {response.status}")
                    return self._create_error_result(token_address)

                data = await response.json()

                # Проверяем наличие основных данных
                if not data or not isinstance(data, dict):
                    logger.error("Invalid API response format")
                    return self._create_error_result(token_address)

                # Безопасное получение метаданных токена
                token_meta_data = data.get("tokenMeta", {})

                # Если метаданные отсутствуют или неполные, используем данные из Jupiter
                if jupiter_info and (not token_meta_data or token_meta_data.get("name") == "Unknown Token"):
                    token_meta = TokenMetadata(
                        name=jupiter_info.get("name", "Unknown Token"),
                        symbol=jupiter_info.get("symbol", "???"),
                        uri=token_meta_data.get("uri", ""),
                        mutable=token_meta_data.get("mutable", True),
                        update_authority=token_meta_data.get("updateAuthority", "unknown")
                    )
                else:
                    token_meta = TokenMetadata(
                        name=token_meta_data.get("name", "Unknown Token"),
                        symbol=token_meta_data.get("symbol", "???"),
                        uri=token_meta_data.get("uri", ""),
                        mutable=token_meta_data.get("mutable", True),
                        update_authority=token_meta_data.get("updateAuthority", "unknown")
                    )

                # Безопасное получение и валидация скора
                score = data.get("score", 0)
                if not isinstance(score, (int, float)) or score < 0:
                    score = 0
                elif score > 100:
                    score = 100

                # Безопасное получение рисков
                risks = []
                critical_risks = 0
                high_risks = 0

                for risk in data.get("risks", []):
                    if not isinstance(risk, dict):
                        continue

                    risk_name = risk.get("name", "").lower()
                    risk_value = str(risk.get("value", "0")).rstrip("%")
                    try:
                        risk_value = float(risk_value)
                    except (ValueError, TypeError):
                        risk_value = 0

                    # Определяем уровень риска на основе типа и значения
                    if "lp unlocked" in risk_name and risk_value >= 90:
                        risk_level = "CRITICAL"
                        critical_risks += 1
                    elif "single holder ownership" in risk_name and risk_value >= 80:
                        risk_level = "CRITICAL"
                        critical_risks += 1
                    elif "high ownership" in risk_name and "top 10" not in risk_name.lower():
                        risk_level = "HIGH"
                        high_risks += 1
                    elif "low liquidity" in risk_name:
                        risk_level = "HIGH"
                        high_risks += 1
                    elif "top 10 holders" in risk_name and risk_value >= 70:
                        risk_level = "HIGH"
                        high_risks += 1
                    elif "mutable" in risk_name:
                        risk_level = "MEDIUM"
                    elif "low amount of lp providers" in risk_name:
                        risk_level = "MEDIUM"
                    else:
                        risk_level = risk.get("level", "").upper()
                        if risk_level not in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
                            risk_level = "INFO"

                        if risk_level == "CRITICAL":
                            critical_risks += 1
                        elif risk_level == "HIGH":
                            high_risks += 1

                    risks.append(TokenRisk(
                        name=risk.get("name", "Unknown Risk"),
                        description=risk.get("description", "No description"),
                        level=risk_level,
                        score=min(max(risk.get("score", 0), 0), 100),  # Ограничиваем 0-100
                        value=str(risk.get("value", ""))
                    ))

                # Если токен не найден в Jupiter и нет метаданных, добавляем предупреждение
                if not jupiter_info and token_meta.name == "Unknown Token":
                    risks.append(TokenRisk(
                        name="Unknown Token",
                        description="Token not found in Jupiter API. This might be a new or unlisted token.",
                        level="HIGH",
                        score=50,
                        value="warning"
                    ))
                    high_risks += 1

                # Определяем статус rugged на основе количества критических и высоких рисков
                is_rugged = critical_risks > 0 or high_risks >= 2

                return RugCheckResult(
                    mint=data.get("mint", token_address),
                    score=score,
                    risks=risks,
                    token_meta=token_meta,
                    rugged=is_rugged,
                    total_market_liquidity=0.0,  # Не используем ликвидность
                    verification=bool(data.get("verification"))
                )

        except Exception as e:
            logger.error(f"Error checking token {token_address}: {e}")
            return self._create_error_result(token_address)

    async def close(self):
        """Close the session if it exists"""
        if self.session:
            await self.session.close()
            self.session = None

    def _create_error_result(self, token_address: str) -> RugCheckResult:
        """Создает результат с ошибкой"""
        return RugCheckResult(
            mint=token_address,
            score=0,
            risks=[TokenRisk(
                name="Error",
                description="Failed to fetch token data",
                level="HIGH",
                score=0,
                value="error"
            )],
            token_meta=TokenMetadata(
                name="Error Loading Token",
                symbol="ERR",
                uri="",
                mutable=True,
                update_authority="unknown"
            ),
            rugged=True,  # Помечаем как опасный при ошибке
            total_market_liquidity=0.0,
            verification=False
        )

    def format_risk_level(self, level: str) -> str:
        """Преобразует уровень риска в эмодзи"""
        return {
            "CRITICAL": "🔴",
            "HIGH": "🟠",
            "MEDIUM": "🟡",
            "LOW": "🟢",
            "INFO": "ℹ️"
        }.get(level.upper(), "❓")
