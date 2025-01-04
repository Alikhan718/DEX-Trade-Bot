from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import Session

class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session: Session):
        self.session = session
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        # Добавляем сессию в данные события
        data["session"] = self.session
        
        try:
            # Вызываем следующий обработчик
            return await handler(event, data)
        except Exception as e:
            # В случае ошибки откатываем изменения
            self.session.rollback()
            raise e 