import logging
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re
from typing import Union

from src.services.solana_service import SolanaService
from src.services.token_info import TokenInfoService
from src.database.models import User
from .start import get_real_user_id
from src.solana_module.transaction_handler import UserTransactionHandler
from src.bot.states import BuyStates, AutoBuySettingsStates, LimitBuyStates
from solders.pubkey import Pubkey
from src.solana_module.utils import get_bonding_curve_address
from ..crud import get_user_setting, update_user_setting

logger = logging.getLogger(__name__)

router = Router()
token_info_service = TokenInfoService()

# Регулярное выражение для определения mint адреса
MINT_ADDRESS_PATTERN = r'^[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{44}$'


def _is_valid_token_address(address: str) -> bool:
    """Проверяет валидность адреса токена"""
    try:
        return bool(re.match(MINT_ADDRESS_PATTERN, address))
    except Exception:
        return False


def _format_price(amount: float, format_length=2) -> str:
    """Форматирует цену в читаемый вид"""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.{format_length}f}M"
    elif amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    else:
        return f"{amount:.{format_length}f}"


@router.callback_query(F.data == "buy", flags={"priority": 3})
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


@router.message(BuyStates.waiting_for_token, flags={"priority": 3})
async def handle_token_input(message: types.Message, state: FSMContext, session: AsyncSession,
                             solana_service: SolanaService):
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
        user = result.unique().scalar_one_or_none()

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

        # Save token address and initial slippage to state
        await state.update_data({
            'token_address': token_address,
            'slippage': 1.0  # Default slippage
        })

        # Get current slippage from state
        data = await state.get_data()
        slippage = data.get('slippage', 1.0)  # Default to 1% if not set

        # Формируем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Тип ордера
            [
                InlineKeyboardButton(text="🟢 Купить", callback_data="market_buy"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_buy")
            ],
            # Предустановленные суммы
            [
                InlineKeyboardButton(text="0.002 SOL", callback_data="buy_0.002"),
                InlineKeyboardButton(text="0.005 SOL", callback_data="buy_0.005"),
                InlineKeyboardButton(text="0.01 SOL", callback_data="buy_0.01")
            ],
            [
                InlineKeyboardButton(text="0.02 SOL", callback_data="buy_0.02"),
                InlineKeyboardButton(text="0.1 SOL", callback_data="buy_0.1"),
                InlineKeyboardButton(text="Custom", callback_data="custom_amount")
            ],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="buy_set_slippage")],
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


