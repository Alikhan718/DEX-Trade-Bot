import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from solders.pubkey import Pubkey

from ...services.solana import SolanaService
from ...services.token_info import TokenInfoService
from ...database.models import User
from .start import get_real_user_id
from ...solana_module.transaction_handler import UserTransactionHandler
from ...solana_module.utils import get_bonding_curve_address, find_associated_bonding_curve

logger = logging.getLogger(__name__)

router = Router()
token_info_service = TokenInfoService()

class SellStates(StatesGroup):
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

@router.callback_query(lambda c: c.data == "sell")
async def on_sell_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Продать в главном меню"""
    try:
        await callback_query.message.edit_text(
            "🔍 Введите адрес токена, который хотите продать:\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )
        await state.set_state(SellStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Error in sell button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.message(SellStates.waiting_for_token)
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
            
        # Save token address to state
        await state.update_data(token_address=token_address)
        
        # Initialize transaction handler
        tx_handler = UserTransactionHandler(user.private_key)
        
        # Get token balance using transaction handler
        token_pubkey = Pubkey.from_string(token_address)
        bonding_curve, _ = get_bonding_curve_address(token_pubkey, tx_handler.client.PUMP_PROGRAM)
        associated_bonding_curve = find_associated_bonding_curve(token_pubkey, bonding_curve)
        
        # Get token balance from the associated token account
        associated_token_account = await tx_handler.client.create_associated_token_account(token_pubkey)
        token_account_info = await tx_handler.client.client.get_token_account_balance(associated_token_account)
        token_balance_decimal = float(token_account_info.value.amount) / 10**6  # Convert from lamports
        
        # Get SOL equivalent
        curve_state = await tx_handler.client.get_pump_curve_state(bonding_curve)
        token_price_sol = tx_handler.client.calculate_pump_curve_price(curve_state)
        sol_value = token_balance_decimal * token_price_sol
        
        # Get SOL price for USD conversion
        sol_price = await solana_service.get_sol_price()
        usd_value = sol_value * sol_price
        
        # Save curve addresses to state for later use
        await state.update_data({
            'bonding_curve': str(bonding_curve),
            'associated_bonding_curve': str(associated_bonding_curve),
            'token_balance': token_balance_decimal
        })
        
        # Формируем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Тип ордера
            [
                InlineKeyboardButton(text="🔴 Продать", callback_data="market_sell"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_sell")
            ],
            # Предустановленные проценты
            [
                InlineKeyboardButton(text="25%", callback_data="sell_25"),
                InlineKeyboardButton(text="50%", callback_data="sell_50"),
                InlineKeyboardButton(text="100%", callback_data="sell_100")
            ],
            [InlineKeyboardButton(text="Ввести количество токенов", callback_data="custom_amount")],
            # Slippage
            [InlineKeyboardButton(text="⚙️ Slippage: 1%", callback_data="set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        # Формируем сообщение
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс токенов:\n"
            f"• Количество: {token_balance_decimal:.4f} {token_info.symbol}\n"
            f"• Стоимость: {sol_value:.4f} SOL (${usd_value:.2f})\n\n"
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

@router.callback_query(lambda c: c.data == "confirm_sell")
async def handle_confirm_sell(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle sell confirmation"""
    try:
        # Get user data
        user_id = get_real_user_id(callback_query)
        logger.info(f"Processing sell confirmation for user: {user_id}")
        
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
        token_balance = data.get("token_balance", 0.0)  # Get token balance from state
        sell_percentage = data.get("sell_percentage", 100.0)  # Default to 100% if not specified
        slippage = data.get("slippage", 1.0)
        
        if not token_address:
            logger.error("Missing token address")
            await callback_query.answer("❌ Не указан токен")
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
            "🔄 Выполняется продажа токена...\n"
            "Пожалуйста, подождите"
        )
        
        # Calculate amount of tokens to sell based on percentage
        amount_tokens = token_balance * (sell_percentage / 100.0)
        logger.info(f"Executing sell transaction for {amount_tokens} tokens ({sell_percentage}%)")
        
        tx_signature = await tx_handler.sell_token(
            token_address=token_address,
            amount_tokens=amount_tokens,  # Pass exact amount instead of percentage
            slippage=slippage
        )
        
        if tx_signature:
            logger.info(f"Sell transaction successful: {tx_signature}")
            # Update success message
            await status_message.edit_text(
                "✅ Токен успешно продан!\n\n"
                f"💰 Продано: {amount_tokens:.6f} токенов ({sell_percentage}%)\n"
                f"🔗 Транзакция: [Explorer](https://solscan.io/tx/{tx_signature})",
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
        else:
            logger.error("Sell transaction failed: No signature returned")
            # Update error message
            await status_message.edit_text(
                "❌ Ошибка при продаже токена\n"
                "Пожалуйста, попробуйте позже",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )

        # Clear state
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error confirming sell: {e}")
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
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_sell")]
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
        await state.set_state(SellStates.waiting_for_slippage)
        return
        
    # Convert choice to float and save to state
    slippage = float(choice)
    await state.update_data(slippage=slippage)
    
    # Return to sell menu
    await show_sell_menu(callback_query.message, state)

@router.callback_query(lambda c: c.data == "back_to_sell")
async def handle_back_to_sell(callback_query: types.CallbackQuery, state: FSMContext):
    """Return to sell menu"""
    await show_sell_menu(callback_query.message, state)

@router.callback_query(lambda c: c.data.startswith("sell_"))
async def handle_sell_percentage(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle sell percentage buttons"""
    try:
        # Extract percentage from callback data
        percentage = float(callback_query.data.split("_")[1])
        
        # Save percentage to state
        await state.update_data(sell_percentage=percentage)
        
        # Update message with selected percentage
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
                InlineKeyboardButton(text="🔴 Продать", callback_data="market_sell"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_sell")
            ],
            # Предустановленные проценты с отметкой выбранной
            [
                InlineKeyboardButton(
                    text=f"✓ 25%" if percentage == 25 else "25%",
                    callback_data="sell_25"
                ),
                InlineKeyboardButton(
                    text=f"✓ 50%" if percentage == 50 else "50%",
                    callback_data="sell_50"
                ),
                InlineKeyboardButton(
                    text=f"✓ 100%" if percentage == 100 else "100%",
                    callback_data="sell_100"
                )
            ],
            [InlineKeyboardButton(text="Ввести количество токенов", callback_data="custom_amount")],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Выбранный процент: {percentage}%\n"
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
        logger.error(f"Error handling sell percentage: {e}")
        await callback_query.answer("❌ Произошла ошибка")

async def show_sell_menu(message: types.Message, state: FSMContext):
    """Show sell menu with current settings"""
    data = await state.get_data()
    token_address = data.get("token_address")
    slippage = data.get("slippage", 1.0)
    sell_percentage = data.get("sell_percentage", 100.0)
    
    # Get token info again
    token_info = await token_info_service.get_token_info(token_address)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # Тип ордера
        [
            InlineKeyboardButton(text="🔴 Продать", callback_data="market_sell"),
            InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_sell")
        ],
        # Предустановленные проценты
        [
            InlineKeyboardButton(text="25%", callback_data="sell_25"),
            InlineKeyboardButton(text="50%", callback_data="sell_50"),
            InlineKeyboardButton(text="100%", callback_data="sell_100")
        ],
        [InlineKeyboardButton(text="Ввести количество токенов", callback_data="custom_amount")],
        # Slippage
        [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="set_slippage")],
        # Действия
        [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    
    message_text = (
        f"${token_info.symbol} 📈 - {token_info.name}\n\n"
        f"📍 Адрес токена:\n`{token_address}`\n\n"
        f"⚙️ Настройки:\n"
        f"• Процент продажи: {sell_percentage}%\n"
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