import logging
from datetime import datetime

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ...database.models import User
from ...services.solana import SolanaService
from solders.keypair import Keypair

logger = logging.getLogger(__name__)

router = Router()

@router.callback_query(lambda c: c.data == "wallet_menu")
async def on_wallet_menu_button(callback_query: types.CallbackQuery, session, solana_service: SolanaService):
    """Handle wallet menu button press"""
    try:
        user = session.query(User).filter(
            User.telegram_id == callback_query.from_user.id
        ).first()
        
        if not user:
            await callback_query.message.edit_text(
                "❌ Кошелек не найден. Используйте /start для создания.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return
        
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

@router.callback_query(lambda c: c.data == "show_private_key")
async def on_show_private_key_button(callback_query: types.CallbackQuery, session):
    """Handle show private key button press"""
    try:
        user = session.query(User).filter(
            User.telegram_id == callback_query.from_user.id
        ).first()
        
        if user:
            # Send private key in private message
            await callback_query.message.answer(
                "🔑 Ваш приватный ключ:\n\n"
                f"<code>{user.private_key}</code>\n\n"
                "⚠️ ВНИМАНИЕ:\n"
                "1. Никогда не делитесь этим ключом\n"
                "2. Сохраните его в надежном месте\n"
                "3. Потеря ключа = потеря доступа к кошельку",
                parse_mode="HTML"
            )
            await callback_query.answer("Приватный ключ отправлен в чат")
        else:
            await callback_query.answer("❌ Кошелек не найден")
    except Exception as e:
        logger.error(f"Error showing private key: {e}")
        await callback_query.answer("❌ Ошибка при показе приватного ключа")

@router.callback_query(lambda c: c.data == "import_wallet")
async def on_import_wallet_button(callback_query: types.CallbackQuery):
    """Handle import wallet button press"""
    await callback_query.message.answer(
        "🔑 Чтобы импортировать кошелек, отправьте команду:\n"
        "<code>/import_wallet PRIVATE_KEY</code>",
        parse_mode="HTML"
    )
    await callback_query.answer()

@router.message(Command("import_wallet"))
async def import_wallet(message: types.Message, session):
    """Import existing wallet using private key array"""
    try:
        # Delete message with private key for security
        await message.delete()
        
        parts = message.text.split(maxsplit=1)
        if len(parts) != 2:
            await message.answer(
                "❌ Пожалуйста, укажите приватный ключ в формате массива:\n"
                "<code>/import_wallet [1,2,3,...]</code>",
                parse_mode="HTML"
            )
            return
        
        try:
            # Parse private key array from string
            private_key_str = parts[1].strip()
            if not (private_key_str.startswith('[') and private_key_str.endswith(']')):
                raise ValueError("Invalid array format")
            
            # Convert string array to list of integers
            private_key_nums = [int(x.strip()) for x in private_key_str[1:-1].split(',')]
            if len(private_key_nums) != 64:
                raise ValueError("Private key must be 64 bytes")
            
            # Convert to bytes and create keypair
            private_key_bytes = bytes(private_key_nums)
            keypair = Keypair.from_bytes(private_key_bytes)
            public_key = str(keypair.pubkey())
            
            logger.info(f"Importing wallet with public key: {public_key[:8]}...")
            
        except Exception as e:
            logger.error(f"Invalid private key format: {e}")
            await message.answer(
                "❌ Неверный формат приватного ключа\n"
                "Используйте формат: [1,2,3,...] (64 числа)"
            )
            return
        
        # Update database
        user = session.query(User).filter(
            User.telegram_id == message.from_user.id
        ).first()
        
        if not user:
            # Create new user if doesn't exist
            user = User(
                telegram_id=message.from_user.id,
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
                f"User {message.from_user.id} replacing wallet "
                f"from {user.solana_wallet[:8]}... to {public_key[:8]}..."
            )
            
            # Update existing user's wallet
            user.solana_wallet = public_key
            user.private_key = private_key_str
            user.last_activity = datetime.now()
        
        session.commit()
        
        await message.answer(
            "✅ Кошелек успешно импортирован!\n\n"
            f"💳 Новый адрес: <code>{public_key}</code>\n\n"
            "⚠️ Сохраните приватный ключ предыдущего кошелька, если хотите вернуть к нему доступ в будущем.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Wallet import error: {e}")
        await message.answer(
            "❌ Ошибка при импорте кошелька. Попробуйте еще раз."
        ) 