@router.callback_query(lambda c: c.data == "confirm_buy", flags={"priority": 3})
async def handle_confirm_buy(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle buy confirmation"""
    try:
        # Get user data
        user_id = get_real_user_id(callback_query)
        logger.info(f"Processing buy confirmation for user: {user_id}")

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
        amount_sol = data.get("amount_sol", 0.0)
        slippage = data.get("slippage", 1.0)

        logger.info(f"Buy parameters - Token: {token_address}, Amount: {amount_sol} SOL, Slippage: {slippage}%")

        if not token_address or not amount_sol:
            logger.error("Missing token address or amount")
            await callback_query.answer("❌ Не указан токен или сумма")
            return

        # Initialize transaction handler with user's private key
        try:
            buy_settings = await get_user_setting(user_id, 'buy', session)
            logger.info("Initializing transaction handler")
            tx_handler = UserTransactionHandler(user.private_key, buy_settings['gas_fee'])
        except ValueError:
            logger.error("Failed to initialize transaction handler")
            await callback_query.answer("❌ Ошибка инициализации кошелька")
            return

        # Send status message
        status_message = await callback_query.message.answer(
            "🔄 Выполняется покупка токена...\n"
            "Пожалуйста, подождите"
        )

        # Get token price before transaction
        mint = Pubkey.from_string(token_address)
        bonding_curve, _ = get_bonding_curve_address(mint, tx_handler.client.PUMP_PROGRAM)
        curve_state = await tx_handler.client.get_pump_curve_state(bonding_curve)
        token_price_sol = tx_handler.client.calculate_pump_curve_price(curve_state)

        # Execute buy transaction
        logger.info("Executing buy transaction")
        tx_signature = await tx_handler.buy_token(
            token_address=token_address,
            amount_sol=amount_sol,
            slippage=slippage
        )

        if tx_signature:
            logger.info(f"Buy transaction successful: {tx_signature}")

            # Calculate token amount from SOL amount and price
            token_amount = amount_sol / token_price_sol

            # Update success message
            await status_message.edit_text(
                "✅ Токен успешно куплен!\n\n"
                f"💰 Потрачено: {amount_sol} SOL\n"
                f"📈 Получено: {token_amount:.6f} токенов\n"
                f"💵 Цена: {token_price_sol:.6f} SOL\n"
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


@router.callback_query(lambda c: c.data == "buy_set_slippage", flags={"priority": 10})
async def handle_set_slippage(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle slippage setting button"""
    try:
        # Get current data to verify we're in buy context
        data = await state.get_data()

        if not data.get("token_address"):
            await callback_query.answer("❌ Ошибка: не выбран токен")
            return

        # Save buy context
        await state.update_data(menu_type="buy")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="0.5%", callback_data="buy_slippage_0.5"),
                InlineKeyboardButton(text="1%", callback_data="buy_slippage_1"),
                InlineKeyboardButton(text="2%", callback_data="buy_slippage_2")
            ],
            [
                InlineKeyboardButton(text="3%", callback_data="buy_slippage_3"),
                InlineKeyboardButton(text="5%", callback_data="buy_slippage_5"),
                InlineKeyboardButton(text="Custom", callback_data="buy_slippage_custom")
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_buy")]
        ])

        await callback_query.message.edit_text(
            "⚙️ Настройка Slippage для покупки\n\n"
            "Выберите максимальное проскальзывание цены:\n"
            "• Чем выше slippage, тем больше вероятность успешной транзакции\n"
            "• Чем ниже slippage, тем лучше цена исполнения\n"
            "• Рекомендуемое значение: 1-2%",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error in set_slippage handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.callback_query(lambda c: c.data.startswith("buy_slippage_"), flags={"priority": 10})
async def handle_slippage_choice(callback_query: types.CallbackQuery, state: FSMContext):
    """Handle slippage choice"""
    try:
        # Verify we're in buy context
        data = await state.get_data()

        if data.get("menu_type") != "buy":
            return

        choice = callback_query.data.split("_")[2]  # buy_slippage_X -> X

        if choice == "custom":
            await callback_query.message.edit_text(
                "⚙️ Пользовательский Slippage для покупки\n\n"
                "Введите значение в процентах (например, 1.5):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_slippage_buy")]
                ])
            )
            await state.set_state(BuyStates.waiting_for_slippage)
            return

        # Convert choice to float and save to state
        slippage = float(choice)
        await state.update_data(slippage=slippage)
        await show_buy_menu(callback_query.message, state)

    except Exception as e:
        logger.error(f"Error handling slippage choice: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(BuyStates.waiting_for_slippage)
async def handle_custom_slippage(message: types.Message, state: FSMContext):
    """Handle custom slippage input"""
    try:
        slippage = float(message.text.replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage value")

        await state.update_data(slippage=slippage)

        # Отправляем новое сообщение об успешном изменении
        status_message = await message.answer(f"✅ Slippage установлен: {slippage}%")

        # Показываем обновленное меню покупки
        await show_buy_menu(status_message, state)

    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_slippage_buy")]
            ])
        )


@router.callback_query(lambda c: c.data == "back_to_buy", flags={"priority": 10})
async def handle_back_to_buy(callback_query: types.CallbackQuery, state: FSMContext):
    """Return to buy menu"""
    logger.info("[BUY] Handling back_to_buy")
    data = await state.get_data()
    logger.info(f"[BUY] Current state data: {data}")
    if data.get("menu_type") != "buy":
        logger.warning(f"[BUY] Wrong menu type: {data.get('menu_type')}")
        return
    await show_buy_menu(callback_query.message, state)
    logger.info("[BUY] Showed buy menu")


