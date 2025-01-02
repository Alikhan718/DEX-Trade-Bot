import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ...services.smart_money import SmartMoneyTracker

logger = logging.getLogger(__name__)

router = Router()
smart_money_tracker = SmartMoneyTracker()

class SmartMoneyStates(StatesGroup):
    waiting_for_token = State()

@router.message(Command("smart"))
async def handle_smart_money_command(message: types.Message):
    """Обработчик команды для получения smart money информации"""
    try:
        # Извлекаем адрес токена из сообщения
        token_address = message.text.split()[1]
        
        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply("❌ Неверный адрес токена")
            return
            
        # Отправляем сообщение о начале поиска
        status_message = await message.reply("🔍 Получаем информацию о smart money...")
        
        # Получаем имя токена (можно реализовать отдельный метод)
        token_name = await _get_token_name(token_address)
        
        # Получаем и форматируем информацию
        smart_money_info = await smart_money_tracker.format_smart_money_message(
            token_address,
            token_name
        )
        
        # Обновляем сообщение с результатами
        await status_message.edit_text(
            smart_money_info,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении smart money: {e}")
        await message.reply("❌ Произошла ошибка при получении информации о smart money")

@router.callback_query(lambda c: c.data == "smart_money")
async def on_smart_money_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle Smart Money button press"""
    try:
        await callback_query.message.edit_text(
            "🧠 Smart Money Анализ\n\n"
            "Пожалуйста, отправьте адрес токен для анализа:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )
        await state.set_state(SmartMoneyStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Error in smart money button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

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
        
        # Process the smart money analysis
        status_message = await message.reply("🔍 Получаем информацию о smart money...")
        
        token_name = await _get_token_name(token_address)
        smart_money_info = await smart_money_tracker.format_smart_money_message(
            token_address,
            token_name
        )
        
        await status_message.edit_text(
            smart_money_info,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Error processing token address: {e}")
        await message.reply("❌ Произошла ошибка при анализе токена")
        await state.clear()

def _is_valid_token_address(address: str) -> bool:
    """Проверяет валидность адреса токена"""
    try:
        # Проверяем длину и формат base58
        if len(address) != 44 or not all(c in '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz' for c in address):
            return False
            
        # Пробуем создать PublicKey из адреса
        from solders.pubkey import Pubkey as PublicKey
        PublicKey.from_string(address)
        return True
        
    except Exception as e:
        logger.error(f"Token address validation error: {e}")
        return False

async def _get_token_name(token_address: str) -> str:
    """Получает имя токена по адресу"""
    try:
        # Здесь должна быть логика получения имени токена
        # Пока возвращаем заглушку
        return "Unknown Token"
    except Exception as e:
        logger.error(f"Error getting token name: {e}")
        return "Unknown Token" 