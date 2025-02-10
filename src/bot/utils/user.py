import logging
from aiogram import types
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

def get_real_user_id(event: types.Message | CallbackQuery | types.Update) -> int:
    """Get real user ID from any event type"""

    # If it's a callback query, return user ID of the person who clicked
    if isinstance(event, CallbackQuery):
        if event.from_user and event.from_user.id:
            logger.info(f"Got user ID from callback_query.from_user: {event.from_user.id}")
            return event.from_user.id
        event = event.message  # Convert to message for further processing

    # If it's a message, return user ID of the sender
    if isinstance(event, types.Message):
        if event.from_user and event.from_user.id:
            logger.info(f"Got user ID from message.from_user: {event.from_user.id}")
            return event.from_user.id

        # Try chat as fallback (useful for groups)
        if event.chat and event.chat.id:
            logger.info(f"Got user ID from message.chat: {event.chat.id}")
            return event.chat.id

    # If we got here, we couldn't find a valid ID
    logger.error(f"Could not determine user ID from event: {event}")
    raise ValueError("Could not determine user ID")