async def show_buy_menu(message: types.Message, state: FSMContext):
    """Show buy menu with current token info and settings"""
    try:
        # Get current data
        data = await state.get_data()
        token_address = data.get("token_address")
        amount_sol = data.get("amount_sol", 0.0)
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
                InlineKeyboardButton(text="🟢 Купить", callback_data="market_buy"),
                InlineKeyboardButton(text="📊 Лимитный", callback_data="limit_buy")
            ],
            # Предустановленные суммы
            [
                InlineKeyboardButton(text="0.002 SOL", callback_data="buy_0.002"),
                InlineKeyboardButton(text="0.005 SOL", callback_data="buy_0.005"),
                InlineKeyboardButton(text="0.01 SOL", callback_data="buy_0.01")
            ],
            [
                InlineKeyboardButton(text="0.02 SOL", callback_data="buy_0.02"),
                InlineKeyboardButton(text="0.1 SOL", callback_data="buy_0.1"),
                InlineKeyboardButton(text="Custom", callback_data="custom_amount")
            ],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="buy_set_slippage")],
            # Действия
            [InlineKeyboardButton(text="💰 Купить", callback_data="confirm_buy")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])

        message_text = (
            f"${token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Выбранная сумма: {amount_sol} SOL\n"
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
        logger.error(f"Error showing buy menu: {e}")
        await message.edit_text(
            "❌ Произошла ошибка при отображении меню",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
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
                    text="✓ 0.002 SOL" if amount == 0.002 else "0.002 SOL",
                    callback_data="buy_0.002"
                ),
                InlineKeyboardButton(
                    text="✓ 0.005 SOL" if amount == 0.005 else "0.005 SOL",
                    callback_data="buy_0.005"
                ),
                InlineKeyboardButton(
                    text="✓ 0.01 SOL" if amount == 0.01 else "0.01 SOL",
                    callback_data="buy_0.01"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✓ 0.02 SOL" if amount == 0.02 else "0.02 SOL",
                    callback_data="buy_0.02"
                ),
                InlineKeyboardButton(
                    text="✓ 0.1 SOL" if amount == 0.1 else "0.1 SOL",
                    callback_data="buy_0.1"
                ),
                InlineKeyboardButton(text="Custom", callback_data="custom_amount")
            ],
            # Slippage
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="buy_set_slippage")],
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


@router.callback_query(lambda c: c.data == "limit_buy", flags={"priority": 3})
async def on_limit_buy_button(callback_query: types.CallbackQuery, state: FSMContext):
    """
    При нажатии "Лимитный" - показываем меню лимитного ордера.
    """
    try:
        # Очищаем/обнуляем данные лимитной покупки (опционально)
        await state.update_data({
            "trigger_price": 0.0,
            "limit_amount_sol": 0.0,
            "limit_slippage": 1.0,
            "gas_fee": 50000,
            "menu_type": 'buy',
            "action_type": 'buy'
        })
        # Переходим в "idle" состояние лимитной покупки (либо можно не ставить)
        await state.set_state(LimitBuyStates.idle)

        await show_limit_buy_menu(callback_query.message, state, edit=True)
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Error on_limit_buy_button: {e}")
        await callback_query.answer("❌ Произошла ошибка")


async def show_limit_buy_menu(
    message: types.Message,
    state: FSMContext,
    edit: bool = False
):
    """
    Отображает меню лимитной покупки (порог цены, сумма SOL, slippage, подтверждение).
    Параметр edit=True означает, что мы редактируем существующее сообщение,
    иначе отправляем новое.
    """
    data = await state.get_data()
    trigger_price = data.get("trigger_price", 0.0)
    limit_amount_sol = data.get("limit_amount_sol", 0.0)
    limit_slippage = data.get("limit_slippage", 1.0)

    # Формируем клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"⚙️ Порог цены: {trigger_price or 0} USD",
                callback_data="limit_buy_set_trigger_price"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💰 Сумма: {limit_amount_sol or 0} SOL",
                callback_data="limit_buy_set_amount_sol"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🛠 Slippage: {limit_slippage}%",
                callback_data="limit_buy_set_slippage"
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Установить ордер",
                callback_data="limit_buy_confirm"
            )
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_buy")
        ]
    ])

    text = (
        "📊 *Настройки лимитного ордера*\n\n"
        f"• Порог цены (USD): `{trigger_price}`\n"
        f"• Сумма (SOL): `{limit_amount_sol}`\n"
        f"• Slippage: `{limit_slippage}%`\n\n"
        "Когда цена токена *достигнет* (или *опустится* ниже) указанного порога,\n"
        "бот автоматически совершит покупку на указанную сумму."
    )

    if edit:
        await message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )


