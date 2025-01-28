# /path/to/handlers/smart_money_handlers.py

import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import asyncio
from aiogram import F

from src.services.smart_money import SmartMoneyTracker, token_info  # Импортируем класс
from src.bot.states import SmartMoneyStates
from src.bot.handlers.buy import _format_price
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

router = Router()
smart_money_tracker = SmartMoneyTracker()  # Создаём экземпляр класса


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


# Хендлер для нажатия кнопки "Smart Money"
@router.callback_query(F.data == "smart_money", flags={"priority": 5})
async def on_smart_money_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Smart Money"""
    try:
        await callback_query.message.answer(
            "🧠 Smart Money Анализ\n\n"
            "Отправьте адрес токена для анализа.\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=ForceReply(selective=True)
        )
        await state.set_state(SmartMoneyStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопки Smart Money: {e}")
        await callback_query.answer("❌ Произошла ошибка")


# Хендлер для команды /smart
@router.message(Command("smart"), flags={"priority": 5})
async def handle_smart_money_command(message: types.Message):
    """Обработчик команды для Smart Money анализа"""
    try:
        # Получаем адрес токена из команды
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

        # Отправляем сообщение о начале анализа
        status_message = await message.reply(
            "🔍 Анализируем токен и получаем информацию о трейдерах...\n"
            "Это может занять некоторое время"
        )

        try:
            # Анализируем токен через SmartMoneyTracker
            metadata, traders = await asyncio.wait_for(
                smart_money_tracker.analyze_accounts(token_address),
                timeout=60  # 60 секунд таймаут
            )

            # Форматируем результат
            result_message = format_smart_money_message(metadata, traders)

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
                "Попробуйте позже"
            )
            return

    except Exception as e:
        logger.error(f"Ошибка в команде Smart Money: {e}")
        await message.reply(
            "❌ Произошла ошибка при анализе токена\n"
            "Пожалуйста, попробуйте позже или проверьте адрес токена"
        )


# Хендлер для ввода адреса токена
@router.message(SmartMoneyStates.waiting_for_token)
async def handle_token_address_input(message: types.Message, state: FSMContext):
    """Обработчик ввода адреса токена для Smart Money анализа"""
    try:
        token_address = message.text.strip()

        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, отправьте корректный адрес токена или нажмите Назад для возврата в меню",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return

        # Сбрасываем состояние
        await state.clear()

        # Отправляем сообщение о начале анализа
        status_message = await message.reply(
            "🔍 Анализируем токен и получаем информацию о трейдерах...\n"
            "Это может занять некоторое время"
        )

        # Анализируем токен через SmartMoneyTracker
        traders = await smart_money_tracker.analyze_accounts(Pubkey.from_string(token_address))
        print(f"Traders: {traders}")
        metadata = token_info(token_address)
        result_message = format_smart_money_message(metadata, traders)

        # Отправляем результат
        await status_message.edit_text(
            result_message,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка обработки ввода токена: {e}")
        await message.reply(
            "❌ Произошла ошибка при анализе токена\n"
            "Пожалуйста, попробуйте позже или проверьте адрес токена"
        )
        await state.clear()


def format_smart_money_message(metadata, traders):
    """Форматируем сообщение с результатами анализа"""
    metadata_message = (
        f"🔹 **Токен:** {metadata.get('baseTokenName')} ({metadata['baseToken'].get('symbol')})\n"
        f"💰 **Цена:** {_format_price(metadata.get('priceUsd'))} USD\n"
        f"📈 **Объём:** {_format_price(metadata.get('marketCap'))} USD\n\n"
    )
    traders_message = "🧑‍💼 **Крупнейшие трейдеры:**\n\n"
    for trader in traders:
        traders_message += (
            f"  - 📜 Адрес: `{trader['address']}`\n"
            f"    🔹 Баланс: {_format_price(trader['balance'])} USD\n"
            f"    🔹 Средний ROI: {_format_price(trader['roi'])}%\n\n"
        )
    return metadata_message + traders_message