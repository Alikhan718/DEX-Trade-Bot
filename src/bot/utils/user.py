import logging
from aiogram import types
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


def get_real_user_id(event: types.Message | CallbackQuery | types.Update) -> int:
    """Get real user ID from any event type"""
    logger.info(f"Getting real user ID from event type: {type(event)}")

    # If it's a callback query
    if isinstance(event, CallbackQuery):
        if event.from_user and event.from_user.id:
            # Check if it's not a bot ID
            user_id = event.from_user.id
            if str(user_id).startswith('7871396830'):
                logger.warning(f"Got bot ID {user_id}, trying to get real user ID")
                if event.message and event.message.chat:
                    user_id = event.message.chat.id
                    logger.info(f"Using chat ID instead: {user_id}")
            logger.info(f"Got user ID from callback_query.from_user: {user_id}")
            return user_id
        event = event.message  # Convert to message for further processing

    # If it's a message
    if isinstance(event, types.Message):
        # Try from_user first
        if event.from_user and event.from_user.id:
            # Check if it's not a bot ID
            user_id = event.from_user.id
            if str(user_id).startswith('7871396830'):
                logger.warning(f"Got bot ID {user_id}, trying to get real user ID")
                if event.chat:
                    user_id = event.chat.id
                    logger.info(f"Using chat ID instead: {user_id}")
            logger.info(f"Got user ID from message.from_user: {user_id}")
            return user_id

        # Try chat as fallback
        if event.chat and event.chat.id:
            user_id = event.chat.id
            logger.info(f"Got user ID from message.chat: {user_id}")
            return user_id

    # If we got here, we couldn't find a valid ID
    logger.error(f"Could not determine user ID from event: {event}")
    raise ValueError("Could not determine user ID")
