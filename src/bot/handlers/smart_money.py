import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
from aiogram import F

from src.services.smart_money import SmartMoneyTracker
from src.bot.states import SmartMoneyStates

logger = logging.getLogger(__name__)

router = Router()
smart_money_tracker = None #process


def _is_valid_token_address(address: str) -> bool:
    """Проверяет валидность адреса токена"""
    try:
        # Проверяем длину адреса
        if len(address) != 44:
            return False

        # Проверяем, что адрес содержит только допустимые символы
        valid_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
        return all(c in valid_chars for c in address)

    except Exception:
        return False


# Приоритет 5 - аналитические функции
@router.callback_query(F.data == "smart_money", flags={"priority": 5})
async def on_smart_money_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle Smart Money button press"""
    try:
        await callback_query.message.edit_text(
            "🧠 Smart Money Анализ\n\n"
            "Отправьте адрес токена для анализа.\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )
        await state.set_state(SmartMoneyStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Error in smart money button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(Command("smart"), flags={"priority": 5})
async def handle_smart_money_command(message: types.Message):
    """Обработчик команды для получения smart money информации"""
    try:
        # Извлекаем адрес токена из сообщения
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply(
                "❌ Пожалуйста, укажите адрес токена после команды\n"
                "Пример: `/smart HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
                parse_mode="MARKDOWN"
            )
            return

        token_address = parts[1]

        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, проверьте адрес и попробуйте снова"
            )
            return

        # Отправляем сообщение о начале поиска
        status_message = await message.reply(
            "🔍 Анализируем токен и получаем информацию о трейдерах...\n"
            "Это может занять несколько секунд"
        )

        try:
            # Получаем анализ токена с таймаутом
            metadata, traders = await asyncio.wait_for(
                smart_money_tracker.get_token_analysis(token_address),
                timeout=60  # 60 секунд таймаут
            )

            # Форматируем и отправляем результат
            result_message = smart_money_tracker.format_smart_money_message(metadata, traders)

            await status_message.edit_text(
                result_message,
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )

        except asyncio.TimeoutError:
            await status_message.edit_text(
                "❌ Превышено время ожидания при анализе токена\n"
                "Пожалуйста, попробуйте позже"
            )
            return

    except Exception as e:
        logger.error(f"Ошибка при получении smart money: {e}")
        await message.reply(
            "❌ Произошла ошибка при анализе токена\n"
            "Пожалуйста, попробуйте позже или проверьте адрес токена"
        )


@router.message(SmartMoneyStates.waiting_for_token)
async def handle_token_address_input(message: types.Message, state: FSMContext):
    """Handle token address input for Smart Money analysis"""
    try:
        token_address = message.text.strip()

        # Check if it's a valid token address
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, отправьте корректный адрес токена или нажмите Назад для возврата в меню",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return

        # Reset state
        await state.clear()

        # Send processing message
        status_message = await message.reply(
            "🔍 Анализируем токен и получаем информацию о трейдерах...\n"
            "Это может занять несколько секунд"
        )

        # Get and format analysis
        metadata, traders = await smart_money_tracker.get_token_analysis(token_address)
        result_message = smart_money_tracker.format_smart_money_message(metadata, traders)

        # Send results
        await status_message.edit_text(
            result_message,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )

    except Exception as e:
        logger.error(f"Error processing token address: {e}")
        await message.reply(
            "❌ Произошла ошибка при анализе токена\n"
            "Пожалуйста, попробуйте позже или проверьте адрес токена"
        )
        await state.clear()
