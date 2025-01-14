import logging
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F

logger = logging.getLogger(__name__)

router = Router()

@router.callback_query(F.data == "help", flags={"priority": 6})
async def on_help_button(callback_query: CallbackQuery):
    """Помощь"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    
    await callback_query.message.edit_text(
        "❓ Помощь и поддержка\n\n"
        "Если у вас возникли вопросы или нужна помощь, обратитесь в нашу службу поддержки:\n\n"
        "📱 Telegram: @dextradebotsupport\n\n"
        "Наша команда поддержки готова помочь вам с любыми вопросами!",
        reply_markup=keyboard
    ) 