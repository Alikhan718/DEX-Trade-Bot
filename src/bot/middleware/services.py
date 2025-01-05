from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
import logging

from ...services.solana import SolanaService
from ...services.smart_money import SmartMoneyTracker
from ...services.rugcheck import RugCheckService

logger = logging.getLogger(__name__)

class ServicesMiddleware(BaseMiddleware):
    def __init__(self, solana_service: SolanaService, smart_money_tracker: SmartMoneyTracker, rugcheck_service: RugCheckService):
        self.solana_service = solana_service
        self.smart_money_tracker = smart_money_tracker
        self.rugcheck_service = rugcheck_service
        super().__init__()
        
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Add services to handler data
        data["solana_service"] = self.solana_service
        data["smart_money_tracker"] = self.smart_money_tracker
        data["rugcheck_service"] = self.rugcheck_service
        
        return await handler(event, data) 