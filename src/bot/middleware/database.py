import traceback
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession
import logging

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_factory):
        self.session_factory = session_factory
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with self.session_factory() as session:
            data['session'] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception as e:
                traceback.print_exc()
                logger.error(f"Error in database middleware: {e}")
                await session.rollback()
            finally:
                await session.close()