import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ...services.solana import SolanaService
from ...services.token_info import TokenInfoService
from ...database.models import User
from .start import get_real_user_id
from ...solana_module.transaction_handler import UserTransactionHandler

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
async def handle_token_input(message: types.Message, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Handle token address input"""
    try:
        token_address = message.text.strip()
        
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, проверьте адрес и попробуйте снова"
            )
            return
            
        # Get user info
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await message.reply("❌ Пользователь не найден")
            return
            
        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await message.reply(
                "❌ Не удалось получить информацию о токене\n"
                "Пожалуйста, проверьте адрес и попробуйте снова"
            )
            return
            
        # Get wallet balance
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price
        
        # Save token address to state
        await state.update_data(token_address=token_address)
        
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
        
        await message.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error processing token address: {e}")
        await message.reply(
            "❌ Произошла ошибка при обработке адреса токена\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку"
        ) 

@router.callback_query(lambda c: c.data == "confirm_buy")
async def handle_confirm_buy(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle buy confirmation"""
    try:
        # Get user data
        user_id = get_real_user_id(callback_query)
        logger.info(f"Processing buy confirmation for user: {user_id}")
        
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            logger.error(f"User not found: {user_id}")
            await callback_query.answer("❌ Пользователь не найден")
            return
            
        # Get state data
        data = await state.get_data()
        token_address = data.get("token_address")
        amount_sol = data.get("amount_sol", 0.0)
        slippage = data.get("slippage", 1.0)
        
        logger.info(f"Buy parameters - Token: {token_address}, Amount: {amount_sol} SOL, Slippage: {slippage}%")
        
        if not token_address or not amount_sol:
            logger.error("Missing token address or amount")
            await callback_query.answer("❌ Не указан токен или сумма")
            return
            
        # Initialize transaction handler with user's private key
        try:
            logger.info("Initializing transaction handler")
            tx_handler = UserTransactionHandler(user.private_key)
        except ValueError:
            logger.error("Failed to initialize transaction handler")
            await callback_query.answer("❌ Ошибка инициализации кошелька")
            return
            
        # Send status message
        status_message = await callback_query.message.answer(
            "🔄 Выполняется покупка токена...\n"
            "Пожалуйста, подождите"
        )
        
        # Execute buy transaction
        logger.info("Executing buy transaction")
        tx_signature = await tx_handler.buy_token(
            token_address=token_address,
            amount_sol=amount_sol,
            slippage=slippage
        )
        
        if tx_signature:
            logger.info(f"Buy transaction successful: {tx_signature}")
            # Update success message
            await status_message.edit_text(
                "✅ Токен успешно куплен!\n\n"
                f"💰 Потрачено: {amount_sol} SOL\n"
                f"🔗 Транзакция: [Explorer](https://solscan.io/tx/{tx_signature})",
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
        else:
            logger.error("Buy transaction failed")
            # Update error message
            await status_message.edit_text(
                "❌ Ошибка при покупке токена\n"
                "Пожалуйста, попробуйте позже",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
        
        # Clear state
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error confirming buy: {e}")
        await callback_query.answer("❌ Произошла ошибка")
        await state.clear()

@router.callback_query(lambda c: c.data == "set_slippage")
async def handle_set_slippage(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle slippage setting button"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="0.5%", callback_data="slippage_0.5"),
            InlineKeyboardButton(text="1%", callback_data="slippage_1"),
            InlineKeyboardButton(text="2%", callback_data="slippage_2")
        ],
        [
            InlineKeyboardButton(text="3%", callback_data="slippage_3"),
            InlineKeyboardButton(text="5%", callback_data="slippage_5"),
            InlineKeyboardButton(text="Custom", callback_data="slippage_custom")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_buy")]
    ])
    
    await callback_query.message.edit_text(
        "⚙️ Настройка Slippage\n\n"
        "Выберите максимальное проскальзывание цены:\n"
        "• Чем выше slippage, тем больше вероятность успешной транзакции\n"
        "• Чем ниже slippage, тем лучше цена исполнения\n"
        "• Рекомендуемое значение: 1-2%",
        reply_markup=keyboard
    )

@router.callback_query(lambda c: c.data.startswith("slippage_"))
async def handle_slippage_choice(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle slippage choice"""
    choice = callback_query.data.split("_")[1]
    
    if choice == "custom":
        await callback_query.message.edit_text(
            "⚙️ Пользовательский Slippage\n\n"
            "Введите значение в процентах (например, 1.5):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_slippage")]
            ])
        )
        await state.set_state(BuyStates.waiting_for_slippage)
        return
        
    # Convert choice to float and save to state
    slippage = float(choice)
    await state.update_data(slippage=slippage)
    
    # Return to buy menu
    await show_buy_menu(callback_query.message, state)

@router.message(BuyStates.waiting_for_slippage)
async def handle_custom_slippage(message: types.Message, state: FSMContext):
    """Handle custom slippage input"""
    try:
        slippage = float(message.text.replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage value")
            
        await state.update_data(slippage=slippage)
        await show_buy_menu(message, state)
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_slippage")]
            ])
        )

@router.callback_query(lambda c: c.data == "back_to_buy")
async def handle_back_to_buy(callback_query: types.CallbackQuery, state: FSMContext):
    """Return to buy menu"""
    await show_buy_menu(callback_query.message, state)

async def show_buy_menu(message: types.Message, state: FSMContext):
    """Show buy menu with current settings"""
    data = await state.get_data()
    token_address = data.get("token_address")
    slippage = data.get("slippage", 1.0)
    
    # Get token info again
    token_info = await token_info_service.get_token_info(token_address)
    
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
        [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="set_slippage")],
        # Действия
        [InlineKeyboardButton(text="💰 Купить", callback_data="confirm_buy")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    
    message_text = (
        f"${token_info.symbol} 📈 - {token_info.name}\n\n"
        f"📍 Адрес токена:\n`{token_address}`\n\n"
        f"⚙️ Настройки:\n"
        f"• Slippage: {slippage}%\n\n"
        f"📊 Информация о токене:\n"
        f"• Price: ${_format_price(token_info.price_usd)}\n"
        f"• MC: ${_format_price(token_info.market_cap)}\n"
        f"• Renounced: {'✓' if token_info.is_renounced else '✗'} "
        f"Burnt: {'✓' if token_info.is_burnt else '✗'}\n\n"
        f"🔍 Анализ: [Pump](https://www.pump.fun/{token_address})"
    )
    
    await message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="MARKDOWN",
        disable_web_page_preview=True
    ) 

@router.callback_query(lambda c: c.data.startswith("buy_"))
async def handle_preset_amount(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle preset amount buttons"""
    try:
        # Extract amount from callback data
        amount = float(callback_query.data.split("_")[1])
        
        # Save amount to state
        await state.update_data(amount_sol=amount)
        
        # Update message with selected amount
        data = await state.get_data()
        token_address = data.get("token_address")
        slippage = data.get("slippage", 1.0)
        
        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await callback_query.answer("❌ Не удалось получить информацию о токене")
            return
            
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Тип ордера
            [
                InlineKeyboardButton(text="🟢 Купить", callback_data="market_buy"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_buy")
            ],
            # Предустановленные суммы с отметкой выбранной
            [
                InlineKeyboardButton(
                    text="✓ 0.2 SOL" if amount == 0.2 else "0.2 SOL",
                    callback_data="buy_0.2"
                ),
                InlineKeyboardButton(
                    text="✓ 0.5 SOL" if amount == 0.5 else "0.5 SOL",
                    callback_data="buy_0.5"
                ),
                InlineKeyboardButton(
                    text="✓ 1 SOL" if amount == 1.0 else "1 SOL",
                    callback_data="buy_1.0"
                )
            ],
            [InlineKeyboardButton(text="Ввести количество SOL", callback_data="custom_amount")],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Купить", callback_data="confirm_buy")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Выбранная сумма: {amount} SOL\n"
            f"⚙️ Slippage: {slippage}%\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✓' if token_info.is_renounced else '✗'} "
            f"Burnt: {'✓' if token_info.is_burnt else '✗'}\n\n"
            f"🔍 Анализ: [Pump](https://www.pump.fun/{token_address})"
        )
        
        await callback_query.message.edit_text(
            message_text,
            reply_markup=keyboard,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error handling preset amount: {e}")
        await callback_query.answer("❌ Произошла ошибка") 