@router.callback_query(lambda c: c.data == "limit_buy_set_trigger_price", flags={"priority": 3})
async def on_limit_buy_set_trigger_price(callback_query: types.CallbackQuery, state: FSMContext):
    """
    Переходим к вводу пороговой цены (USD).
    """
    try:
        await callback_query.message.edit_text(
            "✏️ Введите лимитную цену в USD, при достижении которой нужно купить.\n\n"
            "Например: `0.00075`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="limit_buy_back_to_menu")]
            ])
        )
        await state.set_state(LimitBuyStates.set_trigger_price)
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Error on_limit_buy_set_trigger_price: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(LimitBuyStates.set_trigger_price)
async def on_limit_buy_trigger_price_input(message: types.Message, state: FSMContext):
    """
    Обрабатываем введённую пользователем лимитную цену (USD).
    """
    try:
        text = message.text.strip().replace(",", ".")
        price = float(text)
        if price <= 0:
            raise ValueError("Price must be > 0")

        # Сохраняем в FSM
        await state.update_data(trigger_price=price)

        # Возвращаемся в меню лимитного ордера
        await show_limit_buy_menu(message, state, edit=False)
        # Сбрасываем состояние обратно в idle (или убираем вообще)
        await state.set_state(LimitBuyStates.idle)

    except ValueError:
        await message.reply("❌ Некорректное значение цены. Введите число больше 0.")
    except Exception as e:
        logger.error(f"Error on_limit_buy_trigger_price_input: {e}")
        await message.reply("❌ Произошла ошибка при обработке цены.")
        await state.clear()


@router.callback_query(lambda c: c.data == "limit_buy_set_amount_sol", flags={"priority": 3})
async def on_limit_buy_set_amount_sol(callback_query: types.CallbackQuery, state: FSMContext):
    """
    Переходим к вводу суммы в SOL для лимитного ордера.
    """
    try:
        await callback_query.message.edit_text(
            "✏️ Введите, сколько SOL хотите потратить.\n\n"
            "Например: `0.1`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="limit_buy_back_to_menu")]
            ])
        )
        await state.set_state(LimitBuyStates.set_amount_sol)
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Error on_limit_buy_set_amount_sol: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(LimitBuyStates.set_amount_sol)
async def on_limit_buy_amount_sol_input(message: types.Message, state: FSMContext):
    """
    Обрабатываем введённую сумму SOL.
    """
    try:
        text = message.text.strip().replace(",", ".")
        amount = float(text)
        if amount <= 0:
            raise ValueError("Amount must be > 0")

        # Сохраняем в FSM
        await state.update_data(limit_amount_sol=amount)

        # Возвращаемся в меню
        await show_limit_buy_menu(message, state, edit=False)
        await state.set_state(LimitBuyStates.idle)

    except ValueError:
        await message.reply("❌ Некорректное значение суммы. Введите число > 0.")
    except Exception as e:
        logger.error(f"Error on_limit_buy_amount_sol_input: {e}")
        await message.reply("❌ Произошла ошибка")
        await state.clear()


@router.callback_query(lambda c: c.data == "limit_buy_set_slippage", flags={"priority": 3})
async def on_limit_buy_set_slippage(callback_query: types.CallbackQuery, state: FSMContext):
    """
    Аналогично: просим пользователя ввести slippage (0-100).
    """
    try:
        await callback_query.message.edit_text(
            "✏️ Введите slippage (в процентах), например `1.5`.\n"
            "Диапазон рекомендуемый: 0.1 - 5%",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="limit_buy_back_to_menu")]
            ])
        )
        await state.set_state(LimitBuyStates.set_slippage)
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Error on_limit_buy_set_slippage: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(LimitBuyStates.set_slippage)
async def on_limit_buy_slippage_input(message: types.Message, state: FSMContext):
    """
    Сохраняем slippage в FSM.
    """
    try:
        text = message.text.strip().replace(",", ".")
        slippage = float(text)
        if slippage <= 0 or slippage > 100:
            raise ValueError("Slippage out of range")

        await state.update_data(limit_slippage=slippage)

        await show_limit_buy_menu(message, state, edit=False)
        await state.set_state(LimitBuyStates.idle)

    except ValueError:
        await message.reply("❌ Некорректное значение slippage. Введите число от 0 до 100.")
    except Exception as e:
        logger.error(f"Error on_limit_buy_slippage_input: {e}")
        await message.reply("❌ Произошла ошибка")
        await state.clear()
    

