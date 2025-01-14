import logging
from datetime import datetime
import asyncio
import uuid

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from aiogram import F

from ...database.models import User
from ...services.solana import SolanaService
from solders.keypair import Keypair
from .start import get_real_user_id
from ..states import WalletStates

logger = logging.getLogger(__name__)

router = Router()

@router.callback_query(F.data == "wallet_menu", flags={"priority": 2})
async def on_wallet_menu_button(callback_query: types.CallbackQuery, session: AsyncSession, solana_service: SolanaService):
    """Handle wallet menu button press"""
    try:
        # Get user ID from the callback query itself, not the message
        user_id = get_real_user_id(callback_query)
        logger.info(f"Processing wallet menu for user ID: {user_id}")
        
        # Get user from database
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            # Also check the alternative ID format
            alt_id = int(str(user_id).replace("bot", ""))
            stmt = select(User).where(User.telegram_id == alt_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if user:
                # Update the ID to the current one
                logger.info(f"Updating user ID from {user.telegram_id} to {user_id}")
                user.telegram_id = user_id
                await session.commit()
        
        if not user:
            logger.warning(f"No user found for ID {user_id}")
            await callback_query.message.edit_text(
                "❌ Кошелек не найден. Используйте /start для создания.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return
        
        logger.info(f"Found user with wallet: {user.solana_wallet}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Показать приватный ключ", callback_data="show_private_key"),
                InlineKeyboardButton(text="📥 Импортировать кошелек", callback_data="import_wallet")
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        # Get wallet balance
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price
        
        await callback_query.message.edit_text(
            f"💼 Управление кошельком\n\n"
            f"💳 Текущий адрес: <code>{user.solana_wallet}</code>\n"
            f"💰 Баланс: {balance:.4f} SOL (${usd_balance:.2f})\n\n"
            "⚠️ ВНИМАНИЕ:\n"
            "1. Никогда не делитесь своим приватным ключом\n"
            "2. Храните его в надежном месте\n"
            "3. Потеря ключа = потеря доступа к кошельку",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error in wallet menu: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при загрузке меню кошелька",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )

@router.callback_query(F.data == "show_private_key", flags={"priority": 2})
async def on_show_private_key_button(callback_query: types.CallbackQuery, session: AsyncSession):
    """Handle show private key button press"""
    try:
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return
            
        # Показываем предупреждение перед отображением ключа
        await callback_query.message.edit_text(
            "⚠️ ВНИМАНИЕ! ВАЖНАЯ ИНФОРМАЦИЯ О БЕЗОПАСНОСТИ!\n\n"
            "🔒 Ваш приватный ключ - это доступ к вашим средствам.\n"
            "- Никогда не делитесь им ни с кем\n"
            "- Не вводите его на сторонних сайтах\n"
            "- Храните его в надежном месте\n"
            "- Сразу удалите это сообщение после просмотра\n\n"
            "Ваш приватный ключ:\n"
            f"<code>{user.private_key}</code>\n\n"
            "❗️ Это сообщение будет автоматически удалено через 30 секунд",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Удалить сейчас", callback_data="delete_key_message")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="wallet_menu")]
            ])
        )
        
        # Устанавливаем таймер на удаление сообщения
        asyncio.create_task(delete_message_after_delay(callback_query.message, 30))
        
    except Exception as e:
        logger.error(f"Error showing private key: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.callback_query(lambda c: c.data == "delete_key_message")
async def on_delete_key_message(callback_query: types.CallbackQuery):
    """Handle delete key message button press"""
    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Error deleting key message: {e}")
        await callback_query.answer("❌ Не удалось удалить сообщение")

async def delete_message_after_delay(message: types.Message, delay: int):
    """Delete message after specified delay in seconds"""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Error auto-deleting key message: {e}")

@router.callback_query(F.data == "import_wallet", flags={"priority": 2})
async def on_import_wallet_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle import wallet button press"""
    try:
        await callback_query.message.edit_text(
            "🔑 Импорт кошелька\n\n"
            "Отправьте приватный ключ в формате массива чисел.\n"
            "Например: 124,232,72,36,252,17,98,94,...\n\n"
            "⚠️ ВНИМАНИЕ: Никогда не делитесь своим приватным ключом!\n"
            "Импортируйте кошелек только из надежных источников.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="wallet_menu")]
            ]),
            parse_mode="HTML"
        )
        await state.set_state(WalletStates.waiting_for_private_key)
    except Exception as e:
        logger.error(f"Error in import wallet button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.message(WalletStates.waiting_for_private_key)
async def handle_private_key_input(message: types.Message, state: FSMContext, session: AsyncSession):
    """Handle private key input for wallet import"""
    try:
        private_key_str = message.text.strip()
        
        # Validate and convert private key
        try:
            # Convert string back to bytes
            private_key_bytes = bytes([int(i) for i in private_key_str.split(',')])
            keypair = Keypair.from_bytes(private_key_bytes)
            public_key = str(keypair.pubkey())
            
        except Exception as e:
            logger.error(f"Invalid private key format: {e}")
            await message.reply(
                "❌ Неверный формат приватного ключа.\n"
                "Убедитесь, что вы скопировали его правильно.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Попробовать снова", callback_data="import_wallet")],
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="wallet_menu")]
                ])
            )
            await state.clear()
            return
            
        # Update database
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            # Create new user if doesn't exist
            user = User(
                telegram_id=user_id,
                solana_wallet=public_key,
                private_key=private_key_str,  # Store original array string
                referral_code=str(uuid.uuid4())[:8],
                total_volume=0.0,
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            session.add(user)
        else:
            # Store old wallet info in log for recovery if needed
            logger.info(
                f"User {user_id} replacing wallet "
                f"from {user.solana_wallet[:8]}... to {public_key[:8]}..."
            )
            
            # Update existing user's wallet
            user.solana_wallet = public_key
            user.private_key = private_key_str
            user.last_activity = datetime.now()
        
        await session.commit()
        
        # Delete the message containing the private key for security
        await message.delete()
        
        # Send success message
        await message.answer(
            "✅ Кошелек успешно импортирован!\n\n"
            f"💳 Новый адрес: <code>{public_key}</code>\n\n"
            "⚠️ Сохраните приватный ключ предыдущего кошелька, если хотите вернуть к нему доступ в будущем.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💼 Открыть кошелек", callback_data="wallet_menu")]
            ])
        )
        
        # Clear state
        await state.clear()
        
    except Exception as e:
        logger.error(f"Wallet import error: {e}")
        await message.reply(
            "❌ Ошибка при импорте кошелька. Попробуйте еще раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Попробовать снова", callback_data="import_wallet")],
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="wallet_menu")]
            ])
        )
        await state.clear() 