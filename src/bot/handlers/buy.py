import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ...services.solana import SolanaService
from ...services.token_info import TokenInfoService
from ...database.models import User
from .start import get_real_user_id

logger = logging.getLogger(__name__)

router = Router()
token_info_service = TokenInfoService()

class BuyStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_amount = State()
    waiting_for_slippage = State()

def _is_valid_token_address(address: str) -> bool:
    """Проверяет валидность адреса токена"""
    try:
        if len(address) != 44:
            return False
        valid_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
        return all(c in valid_chars for c in address)
    except Exception:
        return False

def _format_price(amount: float) -> str:
    """Форматирует цену в читаемый вид"""
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"{amount/1_000:.1f}K"
    else:
        return f"{amount:.2f}"

@router.callback_query(lambda c: c.data == "buy")
async def on_buy_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Купить в главном меню"""
    try:
        await callback_query.message.edit_text(
            "🔍 Введите адрес токена, который хотите купить:\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )
        await state.set_state(BuyStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Error in buy button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.message(BuyStates.waiting_for_token)
async def handle_token_input(message: types.Message, state: FSMContext, session, solana_service: SolanaService):
    """Обработчик ввода адреса токена для покупки"""
    try:
        token_address = message.text.strip()
        
        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, отправьте корректный адрес токена",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Получаем информацию о пользователе
        user_id = get_real_user_id(message)
        user = session.query(User).filter(User.telegram_id == user_id).first()
        
        if not user:
            await message.reply("❌ Ошибка: кошелек не найден")
            return
            
        # Получаем баланс кошелька
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price

        # Получаем информацию о токене
        token_info = await token_info_service.get_token_info(token_address)

        # Формируем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Тип ордера
            [
                InlineKeyboardButton(text="🟢 Купить", callback_data="market_buy"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_buy")
            ],
            # Предустановленные суммы
            [
                InlineKeyboardButton(text="0.2 SOL", callback_data="buy_0.2"),
                InlineKeyboardButton(text="0.5 SOL", callback_data="buy_0.5"),
                InlineKeyboardButton(text="1 SOL", callback_data="buy_1.0")
            ],
            [InlineKeyboardButton(text="Ввести количество SOL", callback_data="custom_amount")],
            # Slippage
            [InlineKeyboardButton(text="⚙️ Slippage: 1%", callback_data="set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Купить", callback_data="confirm_buy")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])

        # Формируем сообщение
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс кошелька:\n"
            f"• SOL Balance: {balance:.4f} SOL (${usd_balance:.2f})\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✓' if token_info.is_renounced else '✗'} "
            f"Burnt: {'✓' if token_info.is_burnt else '✗'}\n\n"
            f"🔍 Анализ: [Pump](https://www.pump.fun/{token_address})"
        )

        await message.reply(
            message_text,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True,
            reply_markup=keyboard
        )
        
        # Очищаем состояние
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error processing token address: {e}")
        await message.reply(
            "❌ Произошла ошибка при обработке адреса токена\n"
            "Пожалуйста, попробуйте позже"
        )
        await state.clear() 