@router.callback_query(lambda c: c.data == "limit_buy_confirm", flags={"priority": 3})
async def on_limit_buy_confirm(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Пользователь подтверждает установку лимитного ордера.
    Сохраняем в БД / user_setting и информируем его.
    """
    try:
        user_id = get_real_user_id(callback_query)

        # Получаем данные из FSM
        data = await state.get_data()
        trigger_price = data.get("trigger_price")
        limit_amount_sol = data.get("limit_amount_sol")
        limit_slippage = data.get("limit_slippage")
        token_address = data.get("token_address")  # Если у вас уже где-то хранится

        # Если чего-то нет - выходим
        if not trigger_price or not limit_amount_sol:
            await callback_query.answer("❌ Не указаны все параметры ордера")
            return

        # Сохраняем «лимитный ордер» в БД или в user_setting
        # Пример через update_user_setting:
        limit_buy_settings = {
            "token_address": token_address,
            "trigger_price_usd": trigger_price,
            "amount_sol": limit_amount_sol,
            "gas_fee": 50000,
            "slippage": limit_slippage,
            "enabled": True,
        }
        await update_user_setting(user_id, "limit_buy", limit_buy_settings, session)

        await callback_query.message.edit_text(
            f"✅ Лимитный ордер создан.\n\n"
            f"• Цена (USD): {trigger_price}\n"
            f"• Сумма (SOL): {limit_amount_sol}\n"
            f"• Slippage: {limit_slippage}%",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )

        await state.clear()
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Error on_limit_buy_confirm: {e}")
        await callback_query.answer("❌ Ошибка при сохранении ордера")
        await state.clear()




@router.callback_query(F.data == "auto_buy_settings", flags={"priority": 3})
async def show_auto_buy_settings(update: Union[types.Message, types.CallbackQuery], session: AsyncSession):
    """Показать настройки автобая"""
    try:
        # Определяем тип объекта и получаем нужные атрибуты
        if isinstance(update, types.Message):
            user_id = update.from_user.id
            message = update
        else:  # CallbackQuery
            user_id = update.from_user.id
            message = update.message

        # Получаем пользователя и его настройки
        user = await session.scalar(
            select(User).where(User.telegram_id == user_id)
        )

        if not user:
            if isinstance(update, types.CallbackQuery):
                await update.answer("❌ Пользователь не найден")
            else:
                await update.reply("❌ Пользователь не найден")
            return

        settings = await get_user_setting(user_id, 'auto_buy', session)

        # settings = await session.scalar(
        #     select(AutoBuySettings).where(AutoBuySettings.user_id == user.id)
        # )

        # if not settings:
        #     Создаем настройки по умолчанию
        # settings = AutoBuySettings(user_id=user.id)
        # session.add(settings)
        # await session.commit()

        # Формируем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'🟢' if settings['enabled'] else '🔴'} Автобай",
                callback_data="toggle_auto_buy"
            )],
            [InlineKeyboardButton(
                text=f"💰 Сумма: {settings['amount_sol']} SOL",
                callback_data="set_auto_buy_amount"
            )],
            [InlineKeyboardButton(
                text=f"⚙️ Slippage: {settings['slippage']}%",
                callback_data="set_auto_buy_slippage"
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])

        text = (
            "⚡️ Настройки Автобая\n\n"
            f"Статус: {'Включен' if settings['enabled'] else 'Выключен'}\n"
            f"Сумма покупки: {settings['amount_sol']} SOL\n"
            f"Slippage: {settings['slippage']}%\n"
        )

        # Отправляем или редактируем сообщение в зависимости от типа объекта
        if isinstance(update, types.Message):
            await message.answer(text, reply_markup=keyboard)
        else:  # CallbackQuery
            await message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error showing auto-buy settings: {e}")
        if isinstance(update, types.CallbackQuery):
            await update.answer("❌ Произошла ошибка")
        else:
            await update.reply("❌ Произошла ошибка")


@router.callback_query(F.data == "toggle_auto_buy", flags={"priority": 3})
async def toggle_auto_buy(callback: types.CallbackQuery, session: AsyncSession):
    """Включить/выключить автобай"""
    try:
        user_id = get_real_user_id(callback)
        settings = await get_user_setting(user_id, 'auto_buy', session)
        settings['enabled'] = not settings['enabled']
        await update_user_setting(user_id, 'auto_buy', settings, session)
        await show_auto_buy_settings(callback, session)

    except Exception as e:
        logger.error(f"Error toggling auto-buy: {e}")
        await callback.answer("❌ Произошла ошибка")


@router.callback_query(F.data == "set_auto_buy_amount", flags={"priority": 3})
async def handle_set_auto_buy_amount(callback: types.CallbackQuery, state: FSMContext):
    """Установка суммы для автобая"""
    try:
        await callback.message.edit_text(
            "💰 Введите сумму для автопокупки в SOL\n"
            "Например: 0.1",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="auto_buy_settings")]
            ])
        )
        await state.set_state(AutoBuySettingsStates.ENTER_AMOUNT)
    except Exception as e:
        logger.error(f"Error in set auto-buy amount handler: {e}")
        await callback.answer("❌ Произошла ошибка")


@router.message(AutoBuySettingsStates.ENTER_AMOUNT, flags={"priority": 3})
async def handle_auto_buy_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода суммы для автобая"""
    try:
        # Проверяем введенное значение
        try:
            amount = float(message.text.strip())
            if amount <= 0:
                raise ValueError("Amount must be positive")
        except ValueError:
            await message.reply(
                "❌ Неверный формат суммы\n"
                "Пожалуйста, введите положительное число",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="auto_buy_settings")]
                ])
            )
            return
        user_id = get_real_user_id(message)
        settings = await get_user_setting(user_id, 'auto_buy', session)
        settings['amount_sol'] = amount
        await update_user_setting(user_id, 'auto_buy', settings, session)

        # Очищаем состояние и показываем обновленные настройки
        await state.clear()
        await message.answer(
            f"✅ Сумма автопокупки установлена: {amount} SOL"
        )
        # Используем существующую функцию для показа настроек
        await show_auto_buy_settings(message, session)

    except Exception as e:
        logger.error(f"Error processing auto-buy amount input: {e}")
        await message.reply("❌ Произошла ошибка")
        await state.clear()


