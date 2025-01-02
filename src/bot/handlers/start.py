import logging
import uuid
from datetime import datetime

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ...database.models import User
from ...services.solana import SolanaService
from solders.keypair import Keypair

logger = logging.getLogger(__name__)

router = Router()

@router.message(Command("start"))
async def show_main_menu(message: types.Message, session, solana_service: SolanaService):
    """Show main menu with wallet info"""
    try:
        user = session.query(User).filter(
            User.telegram_id == message.from_user.id
        ).first()
        
        if not user:
            # Generate new Solana wallet for new user
            new_keypair = Keypair()
            # Store private key as a list of integers
            private_key = list(bytes(new_keypair))
            
            user = User(
                telegram_id=message.from_user.id,
                solana_wallet=str(new_keypair.pubkey()),
                private_key=str(private_key),  # Store as string representation of the array
                referral_code=str(uuid.uuid4())[:8],
                total_volume=0.0,
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            session.add(user)
            session.commit()
            logger.info(f"Created new wallet for user {message.from_user.id}: {user.solana_wallet}")
            
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
        session.commit()
        
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
            # Wallet and settings
            [
                InlineKeyboardButton(text="💼 Кошелек", callback_data="wallet_menu"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")
            ],
            # Help and referral
            [
                InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
                InlineKeyboardButton(text="👥 Реферальная Система", callback_data="referral")
            ]
        ])
        
        await message.answer(
            f"💳 Ваш кошелек: <code>{user.solana_wallet}</code>\n\n"
            f"💰 Баланс: {balance:.4f} SOL (${usd_balance:.2f})\n\n"
            "💡 Вы можете отправить SOL на этот адрес или импортировать существующий кошелек.\n\n"
            "Выберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        await message.answer("❌ Ошибка при загрузке меню")

@router.message(Command("reset"))
async def reset_user_data(message: types.Message, session):
    """Delete user data from database for testing"""
    try:
        user = session.query(User).filter(
            User.telegram_id == message.from_user.id
        ).first()
        
        if user:
            # Log the deletion for recovery if needed
            logger.info(f"Deleting user data for {message.from_user.id}")
            logger.info(f"Wallet address was: {user.solana_wallet}")
            logger.info(f"Private key was: {user.private_key}")
            
            # Delete the user
            session.delete(user)
            session.commit()
            
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

@router.callback_query(lambda c: c.data == "main_menu")
async def on_main_menu_button(callback_query: types.CallbackQuery, session, solana_service: SolanaService):
    """Handle main menu button press"""
    await callback_query.answer()
    await show_main_menu(callback_query.message, session, solana_service) 