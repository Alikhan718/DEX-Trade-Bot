import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, Message, CallbackQuery
from aiogram.filters import StateFilter, Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.database.models import User
from src.bot.states import WithdrawStates
from src.services.solana_service import SolanaService
from src.bot.utils.user import get_real_user_id
from src.bot.handlers.buy import _format_price
from solders.pubkey import Pubkey

router = Router()
logger = logging.getLogger(__name__)

# Клавиатура для меню вывода
withdraw_menu_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="💰 Количество SOL", callback_data="set_withdraw_amount"),
        InlineKeyboardButton(text="📍 Адрес кошелька для вывода", callback_data="set_withdraw_address")
    ],
    [InlineKeyboardButton(text="✅ Вывести", callback_data="confirm_withdraw")],
    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
])

@router.callback_query(F.data == "withdraw", flags={"priority": 5})
async def show_withdraw_menu(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Показать меню вывода средств"""
    try:
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return

        # Получаем баланс
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        
        # Получаем сохраненные данные
        data = await state.get_data()
        withdraw_amount = data.get("withdraw_amount", "Не указано")
        withdraw_address = data.get("withdraw_address", "Не указан")
        
        menu_text = (
            f"💳 Баланс кошелька: {_format_price(balance)} SOL\n\n"
            f"📊 Сумма для вывода: {_format_price(withdraw_amount) if isinstance(withdraw_amount, (int, float)) else withdraw_amount} SOL\n"
            f"📍 Адрес получателя: {withdraw_address}\n\n"
            "Выберите действие:"
        )
        
        await callback_query.message.edit_text(
            menu_text,
            reply_markup=withdraw_menu_keyboard
        )

    except Exception as e:
        logger.error(f"Error showing withdraw menu: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )

@router.callback_query(F.data == "set_withdraw_amount", flags={"priority": 5})
async def ask_withdraw_amount(callback_query: types.CallbackQuery, state: FSMContext):
    """Запросить сумму для вывода"""
    logger.info("[WITHDRAW] Starting amount input process")
    
    # Устанавливаем новое состояние
    await state.set_state(WithdrawStates.waiting_for_amount)
    current_state = await state.get_state()
    logger.info(f"[WITHDRAW] State set to: {current_state}")
    
    # Отправляем сообщение с ForceReply
    
    await callback_query.message.answer(
        "💰 Введите сумму SOL для вывода:",
        reply_markup=ForceReply(selective=True)
    )
    
    logger.info("[WITHDRAW] Sent amount input request with ForceReply")

@router.callback_query(F.data == "set_withdraw_address", flags={"priority": 5})
async def ask_withdraw_address(callback_query: types.CallbackQuery, state: FSMContext):
    """Запросить адрес для вывода"""
    logger.info("[WITHDRAW] Starting address input process")
    
    # Сохраняем текущие данные
    current_data = await state.get_data()
    withdraw_amount = current_data.get("withdraw_amount")
    logger.info(f"[WITHDRAW] Preserved withdraw amount: {withdraw_amount}")
    
    # Очищаем состояние и сохраняем данные обратно
    
    await state.update_data(withdraw_amount=withdraw_amount)
    logger.info("[WITHDRAW] Previous state cleared, amount restored")
    
    # Устанавливаем новое состояние
    await state.set_state(WithdrawStates.waiting_for_address)
    current_state = await state.get_state()
    logger.info(f"[WITHDRAW] State set to: {current_state}")
    
    await callback_query.message.answer(
        "📍 Введите адрес кошелька для вывода:",
        reply_markup=ForceReply(selective=True)
    )
    logger.info("[WITHDRAW] Sent address input request with ForceReply")

@router.message(StateFilter(WithdrawStates.waiting_for_amount), flags={"priority": 5})
async def handle_withdraw_amount(message: Message, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Обработать введенную сумму"""
    logger.info("[WITHDRAW] Entering withdraw amount handler")
    logger.info(f"[WITHDRAW] Message text: {message.text}")
    logger.info(f"[WITHDRAW] Message type: {type(message)}")
    
    current_state = await state.get_state()
    logger.info(f"[WITHDRAW] Current state: {current_state}")
    
    try:
        # Проверяем, что введено число
        try:
            amount = float(message.text)
            logger.info(f"[WITHDRAW] Successfully parsed amount: {amount}")
        except ValueError:
            logger.info("[WITHDRAW] Invalid amount format")
            await message.answer(
                "❌ Пожалуйста, введите корректное число",
                reply_markup=ForceReply(selective=True)
            )
            return

        if amount <= 0:
            logger.info("[WITHDRAW] Amount is not positive")
            await message.answer(
                "❌ Сумма должна быть больше нуля",
                reply_markup=withdraw_menu_keyboard
            )
            return

        # Проверяем баланс
        user_id = get_real_user_id(message)
        logger.info(f"[WITHDRAW] Processing for user {user_id}")
        
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()
        
        if not user:
            logger.error("[WITHDRAW] User not found")
            await message.answer(
                "❌ Пользователь не найден",
                reply_markup=withdraw_menu_keyboard
            )
            return
        
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        logger.info(f"[WITHDRAW] User balance: {balance}, requested amount: {amount}")
        
        if amount > balance:
            logger.info("[WITHDRAW] Insufficient funds")
            await message.answer(
                "❌ Недостаточно средств на балансе",
                reply_markup=withdraw_menu_keyboard
            )
            return

        # Сохраняем сумму
        await state.update_data(withdraw_amount=amount)
        logger.info(f"[WITHDRAW] Amount {amount} saved to state")
        
        # Показываем меню вывода с обновленной информацией
        data = await state.get_data()
        address = data.get("withdraw_address", "Не указан")
        
        await message.answer(
            f"✅ Сумма установлена\n\n"
            f"💳 Баланс кошелька: {_format_price(balance)} SOL\n"
            f"💰 Сумма для вывода: {_format_price(amount)} SOL\n"
            f"📍 Адрес получателя: {address}",
            reply_markup=withdraw_menu_keyboard
        )
        logger.info("[WITHDRAW] Sent confirmation message with updated menu")

    except Exception as e:
        logger.error(f"[WITHDRAW] Error processing amount: {e}")
        await message.answer(
            "❌ Произошла ошибка при обработке суммы",
            reply_markup=withdraw_menu_keyboard
        )

@router.message(StateFilter(WithdrawStates.waiting_for_address), flags={"priority": 20})
async def handle_withdraw_address(message: Message, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Обработать введенный адрес"""
    logger.info(f"[WITHDRAW] Received address message: {message.text}")
    try:
        address = message.text.strip()
        # Проверяем валидность адреса
        try:
            Pubkey.from_string(address)
        except ValueError:
            await message.answer(
                "❌ Некорректный адрес кошелька",
                reply_markup=withdraw_menu_keyboard
            )
            return

        # Сохраняем адрес
        await state.update_data(withdraw_address=address)
        
        # Показываем меню вывода с обновленной информацией
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()
        
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        data = await state.get_data()
        amount = data.get("withdraw_amount", "Не указана")
        
        await message.answer(
            f"💳 Баланс кошелька: {_format_price(balance)} SOL\n"
            f"💰 Сумма для вывода: {_format_price(amount) if isinstance(amount, (int, float)) else amount}\n"
            f"📍 Адрес получателя: {address}",
            reply_markup=withdraw_menu_keyboard
        )

    except Exception as e:
        logger.error(f"Error handling withdraw address: {e}")
        await message.answer(
            "❌ Произошла ошибка при обработке адреса",
            reply_markup=withdraw_menu_keyboard
        )

def shorten_address(address: str, start_chars: int = 6, end_chars: int = 4) -> str:
    """Сокращает адрес, оставляя начало и конец"""
    if len(address) <= start_chars + end_chars:
        return address
    return f"{address[:start_chars]}...{address[-end_chars:]}"

@router.callback_query(F.data == "confirm_withdraw", flags={"priority": 3})
async def handle_withdraw_confirm(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Подтвердить и выполнить вывод средств"""
    try:
        # Получаем сохраненные данные
        data = await state.get_data()
        amount = data.get("withdraw_amount")
        address = data.get("withdraw_address")

        if not amount or not address:
            await callback_query.answer("❌ Укажите сумму и адрес для вывода")
            return

        # Получаем пользователя и проверяем баланс
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()
        
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        
        if amount > balance:
            await callback_query.answer("❌ Недостаточно средств на балансе")
            return

        # Отправляем уведомление о начале транзакции
        await callback_query.message.edit_text(
            f"⏳ Выполняется вывод {_format_price(amount)} SOL на адрес {shorten_address(address)}...",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[])
        )

        # Создаем клиент и выполняем транзакцию
        client = solana_service.create_client(user.private_key)
        signature = await client.send_transfer_transaction(
            recipient_address=address,
            amount_sol=amount,
            is_token_transfer=False,  # Это перевод SOL, а не токенов
        )

        if signature:
            # Транзакция успешна
            await callback_query.message.edit_text(
                f"✅ Успешно выведено {_format_price(amount)} SOL\n"
                f"📍 Адрес: {shorten_address(address)}\n"
                f"🔗 Транзакция: [Solscan](https://solscan.io/tx/{signature})",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
        else:
            raise Exception("Transaction failed")

    except Exception as e:
        logger.error(f"Error confirming withdrawal: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при выводе средств",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="withdraw")]
            ])
        )

    finally:
        await state.clear() 