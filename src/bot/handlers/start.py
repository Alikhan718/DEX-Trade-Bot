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
from sqlalchemy.orm import selectinload

from ...database.models import User
from ...services.solana import SolanaService
from ..utils.user import get_real_user_id

router = Router()
logger = logging.getLogger(__name__)


# Высший приоритет - базовые команды
@router.message(CommandStart(), flags={"priority": 1})
async def show_main_menu(message: types.Message, session: AsyncSession, solana_service: SolanaService):
    """Главное меню - самый высокий приоритет"""
    try:
        # Get real user ID
        user_id = get_real_user_id(message)
        logger.info(f"Processing start command for user ID: {user_id}")

        # Try to find user by any possible ID
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            # Log all existing users for debugging
            stmt = select(User)
            result = await session.execute(stmt)
            all_users = result.unique().scalars().all()
            logger.info("Current users in database:")
            for u in all_users:
                logger.info(f"ID: {u.telegram_id}, Wallet: {u.solana_wallet}")

            # Also check the alternative ID format
            alt_id = int(str(user_id).replace("bot", ""))
            stmt = select(User).where(User.telegram_id == alt_id)
            result = await session.execute(stmt)
            user = result.unique().scalar_one_or_none()

            if user:
                # Update the ID to the current one
                logger.info(f"Updating user ID from {user.telegram_id} to {user_id}")
                user.telegram_id = user_id
                await session.commit()

        if not user:
            # Generate new Solana wallet for new user
            new_keypair = Keypair()
            # Store private key as a list of integers
            private_key = list(bytes(new_keypair))

            user = User(
                telegram_id=user_id,
                solana_wallet=str(new_keypair.pubkey()),
                private_key=str(private_key),  # Store as string representation of the array
                referral_code=str(uuid.uuid4())[:8],
                total_volume=0.0,
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            session.add(user)
            await session.commit()
            logger.info(f"Created new wallet for user {user_id}: {user.solana_wallet}")

            # Send welcome message for new users
            await message.answer(
                "🎉 Добро пожаловать! Для вас создан новый Solana кошелек:\n\n"
                f"Адрес: <code>{user.solana_wallet}</code>\n\n"
                "⚠️ ВАЖНО: Храните приватный ключ в безопасном месте!\n"
                "Никогда не делитесь им ни с кем.\n"
                "Используйте кнопку «Показать приватный ключ» чтобы увидеть его.",
                parse_mode="HTML"
            )

        # Update last activity
        user.last_activity = datetime.now()
        await session.commit()

        # Get wallet balance and SOL price
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Trading buttons
            [
                InlineKeyboardButton(text="🟢 Купить", callback_data="buy"),
                InlineKeyboardButton(text="🔴 Продать", callback_data="sell")
            ],
            # Auto-buy settings
            [
                InlineKeyboardButton(text="⚡️ Автобай", callback_data="auto_buy_settings")
            ],
            # Trading features
            [
                InlineKeyboardButton(text="👥 Copy Trade", callback_data="copy_trade"),
                InlineKeyboardButton(text="🧠 Smart Wallet", callback_data="smart_money")
            ],
            # Orders and positions
            [
                InlineKeyboardButton(text="📊 Лимитные Ордера", callback_data="limit_orders"),
                InlineKeyboardButton(text="📈 Открытые Позиции", callback_data="open_positions")
            ],
            # Security and wallet
            [
                InlineKeyboardButton(text="🛡️ Проверка на скам", callback_data="rugcheck"),
                InlineKeyboardButton(text="💼 Кошелек", callback_data="wallet_menu")
            ],
            # Settings and help
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
                InlineKeyboardButton(text="❓ Помощь", callback_data="help")
            ],
            # Referral
            [
                InlineKeyboardButton(text="👥 Реферальная Система", callback_data="referral")
            ]
        ])

        await message.answer(
            f"💳 Баланс кошелька: {balance:.4f} SOL (${usd_balance:.2f})\n"
            f"💳 Адрес: <code>{user.solana_wallet}</code>\n\n"
            "Выберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
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

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Trading buttons
            [
                InlineKeyboardButton(text="🟢 Купить", callback_data="buy"),
                InlineKeyboardButton(text="🔴 Продать", callback_data="sell")
            ],
            # Auto-buy settings
            [
                InlineKeyboardButton(text="⚡️ Автобай", callback_data="auto_buy_settings")
            ],
            # Trading features
            [
                InlineKeyboardButton(text="👥 Copy Trade", callback_data="copy_trade"),
                InlineKeyboardButton(text="🧠 Smart Wallet", callback_data="smart_money")
            ],
            # Orders and positions
            [
                InlineKeyboardButton(text="📊 Лимитные Ордера", callback_data="limit_orders"),
                InlineKeyboardButton(text="📈 Открытые Позиции", callback_data="open_positions")
            ],
            # Security and wallet
            [
                InlineKeyboardButton(text="🛡️ Проверка на скам", callback_data="rugcheck"),
                InlineKeyboardButton(text="💼 Кошелек", callback_data="wallet_menu")
            ],
            # Settings and help
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
                InlineKeyboardButton(text="❓ Помощь", callback_data="help")
            ],
            # Referral
            [
                InlineKeyboardButton(text="👥 Реферальная Система", callback_data="referral")
            ]
        ])

        await callback_query.message.edit_text(
            f"💳 Баланс кошелька: {balance:.4f} SOL (${usd_balance:.2f})\n"
            f"💳 Адрес: <code>{user.solana_wallet}</code>\n\n"
            "Выберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error returning to main menu: {e}")
        await callback_query.answer("❌ Произошла ошибка")