@router.callback_query(F.data == "set_auto_buy_slippage", flags={"priority": 3})
async def handle_set_auto_buy_slippage(callback: types.CallbackQuery, state: FSMContext):
    """Установка slippage для автобая"""
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="0.5%", callback_data="auto_buy_slippage_0.5"),
                InlineKeyboardButton(text="1%", callback_data="auto_buy_slippage_1"),
                InlineKeyboardButton(text="2%", callback_data="auto_buy_slippage_2")
            ],
            [InlineKeyboardButton(text="Ввести вручную", callback_data="auto_buy_slippage_custom")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="auto_buy_settings")]
        ])

        await callback.message.edit_text(
            "⚙️ Выберите slippage для автопокупки:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error in set auto-buy slippage handler: {e}")
        await callback.answer("❌ Произошла ошибка")


@router.callback_query(lambda c: c.data.startswith("auto_buy_slippage_"), flags={"priority": 3})
async def handle_auto_buy_slippage_choice(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обработка выбора slippage для автобая"""
    try:
        choice = callback.data.split("_")[3]  # auto_buy_slippage_X -> X
        print("\n\nCHOICE", choice, "\n\n")
        if choice == "custom":
            await callback.message.edit_text(
                "⚙️ Введите значение slippage (в процентах)\n"
                "Например: 1.5",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_auto_buy_slippage")]
                ])
            )
            await state.set_state(AutoBuySettingsStates.ENTER_SLIPPAGE)
            return

        # Если выбрано предустановленное значение
        slippage = float(choice)
        print(slippage)
        user_id = get_real_user_id(callback)
        settings = await get_user_setting(user_id, 'auto_buy', session)
        settings['slippage'] = slippage
        await update_user_setting(user_id, 'auto_buy', settings, session)
        await show_auto_buy_settings(callback, session)

    except Exception as e:
        logger.error(f"Error processing auto-buy slippage choice: {e}")
        await callback.answer("❌ Произошла ошибка")


@router.message(AutoBuySettingsStates.ENTER_SLIPPAGE, flags={"priority": 3})
async def handle_auto_buy_slippage_input(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода slippage для автобая"""
    try:
        # Проверяем введенное значение
        try:
            slippage = float(message.text.strip())
            if slippage <= 0 or slippage > 100:
                raise ValueError("Slippage must be between 0 and 100")
        except ValueError:
            await message.reply(
                "❌ Неверный формат slippage\n"
                "Пожалуйста, введите число от 0 до 100",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="set_auto_buy_slippage")]
                ])
            )
            return
        slippage = float(slippage)
        user_id = get_real_user_id(message)
        settings = await get_user_setting(user_id, 'auto_buy', session)
        settings['slippage'] = slippage
        await update_user_setting(user_id, 'auto_buy', settings, session)
        await state.clear()
        await message.answer(
            f"✅ Slippage установлен: {slippage}%"
        )
        # Используем существующую функцию для показа настроек
        await show_auto_buy_settings(message, session)

    except Exception as e:
        logger.error(f"Error processing auto-buy slippage input: {e}")
        await message.reply("❌ Произошла ошибка")
        await state.clear()


