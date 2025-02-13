import traceback

import logging
from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from datetime import datetime
import uuid
from solders.keypair import Keypair
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import F
import base58

from src.bot.crud import create_initial_user_settings

from src.database.models import User
from src.services.solana_service import SolanaService
from src.bot.utils.user import get_real_user_id
from src.bot.handlers.sell import on_token_selected_via_link

router = Router()
logger = logging.getLogger(__name__)

main_menu_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    # Trading buttons
    [
        InlineKeyboardButton(text="🟢 Купить", callback_data="buy"),
        InlineKeyboardButton(text="🔴 Продать", callback_data="sell")
    ],
    # Trading features
    [
        InlineKeyboardButton(text="👥 Copy Trade", callback_data="copy_trade"),
        InlineKeyboardButton(text="🧠 Smart Money", callback_data="smart_money")
    ],
    # Orders and positions
    [
        InlineKeyboardButton(text="📊 Лимитные Ордера", callback_data="limit_orders"),
        InlineKeyboardButton(text="🚀 Открытые Позиции", callback_data="open_positions")
    ],
    # Security and wallet
    [
        InlineKeyboardButton(text="🛡️ Проверка на скам", callback_data="rugcheck"),
        InlineKeyboardButton(text="💼 Кошелек", callback_data="wallet_menu")
    ],
    # Settings and help
    [
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="help")
    ],
    # Referral
    [
        InlineKeyboardButton(text="👥 Реферальная Система", callback_data="referral_menu")
    ]
])

# Высший приоритет - базовые команды
@router.message(CommandStart(), flags={"priority": 1})
async def show_main_menu(message: types.Message, session: AsyncSession, solana_service: SolanaService, state: FSMContext):
    """Главное меню с обработкой реферального кода"""
    try:
        # Получаем ID пользователя
        user_id = get_real_user_id(message)
        logger.info(f"Processing start command for user ID: {user_id}")

        # Извлекаем реферальный код из команды (если есть)
        args = message.text.split()
        referral_code = args[1] if len(args) > 1 else None
        # logger.info(f"Referral code: {referral_code}")
        if referral_code and len(referral_code.split('-')) > 1:
            await on_token_selected_via_link(message, state, session, solana_service)
            return

        # Пытаемся найти пользователя по ID
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        # Если пользователь с таким ID уже существует
        if user:
            # Обновляем время последней активности
            user.last_activity = datetime.now()
            await session.commit()
        else:
            # Генерируем новый Solana-кошелек
            new_keypair = Keypair()
            # Получаем приватный ключ в формате base58
            private_key = base58.b58encode(bytes(new_keypair)).decode('ascii')

            # Поиск владельца реферального кода (если он передан)
            referrer = None
            if referral_code:
                referral_code = referral_code.replace("code_", "")
                referrer_stmt = select(User).where(User.referral_code == referral_code)
                referrer_result = await session.execute(referrer_stmt)
                referrer = referrer_result.unique().scalar_one_or_none()

            # Создаём нового пользователя
            user = User(
                telegram_id=user_id,
                solana_wallet=str(new_keypair.pubkey()),
                private_key=str(private_key),
                referral_code=str(uuid.uuid4())[:8],  # Генерация нового реферального кода
                total_volume=0.0,
                referral_id=referrer.id if referrer else None,  # Указываем владельца кода
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            session.add(user)
            await session.commit()
            # Отправляем сообщение владельцу реферала о новом пользователе
            if referrer:
                try:
                    message_text = f"🎉 Новый реферал присоединился с вашим кодом!"  # Используем ID нового пользователя
                    await message.bot.send_message(referrer.telegram_id, message_text)  # Используем message.bot, если bot не определён глобально
                except Exception as e:
                    logger.error(f"Error sending referral notification to {referrer.telegram_id}: {e}")

            logger.info(f"Created new user with wallet {user.solana_wallet} and referrer {referrer.id if referrer else 'None'}")

            # Отправляем приветственное сообщение
            await message.answer(
                "🎉 Добро пожаловать! Для вас создан новый Solana кошелек:\n\n"
                f"Адрес: <code>{user.solana_wallet}</code>\n\n"
                "⚠️ ВАЖНО: Храните приватный ключ в безопасном месте!\n"
                "Никогда не делитесь им ни с кем.\n"
                "Используйте кнопку «Показать приватный ключ», чтобы увидеть его.",
                parse_mode="HTML"
            )

        # Получаем баланс и цену SOL
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price

        # Создаём настройки пользователя (если нужно)
        await create_initial_user_settings(user_id, session)

        # Отправляем главное меню
        from src.bot.handlers.buy import _format_price
        await message.answer(
            f"💳 Баланс кошелька: {_format_price(balance)} SOL (${_format_price(usd_balance)})\n"
            f"💳 Адрес: <code>{user.solana_wallet}</code>\n\n"
            "Выберите действие:",
            reply_markup=main_menu_keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error in start command: {e}")
        traceback.print_exc()

        await message.answer(
            "❌ Произошла ошибка при загрузке меню.\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку."
        )


@router.message(Command("reset"), flags={"priority": 1})
async def reset_user_data(message: types.Message, session: AsyncSession):
    """Сброс данных - высокий приоритет"""
    try:
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if user:
            # Log the deletion for recovery if needed
            logger.info(f"Deleting user data for {user_id}")
            logger.info(f"Wallet address was: {user.solana_wallet}")
            logger.info(f"Private key was: {user.private_key}")

            # Delete the user
            await session.delete(user)
            await session.commit()

            await message.answer(
                "🗑 Ваши данные успешно удалены из базы данных.\n"
                "Используйте /start чтобы начать заново.",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "❌ Данные не найдены в базе данных.\n"
                "Используйте /start чтобы начать.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Error resetting user data: {e}")
        await message.answer("❌ Ошибка при удалении данных")

    # Возврат в главное меню - тоже высокий приоритет


@router.callback_query(F.data == "main_menu", flags={"priority": 1})
async def back_to_main_menu(callback_query: types.CallbackQuery, session: AsyncSession, solana_service: SolanaService,
                            state: FSMContext):
    """Возврат в главное меню"""
    try:
        user_id = get_real_user_id(callback_query)
        # Изменяем запрос, чтобы избежать проблем с eager loading
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()
        await state.clear()

        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return

        # Get wallet balance and SOL price
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price
        from src.bot.handlers.buy import _format_price
        await callback_query.message.edit_text(
            f"💳 Баланс кошелька: {_format_price(balance)} SOL (${_format_price(usd_balance)})\n"
            f"💳 Адрес: <code>{user.solana_wallet}</code>\n\n"
            "Выберите действие:",
            reply_markup=main_menu_keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error returning to main menu: {e}")
        await callback_query.answer("❌ Произошла ошибка")
