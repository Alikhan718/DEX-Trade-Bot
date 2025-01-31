import logging
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from solders.pubkey import Pubkey

from src.services.solana_service import SolanaService
from src.services.token_info import TokenInfoService
from src.database.models import User, Trade, TransactionType
from .buy import _format_price
from .start import get_real_user_id
from src.solana_module.transaction_handler import UserTransactionHandler
from src.solana_module.utils import get_bonding_curve_address, find_associated_bonding_curve
from src.bot.states import SellStates
from src.bot.crud import get_user_setting, update_user_setting
from src.solana_module.solana_client import SolanaClient
from src.solana_module.token_info import token_info
import traceback

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


@router.callback_query(F.data == "sell", flags={"priority": 3})
async def on_sell_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle sell button press"""
    try:
        await state.set_state(SellStates.waiting_for_token)

        # Get user's token balance
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return

        # Create SolanaClient instance
        solana_client = SolanaClient(compute_unit_price=100000)  # Default compute unit price
        
        # Get user's tokens
        tx_handler = UserTransactionHandler(user.private_key, 10000000)
        tokens = await solana_client.get_tokens(user.solana_wallet, tx_handler)
        
        if not tokens:
            await callback_query.message.edit_text(
                "❌ У вас нет токенов для продажи",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return

        # Create keyboard with tokens
        keyboard = []
        for token_address, market_cap, name, symbol, balance in tokens:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"💎 {symbol} ({name} - ${_format_price(balance)})",
                    callback_data=f"select_token_{token_address}"
                )
            ])

        # Add back button
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])

        await callback_query.message.edit_text(
            "🔴 Выберите токен для продажи:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

        await state.set_state(SellStates.waiting_for_token)

    except Exception as e:
        traceback.format_exc()
        logger.error(f"Error in sell button handler: {e}")
        traceback.print_exc()
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при получении списка токенов",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )

# Добавляем новый обработчик для выбора токена из списка
@router.callback_query(lambda c: c.data.startswith("select_token_"), flags={"priority": 3})
async def handle_token_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        token_address = callback_query.data.replace("select_token_", "")
        
        # Store token address in state
        user_id = get_real_user_id(callback_query)
        stmt = await session.execute(select(User).where(User.telegram_id == user_id))
        user = stmt.unique().scalar_one_or_none()
        tx_handler = UserTransactionHandler(user.private_key, 10000000)
        token_balance = await tx_handler.client.get_token_balance(Pubkey.from_string(token_address))

        mint = Pubkey.from_string(token_address)
        bonding_curve, _ = get_bonding_curve_address(mint, tx_handler.client.PUMP_PROGRAM)
        associated_bonding_curve = find_associated_bonding_curve(mint, bonding_curve)

        # Save token data to state
        sell_setting = await get_user_setting(user_id, 'sell', session)
        slippage = sell_setting['slippage']
        gas_fee = sell_setting['gas_fee']
        await state.update_data({
            'token_address': token_address,
            'bonding_curve': str(bonding_curve),
            'associated_bonding_curve': str(associated_bonding_curve),
            'token_balance': token_balance,
            'operation_context': 'sell',  # Set operation context to sell
            'sell_percentage': 100,  # Default to 100%
            'slippage': slippage,  # Default slippage
            'gas_fee': gas_fee
        })

        # Show sell menu for selected token
        await show_sell_menu(callback_query.message, state, session)
        
    except Exception as e:
        logger.error(f"Error handling token selection: {e}")
        traceback.print_exc()
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при выборе токена",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sell")]
            ])
        )


@router.message(F.text.startswith("token_"), flags={"priority": 2})
async def on_token_selected_via_link(message: types.Message, state: FSMContext, session: AsyncSession,
                                     solana_service: SolanaService):
    message.text = message.text.split("_")[1]
    await handle_token_input(message, state, session, solana_service)


@router.message(SellStates.waiting_for_token, flags={"priority": 2})
async def handle_token_input(message: types.Message, state: FSMContext, session: AsyncSession,
                             solana_service: SolanaService):
    """Handle token address input"""
    try:
        token_address = message.text.strip()

        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный формат адреса токена\n"
                "Пожалуйста, введите корректный адрес:",
                reply_markup=ForceReply(selective=True)
            )
            return

        # Get user's token balance
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        sell_setting = await session.execute(stmt)
        user = sell_setting.unique().scalar_one_or_none()

        if not user:
            await message.reply("❌ Пользователь не найден")
            return

        try:
            sell_settings = await get_user_setting(user_id, 'sell', session)
            tx_handler = UserTransactionHandler(user.private_key, sell_settings['gas_fee'])
            token_balance = await tx_handler.client.get_token_balance(Pubkey.from_string(token_address))

            if token_balance <= 0:
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
        sell_setting = await get_user_setting(user_id, 'sell', session)
        slippage = sell_setting['slippage']
        gas_fee = sell_setting['gas_fee']
        await state.update_data({
            'token_address': token_address,
            'bonding_curve': str(bonding_curve),
            'associated_bonding_curve': str(associated_bonding_curve),
            'token_balance': token_balance,
            'operation_context': 'sell',  # Set operation context to sell
            'sell_percentage': 100,  # Default to 100%
            'slippage': slippage,  # Default slippage
            'gas_fee': gas_fee
        })

        sell_percentage = 100
        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await message.reply("❌ Не удалось получить информацию о токене")
            return

        # Формируем клавиатуру
        user_id = get_real_user_id(message)
        stmt = select(User.last_buy_amount).where(User.telegram_id == user_id)
        sell_setting = await session.execute(stmt)
        last_buy_amount = sell_setting.scalar()

        keyboard = get_sell_keyboard_list(slippage, last_buy_amount, sell_percentage, gas_fee)

        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс: {_format_price(token_balance)} токенов (${_format_price(token_balance * token_info.price_usd)})\n"
            f"⚙️ Slippage: {slippage}%\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✅️' if token_info.is_renounced else '✗'} "
            f"Burnt: {'✅️' if token_info.is_burnt else '✗'}\n\n"
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
        user = result.unique().scalar_one_or_none()

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
            sell_settings = await get_user_setting(user_id, 'sell', session)
            tx_handler = UserTransactionHandler(user.private_key, sell_settings['gas_fee'])
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

            if True:
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
            # amount_tokens = (buy_tx.amount_sol / current_price_sol)
            #
            # # Check if we have enough tokens
            # if amount_tokens > token_balance:
            #     amount_tokens = token_balance  # Sell all available tokens if not enough

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

            # Update success message
            sell_type = "Initial" if sell_percentage == "initial" else f"{sell_percentage}%"
            await status_message.edit_text(
                "✅ Токен успешно продан!\n\n"
                f"💰 Продано: {_format_price(amount_tokens)} токенов ({sell_type})\n"
                f"💵 Цена: {(current_price_sol)} SOL\n"
                f"💰 Получено: {_format_price(amount_tokens * current_price_sol)} SOL\n"
                f"🔗 Транзакция: [Explorer](https://solscan.io/tx/{tx_signature})",
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            trade = Trade(
                user_id=user.id,
                token_address=token_address,
                amount=amount_tokens,
                price_usd=current_price_sol,
                amount_sol=amount_tokens * current_price_sol,
                created_at=datetime.now(),
                transaction_type=1,
                status="SUCCESS",
                gas_fee=sell_settings['gas_fee'],
                transaction_hash=tx_signature,
            )
            session.add(trade)
            await session.commit()
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
        chosen_slippage = data.get('slippage')
        # Save sell context
        await state.update_data(menu_type="sell")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{'✅️' if chosen_slippage == 0.05 else ''} 0.5%",
                                     callback_data="sell_slippage_0.5"),
                InlineKeyboardButton(text=f"{'✅️' if chosen_slippage == 1 else ''} 1%",
                                     callback_data="sell_slippage_1"),
                InlineKeyboardButton(text=f"{'✅️' if chosen_slippage == 2 else ''} 2%", callback_data="sell_slippage_2")
            ],
            [
                InlineKeyboardButton(text=f"{'✅️' if chosen_slippage == 3 else ''} 3%",
                                     callback_data="sell_slippage_3"),
                InlineKeyboardButton(text=f"{'✅️' if chosen_slippage == 5 else ''} 5%",
                                     callback_data="sell_slippage_5"),
                InlineKeyboardButton(
                    text=f"{'✅️ ' + str(_format_price(chosen_slippage)) if chosen_slippage and chosen_slippage not in [0.05, 1, 2, 3, 5] else ''} Custom",
                    callback_data="sell_slippage_custom")
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
async def handle_slippage_choice(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle slippage choice"""
    try:
        # Verify we're in sell context
        data = await state.get_data()

        if data.get("menu_type") != "sell":
            return

        choice = callback_query.data.split("_")[2]  # sell_slippage_X -> X

        if choice == "custom":
            await callback_query.message.answer(
                "⚙️ Пользовательский Slippage для продажи\n\n"
                "Введите значение в процентах (например, 1.5):",
                reply_markup=ForceReply(selective=True)
            )
            await state.set_state(SellStates.waiting_for_slippage)
            return

        # Convert choice to float and save to state
        slippage = float(choice)
        user_id = get_real_user_id(callback_query)

        sell_setting = await get_user_setting(user_id, 'sell', session)
        sell_setting['slippage'] = slippage
        await update_user_setting(user_id, 'sell', sell_setting, session)
        await state.update_data(slippage=slippage)
        await show_sell_menu(callback_query.message, state, session)

    except Exception as e:
        logger.error(f"Error handling slippage choice: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.callback_query(lambda c: c.data == "back_to_sell", flags={"priority": 3})
async def handle_back_to_sell(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Return to sell menu"""
    logger.info("[SELL] Handling back_to_sell")
    data = await state.get_data()
    logger.info(f"[SELL] Current state data: {data}")
    await show_sell_menu(callback_query.message, state, session)
    logger.info("[SELL] Showed sell menu")


@router.callback_query(lambda c: c.data.startswith("sell_"), flags={"priority": 8})
async def handle_sell_percentage(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle sell percentage buttons"""
    try:
        # Extract percentage from callback data
        sell_type = callback_query.data.split("_", 1)[1]

        if sell_type == "initial":
            # Save special type to state
            await state.update_data(sell_percentage="initial")
            percentage = "initial"
        elif sell_type == "set_gas_fee":
            await callback_query.message.answer(
                "⚙️ Пользовательский Gas Fee для покупки\n\n"
                "Введите значение в SOL (например, 0.01):",
                reply_markup=ForceReply(selective=True)  # Указываем, что требуется ответ
            )
            # Устанавливаем состояние для ожидания ввода пользователя
            await state.set_state(SellStates.waiting_for_gas_fee)
            return
        elif sell_type == "custom":
            await callback_query.message.answer(
                "⚙️ Процент для продажи\n\n"
                "Введите значение в процентах (например, 50%):",
                reply_markup=ForceReply(selective=True)
            )
            await state.set_state(SellStates.waiting_for_percentage)
            return
        else:
            # Convert percentage to float and save to state
            percentage = float(sell_type)
        await state.update_data(sell_percentage=percentage)

        # Update message with selected percentage
        data = await state.get_data()
        token_address = data.get("token_address")
        slippage = data.get("slippage")
        gas_fee = data.get('gas_fee')

        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await callback_query.answer("❌ Не удалось получить информацию о токене")
            return

        user_id = get_real_user_id(callback_query)
        stmt = select(User.last_buy_amount).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        last_buy_amount = result.scalar()
        keyboard = get_sell_keyboard_list(slippage, last_buy_amount, percentage, gas_fee)

        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Выбранный тип продажи: {percentage if percentage == 'initial' else str(percentage) + '%'}\n"
            f"⚙️ Slippage: {slippage}%\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✅️' if token_info.is_renounced else '✗'} "
            f"Burnt: {'✅️' if token_info.is_burnt else '✗'}\n\n"
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


async def show_sell_menu(message: types.Message, state: FSMContext, session: AsyncSession):
    """Show sell menu with current token info and settings"""
    try:
        # Get current data

        data = await state.get_data()
        token_address = data.get("token_address")
        token_balance = data.get("token_balance", 0.0)
        sell_percentage = data.get("sell_percentage", 100)
        slippage = data.get("slippage", 1.0)
        gas_fee = data.get("gas_fee")

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
        user_id = get_real_user_id(message)
        stmt = select(User.last_buy_amount).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        last_buy_amount = result.scalar()

        keyboard = get_sell_keyboard_list(slippage, last_buy_amount, sell_percentage, gas_fee)

        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс: {_format_price(token_balance)} токенов (${_format_price(token_balance * token_info.price_usd)})\n"
            f"⚙️ Slippage: {slippage}%\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✅️' if token_info.is_renounced else '✗'} "
            f"Burnt: {'✅️' if token_info.is_burnt else '✗'}\n\n"
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
        traceback.print_exc()
        await message.edit_text(
            "❌ Произошла ошибка при отображении меню",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )


@router.message(SellStates.waiting_for_slippage)
async def handle_custom_slippage(message: types.Message, state: FSMContext, session: AsyncSession):
    """Handle custom slippage input"""
    try:
        slippage = float(message.text.replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage value")
        user_id = get_real_user_id(message)
        sell_setting = await get_user_setting(user_id, 'sell', session)
        sell_setting['slippage'] = slippage
        await update_user_setting(user_id, 'sell', sell_setting, session)
        await state.update_data(slippage=slippage)

        # Отправляем новое сообщение об успешном изменении
        status_message = await message.answer(f"✅ Slippage установлен: {slippage}%")

        # Показываем обновленное меню продажи
        await show_sell_menu(status_message, state, session)

    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100:",
            reply_markup=ForceReply(selective=True)
        )


@router.message(SellStates.waiting_for_percentage)
async def handle_custom_percentage(message: types.Message, state: FSMContext, session: AsyncSession):
    """Handle custom percentage input"""
    try:
        sell_percentage = float(message.text.replace("%", ""))
        if sell_percentage < 1 or sell_percentage > 100:
            raise ValueError("Invalid percentage value")
        await state.update_data(sell_percentage=sell_percentage)

        # Отправляем новое сообщение об успешном изменении
        status_message = await message.answer(f"✅ Процент установлен: {sell_percentage}%")

        # Показываем обновленное меню продажи
        await show_sell_menu(status_message, state, session)

    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 1 до 100:",
            reply_markup=ForceReply(selective=True)
        )


def get_sell_keyboard_list(
        slippage: float,
        last_buy_amount: float,
        sell_percentage: float | str,
        gas_fee: float
):
    first_row = [[
        InlineKeyboardButton(text="🔴 Продать", callback_data="market_sell"),
        InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_sell")
    ]]
    last_row = [
        [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="sell_set_slippage")],
        [InlineKeyboardButton(text="💰 Продать", callback_data="confirm_sell")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ]

    values = [
        25,
        50,
        75,
        100,
    ]
    buttons = []
    row = []
    chosen = False
    for i in range(len(values)):
        val = values[i]
        if i % 3 == 0:
            buttons.append(row)
            row = []
        if sell_percentage == val:
            chosen = True
        row.append(
            InlineKeyboardButton(
                text=f"✅️ {val}%" if sell_percentage == val else f"{val}%",
                callback_data=f"sell_{val}"
            )
        )
    if row:
        buttons.append(row)
    if sell_percentage == 'initial':
        chosen = True
    buttons[-1].append(InlineKeyboardButton(
        text=f"{'' if chosen else '✅️ ' + str(sell_percentage) + '%'} Custom",
        callback_data="sell_custom"))
    if last_buy_amount:
        buttons.append([InlineKeyboardButton(
            text=f"Initial {'✅️' if sell_percentage == 'initial' else ''} {last_buy_amount} SOL ",
            callback_data=f"sell_initial"
        )])
    buttons.append([InlineKeyboardButton(
        text=f"🚀 Gas Fee {': ' + _format_price(gas_fee / 1e9) + ' SOL' if gas_fee else ''}",
        callback_data=f"sell_set_gas_fee")])
    return InlineKeyboardMarkup(inline_keyboard=first_row + buttons + last_row)

@router.message(SellStates.waiting_for_gas_fee)
async def handle_custom_gas_fee(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle custom slippage input"""
    try:
        gas_fee = float(callback_query.text.replace(",", "."))
        if gas_fee <= 0 or gas_fee > 10:
            raise ValueError("Invalid gas_fee value")
        gas_fee *= 1e9
        user_id = get_real_user_id(callback_query)

        sell_setting = await get_user_setting(user_id, 'sell', session)
        sell_setting['gas_fee'] = gas_fee
        await update_user_setting(user_id, 'sell', sell_setting, session)
        await state.update_data(gas_fee=gas_fee)

        # Отправляем новое сообщение об успешном изменении
        status_message = await callback_query.answer(f"✅ Gas Fee установлен: {_format_price(gas_fee / 1e9)} SOL")

        # Показываем обновленное меню покупки
        await show_sell_menu(status_message, state, session)

    except ValueError as e:
        logger.error(f"[BUY] Invalid gas_fee value: {e}")
        await callback_query.reply(
            "❌ Неверное значение. Введите число от 0 до 10:",
            reply_markup=ForceReply(selective=True)
        )