@router.message(flags={"allow_next": True})
async def handle_auto_buy(message: types.Message, state: FSMContext, session: AsyncSession,
                          solana_service: SolanaService):
    """Автоматическая покупка при получении mint адреса"""
    try:
        user_id = get_real_user_id(message)
        auto_buy_settings = await get_user_setting(user_id, 'auto_buy', session)
        # Если автобай выключен или настройки не найдены, пропускаем

        # Проверяем текущее состояние пользователя
        current_state = await state.get_state()
        if current_state is not None:
            return

        # Проверяем, является ли сообщение mint адресом
        token_address = message.text.strip()
        if not _is_valid_token_address(token_address):
            return

        logger.info(f"Detected mint address: {token_address}")

        # Получаем информацию о пользователе
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            logger.warning(f"User not found for auto-buy: {user_id}")
            return

        # Получаем баланс кошелька для проверки
        balance = await solana_service.get_wallet_balance(user.solana_wallet)

        # Проверяем достаточно ли средств
        if balance < auto_buy_settings['amount_sol']:
            await message.reply(
                f"❌ Недостаточно средств для автопокупки\n"
                f"Необходимо: {auto_buy_settings['amount_sol']} SOL\n"
                f"Доступно: {balance:.4f} SOL",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Отправляем сообщение о начале покупки
        status_message = await message.reply(
            "🔄 Выполняется автоматическая покупка токена...\n"
            "Пожалуйста, подождите",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить", callback_data="main_menu")]
            ])
        )

        # Инициализируем обработчик транзакций
        try:
            user_id = get_real_user_id(message)
            settings = await get_user_setting(user_id, 'buy', session)
            tx_handler = UserTransactionHandler(user.private_key, settings['gas_fee'])
        except ValueError as e:
            logger.error(f"Failed to initialize transaction handler: {e}")
            await status_message.edit_text(
                "❌ Ошибка инициализации кошелька",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Выполняем покупку с предустановленными параметрами
        amount_sol = auto_buy_settings['amount_sol']
        slippage = auto_buy_settings['slippage']

        # Получаем информацию о токене перед покупкой
        token_info = await token_info_service.get_token_info(token_address)

        tx_signature = await tx_handler.buy_token(
            token_address=token_address,
            amount_sol=amount_sol,
            slippage=slippage
        )

        if tx_signature:
            logger.info(f"Auto-buy successful: {tx_signature}")
            # Обновляем сообщение об успехе
            await status_message.edit_text(
                "✅ Токен успешно куплен!\n\n"
                f"🪙 Токен: {token_info.symbol if token_info else 'Unknown'}\n"
                f"💰 Потрачено: {amount_sol} SOL\n"
                f"⚙️ Slippage: {slippage}%\n"
                f"💳 Баланс: {(balance - amount_sol):.4f} SOL\n"
                f"🔗 Транзакция: [Explorer](https://solscan.io/tx/{tx_signature})",
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
        else:
            logger.error("Auto-buy transaction failed")
            await status_message.edit_text(
                "❌ Ошибка при покупке токена\n"
                "Пожалуйста, попробуйте позже или используйте стандартный процесс покупки",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )

    except Exception as e:
        logger.error(f"Error in auto-buy handler: {e}")
        await message.reply(
            "❌ Произошла ошибка при автопокупке\n"
            "Пожалуйста, используйте стандартный процесс покупки через меню",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )
