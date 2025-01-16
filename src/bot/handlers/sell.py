import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from solders.pubkey import Pubkey

from ...services.solana import SolanaService
from ...services.token_info import TokenInfoService
from ...database.models import User, Trade
from .start import get_real_user_id
from ...solana_module.transaction_handler import UserTransactionHandler
from ...solana_module.utils import get_bonding_curve_address, find_associated_bonding_curve
from ..states import SellStates

logger = logging.getLogger(__name__)

router = Router()
token_info_service = TokenInfoService()

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

@router.callback_query(F.data == "sell", flags={"priority": 3})
async def on_sell_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Продать в главном меню"""
    try:
        await state.set_state(SellStates.waiting_for_token)
        await callback_query.message.edit_text(
            "🔍 Введите адрес токена, который хотите продать:\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )   
    except Exception as e:
        logger.error(f"Error in sell button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.message(SellStates.waiting_for_token, flags={"priority": 2})
async def handle_token_input(message: types.Message, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Handle token address input"""
    try:
        token_address = message.text.strip()
        
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный формат адреса токена\n"
                "Пожалуйста, введите корректный адрес:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return
            
        # Get user's token balance
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await message.reply("❌ Пользователь не найден")
            return
            
        try:
            tx_handler = UserTransactionHandler(user.private_key)
            token_balance = await tx_handler.client.get_token_balance(Pubkey.from_string(token_address))
            token_balance_decimal = float(token_balance) if token_balance else 0.0
            
            if token_balance_decimal <= 0:
                await message.reply(
                    "❌ У вас нет этого токена",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                    ])
                )
                return
                
        except Exception as e:
            logger.error(f"Error getting token balance: {e}")
            await message.reply(
                "❌ Ошибка при получении баланса токена",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return
            
        # Get bonding curve addresses
        mint = Pubkey.from_string(token_address)
        bonding_curve, _ = get_bonding_curve_address(mint, tx_handler.client.PUMP_PROGRAM)
        associated_bonding_curve = find_associated_bonding_curve(mint, bonding_curve)
        
        # Save token data to state
        await state.update_data({
            'token_address': token_address,
            'bonding_curve': str(bonding_curve),
            'associated_bonding_curve': str(associated_bonding_curve),
            'token_balance': token_balance_decimal,
            'operation_context': 'sell',  # Set operation context to sell
            'sell_percentage': 100,  # Default to 100%
            'slippage': 1.0  # Default slippage
        })
        
        # Get current slippage from state
        data = await state.get_data()
        slippage = data.get('slippage', 1.0)  # Default to 1% if not set
        
        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await message.reply("❌ Не удалось получить информацию о токене")
            return
            
        # Формируем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Тип ордера
            [
                InlineKeyboardButton(text="🔴 Продать", callback_data="market_sell"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_sell")
            ],
            # Предустановленные проценты
            [
                InlineKeyboardButton(text="Initial", callback_data="sell_initial"),
                InlineKeyboardButton(text="25%", callback_data="sell_25"),
                InlineKeyboardButton(text="50%", callback_data="sell_50")
            ],
            [
                InlineKeyboardButton(text="75%", callback_data="sell_75"),
                InlineKeyboardButton(text="100%", callback_data="sell_100"),
                InlineKeyboardButton(text="Custom", callback_data="custom_amount")
            ],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="sell_set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс: {token_balance_decimal:.6f} токенов\n"
            f"⚙️ Slippage: {slippage}%\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✓' if token_info.is_renounced else '✗'} "
            f"Burnt: {'✓' if token_info.is_burnt else '✗'}\n\n"
            f"🔍 Анализ: [Pump](https://www.pump.fun/{token_address})"
        )
        
        await message.reply(
            message_text,
            reply_markup=keyboard,
            parse_mode="MARKDOWN",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error processing token address: {e}")
        await message.reply(
            "❌ Произошла ошибка при обработке адреса токена",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
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
        
        # Get current token price
        mint = Pubkey.from_string(token_address)
        bonding_curve, _ = get_bonding_curve_address(mint, tx_handler.client.PUMP_PROGRAM)
        curve_state = await tx_handler.client.get_pump_curve_state(bonding_curve)
        current_price_sol = tx_handler.client.calculate_pump_curve_price(curve_state)
        
        # Calculate amount of tokens to sell based on percentage or initial amount
        if sell_percentage == "initial":
            # Find the most recent buy transaction for this token
            buy_tx = await session.scalar(
                select(Trade)
                .where(
                    Trade.user_id == user.id,
                    Trade.token_address == token_address,
                    Trade.is_buy == True,
                    Trade.status == 'completed'
                )
                .order_by(Trade.timestamp.desc())
            )
            
            if not buy_tx:
                logger.warning("No previous buy transaction found for Initial sell")
                await status_message.edit_text(
                    "❌ Не найдена предыдущая транзакция покупки\n"
                    "Пожалуйста, выберите другую сумму для продажи",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_sell")]
                    ])
                )
                return
                
            # Calculate how many tokens we need to sell to get the same amount of SOL
            amount_tokens = (buy_tx.amount_sol / current_price_sol)
            
            # Check if we have enough tokens
            if amount_tokens > token_balance:
                amount_tokens = token_balance  # Sell all available tokens if not enough
                
            logger.info(f"Initial sell: Selling {amount_tokens} tokens to get {buy_tx.amount_sol} SOL")
        else:
            amount_tokens = token_balance * (sell_percentage / 100.0)
            
        logger.info(f"Executing sell transaction for {amount_tokens} tokens ({sell_percentage})")
        
        tx_signature = await tx_handler.sell_token(
            token_address=token_address,
            amount_tokens=amount_tokens,
            slippage=slippage
        )
        
        if tx_signature:
            logger.info(f"Sell transaction successful: {tx_signature}")
            
            # Save transaction to database
            new_trade = Trade(
                user_id=user.id,
                token_address=token_address,
                amount=amount_tokens,
                price=current_price_sol,
                amount_sol=amount_tokens * current_price_sol,
                is_buy=False,
                status='completed',
                transaction_hash=tx_signature
            )
            session.add(new_trade)
            await session.commit()
            
            # Update success message
            sell_type = "Initial" if sell_percentage == "initial" else f"{sell_percentage}%"
            await status_message.edit_text(
                "✅ Токен успешно продан!\n\n"
                f"💰 Продано: {amount_tokens:.6f} токенов ({sell_type})\n"
                f"💵 Цена: {current_price_sol:.6f} SOL\n"
                f"💰 Получено: {(amount_tokens * current_price_sol):.4f} SOL\n"
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

@router.callback_query(lambda c: c.data == "sell_set_slippage", flags={"priority": 20})
async def handle_set_slippage(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle slippage setting button"""
    try:
        # Get current data to verify we're in sell context
        data = await state.get_data()
        
        if not data.get("token_address"):
            await callback_query.answer("❌ Ошибка: не выбран токен")
            return
            
        # Save sell context
        await state.update_data(menu_type="sell")
            
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="0.5%", callback_data="sell_slippage_0.5"),
                InlineKeyboardButton(text="1%", callback_data="sell_slippage_1"),
                InlineKeyboardButton(text="2%", callback_data="sell_slippage_2")
            ],
            [
                InlineKeyboardButton(text="3%", callback_data="sell_slippage_3"),
                InlineKeyboardButton(text="5%", callback_data="sell_slippage_5"),
                InlineKeyboardButton(text="Custom", callback_data="sell_slippage_custom")
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_sell")]
        ])
        
        await callback_query.message.edit_text(
            "⚙️ Настройка Slippage для продажи\n\n"
            "Выберите максимальное проскальзывание цены:\n"
            "• Чем выше slippage, тем больше вероятность успешной транзакции\n"
            "• Чем ниже slippage, тем лучше цена исполнения\n"
            "• Рекомендуемое значение: 1-2%",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error in set_slippage handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.callback_query(lambda c: c.data.startswith("sell_slippage_"), flags={"priority": 20})
async def handle_slippage_choice(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle slippage choice"""
    try:
        # Verify we're in sell context
        data = await state.get_data()
        
        if data.get("menu_type") != "sell":
            return
            
        choice = callback_query.data.split("_")[2]  # sell_slippage_X -> X
        
        if choice == "custom":
            await callback_query.message.edit_text(
                "⚙️ Пользовательский Slippage для продажи\n\n"
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
        await show_sell_menu(callback_query.message, state)
            
    except Exception as e:
        logger.error(f"Error handling slippage choice: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.callback_query(lambda c: c.data == "back_to_sell", flags={"priority": 3})
async def handle_back_to_sell(callback_query: types.CallbackQuery, state: FSMContext):
    """Return to sell menu"""
    logger.info("[SELL] Handling back_to_sell")
    data = await state.get_data()
    logger.info(f"[SELL] Current state data: {data}")
    if data.get("menu_type") != "sell":
        logger.warning(f"[SELL] Wrong menu type: {data.get('menu_type')}")
        return
    await show_sell_menu(callback_query.message, state)
    logger.info("[SELL] Showed sell menu")

@router.callback_query(lambda c: c.data.startswith("sell_"))
async def handle_sell_percentage(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle sell percentage buttons"""
    try:
        # Extract percentage from callback data
        sell_type = callback_query.data.split("_")[1]
        
        if sell_type == "initial":
            # Save special type to state
            await state.update_data(sell_percentage="initial")
            percentage = "initial"
        else:
            # Convert percentage to float and save to state
            percentage = float(sell_type)
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
                    text=f"✓ Initial" if percentage == "initial" else "Initial",
                    callback_data="sell_initial"
                ),
                InlineKeyboardButton(
                    text=f"✓ 25%" if percentage == 25 else "25%",
                    callback_data="sell_25"
                ),
                InlineKeyboardButton(
                    text=f"✓ 50%" if percentage == 50 else "50%",
                    callback_data="sell_50"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"✓ 75%" if percentage == 75 else "75%",
                    callback_data="sell_75"
                ),
                InlineKeyboardButton(
                    text=f"✓ 100%" if percentage == 100 else "100%",
                    callback_data="sell_100"
                ),
                InlineKeyboardButton(text="Custom", callback_data="custom_amount")
            ],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="sell_set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Выбранный тип продажи: {percentage if percentage == 'initial' else str(percentage) + '%'}\n"
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
    """Show sell menu with current token info and settings"""
    try:
        # Get current data
        data = await state.get_data()
        token_address = data.get("token_address")
        token_balance = data.get("token_balance", 0.0)
        sell_percentage = data.get("sell_percentage", 100)
        slippage = data.get("slippage", 1.0)
        
        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await message.edit_text(
                "❌ Не удалось получить информацию о токене",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return
            
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Тип ордера
            [
                InlineKeyboardButton(text="🔴 Продать", callback_data="market_sell"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_sell")
            ],
            # Предустановленные проценты
            [
                InlineKeyboardButton(
                    text=f"✓ Initial" if sell_percentage == "initial" else "Initial",
                    callback_data="sell_initial"
                ),
                InlineKeyboardButton(
                    text=f"✓ 25%" if sell_percentage == 25 else "25%",
                    callback_data="sell_25"
                ),
                InlineKeyboardButton(
                    text=f"✓ 50%" if sell_percentage == 50 else "50%",
                    callback_data="sell_50"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"✓ 75%" if sell_percentage == 75 else "75%",
                    callback_data="sell_75"
                ),
                InlineKeyboardButton(
                    text=f"✓ 100%" if sell_percentage == 100 else "100%",
                    callback_data="sell_100"
                ),
                InlineKeyboardButton(text="Custom", callback_data="custom_amount")
            ],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="sell_set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс: {token_balance:.6f} токенов\n"
            f"⚙️ Slippage: {slippage}%\n\n"
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
        
    except Exception as e:
        logger.error(f"Error showing sell menu: {e}")
        await message.edit_text(
            "❌ Произошла ошибка при отображении меню",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        ) 

@router.message(SellStates.waiting_for_slippage)
async def handle_custom_slippage(message: types.Message, state: FSMContext):
    """Handle custom slippage input"""
    try:
        slippage = float(message.text.replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage value")
            
        await state.update_data(slippage=slippage)
        
        # Отправляем новое сообщение об успешном изменении
        status_message = await message.answer(f"✅ Slippage установлен: {slippage}%")
        
        # Показываем обновленное меню продажи
        await show_sell_menu(status_message, state)
            
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_slippage")]
            ])
        ) 