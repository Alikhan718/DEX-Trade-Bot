import traceback
from datetime import datetime

import logging
from decimal import Decimal
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re
from typing import Union
from aiogram.filters import StateFilter

from src.services.solana_service import SolanaService
from src.services.token_info import TokenInfoService
from src.database.models import User, LimitOrder, Trade, TransactionType, ReferralRecords
from .start import get_real_user_id
from src.solana_module.transaction_handler import UserTransactionHandler
from src.bot.states import BuyStates, AutoBuySettingsStates, LimitBuyStates, SellStates
from solders.pubkey import Pubkey
from src.solana_module.utils import get_bonding_curve_address
from ..crud import get_user_setting, update_user_setting

# from bot import bot


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
        logger.error(f"Invalid token address: {address}")
        return False


def _format_price(amount, format_length=2) -> str:
    def decimal_to_plain_string(exp_str: str) -> str:
        """
        Принимает число в виде строки (в том числе экспоненциальном формате: '3.0145939853849426E-8')
        и возвращает его в обычном десятичном виде без экспоненты, сохраняя все цифры.
        """
        d = Decimal(exp_str)  # создаём Decimal из строки

        # Парсим структуру внутри Decimal:
        # sign = 0 или 1, digits = кортеж цифр, exponent = целое (куда сдвинута запятая)
        sign, digits, exponent = d.as_tuple()

        # Превращаем кортеж цифр в строку.
        # Например, digits = (3, 0, 1, 4, ...) => "3014..."
        digits_str = "".join(str(dig) for dig in digits)

        # Если все цифры — это просто "0", значит число равно 0:
        if all(dig == 0 for dig in digits):
            # Учитывая знак, это будет либо '0', либо '-0'
            # Но обычно '-0' мы не любим. Если хочешь его сохранить, убери [:-1].
            return "-0" if sign else "0"

        # Позиция десятичной точки относительно начала digits_str
        # Пример: если у нас exponent = -8 и digits_str = "30145939853849426",
        # то int_position = len(digits_str) + exponent = 17 + (-8) = 9
        int_position = len(digits_str) + exponent

        # Формируем знак
        result_sign = "-" if sign else ""

        if int_position <= 0:
            # Все цифры "ушли" вправо от десятичной точки,
            # значит число меньше 1 и начинается с "0."
            # Пример: int_position = -2, digits_str = "30145" => 0.0030145...
            # Нужно добавить |int_position| нулей после десятичной точки
            zeros_needed = abs(int_position)
            result = result_sign + "0." + ("0" * (zeros_needed)) + digits_str
        elif int_position >= len(digits_str):
            # Все цифры — это целая часть, дробной нет, или её нужно дополнить нулями
            # Пример: int_position = 6, digits_str = "12345" => надо одну цифру "0" в конец
            zeros_needed = int_position - len(digits_str)
            result = result_sign + digits_str + ("0" * zeros_needed)
        else:
            # Часть цифр — целая, часть — дробная
            # Разделяем строку digits_str на две части
            # Пример: digits_str = "30145939853849426", int_position = 1 => "3.0145939853849426"
            result = (
                    result_sign
                    + digits_str[:int_position]
                    + "."
                    + digits_str[int_position:]
            )

        return result

    """Форматирует цену в читаемый вид с маленькими цифрами после точки"""
    amount = Decimal(str(amount))
    # Юникод для маленьких цифр
    small_digits = {
        '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
        '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉'
    }

    def to_small_and_normal_digits(number: Decimal, digits=2) -> str:
        """Преобразует число в строку, заменяя нули на маленькие цифры, а остальные на обычные"""
        number = decimal_to_plain_string(str(number))
        parts = str(number).split('.')
        int_part = parts[0]
        frac_part = parts[1] if len(parts) > 1 else ''
        # Считаем количество ведущих нулей в дробной части
        leading_zeros = len(frac_part) - len(frac_part.lstrip('0'))

        # Преобразуем эти нули в маленькие цифры, если больше 6 нулей
        if leading_zeros > 2:
            frac_part_small = ''.join(small_digits[digit] for digit in str(leading_zeros))
        else:
            frac_part_small = ''.join('0' for _ in range(leading_zeros))

        # Оставшиеся цифры — обычные
        frac_part_normal = frac_part[leading_zeros:(leading_zeros + 5)]
        return f"{int_part}{'.' if frac_part_normal else ''}{frac_part_small}{frac_part_normal}"

    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.{format_length}f}M"
    elif amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    elif amount < 1 and amount != 0:
        return to_small_and_normal_digits(amount, format_length)
    else:
        return f"{amount:.{format_length}f}"


@router.callback_query(F.data == "buy", flags={"priority": 3})
async def on_buy_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Купить в главном меню"""
    try:
        await callback_query.message.answer(
            "🔍 Введите адрес токена, который хотите купить:\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=ForceReply(selective=True)
        )
        await state.set_state(BuyStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Error in buy button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")


async def get_sol_update_keyboard(state: FSMContext, prefix="buy"):
    data = await state.get_data()
    is_limit_order = data.get("is_limit_order", False)
    chosen_amount = data.get("amount_sol", None)
    gas_fee = data.get("gas_fee", None)
    # Формируем клавиатуру
    keyboard = []

    # Кнопки выбора типа ордера
    keyboard.append([
        InlineKeyboardButton(
            text="🟢 Купить" if not is_limit_order else "⚪️ Купить",
            callback_data="market_buy"
        ),
        InlineKeyboardButton(
            text="🟢 Лимитный" if is_limit_order else "⚪️ Лимитный",
            callback_data="limit_buy"
        )
    ])

    # Предустановленные суммы
    keyboard.extend([
        [
            InlineKeyboardButton(text=f"{'✅️' if chosen_amount == 0.002 else ''} 0.002 SOL",
                                 callback_data=f"{prefix}_0.002"),
            InlineKeyboardButton(text=f"{'✅️' if chosen_amount == 0.005 else ''} 0.005 SOL",
                                 callback_data=f"{prefix}_0.005"),
            InlineKeyboardButton(text=f"{'✅️' if chosen_amount == 0.01 else ''} 0.01 SOL",
                                 callback_data=f"{prefix}_0.01")
        ],
        [
            InlineKeyboardButton(text=f"{'✅️' if chosen_amount == 0.02 else ''} 0.02 SOL",
                                 callback_data=f"{prefix}_0.02"),
            InlineKeyboardButton(text=f"{'✅️' if chosen_amount == 0.1 else ''} 0.1 SOL", callback_data=f"{prefix}_0.1"),
            InlineKeyboardButton(
                text=f"{'✅️ ' + str(_format_price(chosen_amount)) if chosen_amount and chosen_amount not in [0.002, 0.005, 0.01, 0.02, 0.1] else ''} Custom",
                callback_data=f"{prefix}_custom")
        ],
        [
            InlineKeyboardButton(
                text=f"🚀 Gas Fee {': ' + _format_price(gas_fee / 1e9) + ' SOL' if gas_fee else ''}",
                callback_data=f"{prefix}_set_gas_fee"
            )
        ]
    ])
    return keyboard


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
        settings = await get_user_setting(user_id, 'buy', session)
        # Save token address and initial slippage to state
        await state.update_data({
            'token_address': token_address,
            'slippage': settings['slippage'] if 'slippage' in settings else 1.0,
            'gas_fee': settings['gas_fee'] if 'gas_fee' in settings else None,
            'balance': balance,
            'sol_price': sol_price,
            'usd_balance': usd_balance,
        })

        # Get current slippage from state
        data = await state.get_data()
        slippage = data.get('slippage', 1.0)  # Default to 1% if not set

        # Формируем клавиатуру
        keyboard = await get_sol_update_keyboard(
            state=state,
            prefix="buy"
        )
        keyboard.append(
            [InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="buy_set_slippage")],
        )
        keyboard.append(
            [InlineKeyboardButton(text="💰 Купить", callback_data="confirm_buy")]
        )
        keyboard.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        )
        # Формируем сообщение
        message_text = (
            f"💲{token_info.symbol} 📈 - {token_info.name}\n\n"
            f"📍 Адрес токена:\n`{token_address}`\n\n"
            f"💰 Баланс кошелька:\n"
            f"• SOL Balance: {_format_price(balance)} SOL (${_format_price(usd_balance)})\n\n"
            f"📊 Информация о токене:\n"
            f"• Price: ${_format_price(token_info.price_usd)}\n"
            f"• MC: ${_format_price(token_info.market_cap)}\n"
            f"• Renounced: {'✔️' if token_info.is_renounced else '✖️'} "
            f"Burnt: {'✔️' if token_info.is_burnt else '✖️'}\n\n"
            f"🔍 Анализ: [Pump](https://www.pump.fun/{token_address})"
        )

        await message.answer(
            message_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="MARKDOWN",
            disable_web_page_preview=True
        )

    except Exception as e:
        traceback.print_exc()
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
        is_limit_order = data.get("is_limit_order", False)
        trigger_price_percent = data.get("trigger_price_percent")

        logger.info(f"Buy parameters - Token: {token_address}, Amount: {amount_sol} SOL, Slippage: {slippage}%")

        if not token_address or not amount_sol:
            logger.error("Missing token address or amount")
            await callback_query.answer("❌ Не указан токен или сумма")
            return
        token_info = await token_info_service.get_token_info(token_address)
        if is_limit_order:
            if not trigger_price_percent:
                logger.error("Missing trigger price for limit order")
                await callback_query.answer("❌ Не указана триггерная цена")
                return

            # Get current token price
            if not token_info:
                logger.error("Failed to get token info")
                await callback_query.answer("❌ Не удалось получить информацию о токене")
                return

            # Calculate trigger price in USD
            trigger_price_usd = token_info.price_usd * (1 + (trigger_price_percent / 100))

            # Create limit order
            limit_order = LimitOrder(
                user_id=user.id,
                token_address=token_address,
                order_type='buy',
                amount_sol=amount_sol,
                trigger_price_usd=trigger_price_usd,
                trigger_price_percent=trigger_price_percent,
                slippage=slippage,
                status='active'
            )
            session.add(limit_order)
            await session.commit()

            # Send confirmation message
            await callback_query.message.edit_text(
                "✅ Лимитный ордер создан!\n\n"
                f"💰 Сумма: {_format_price(amount_sol)} SOL\n"
                f"📈 Триггерная цена: {trigger_price_percent}% (${_format_price(trigger_price_usd)})\n"
                f"⚙️ Slippage: {slippage}%\n\n"
                "Ордер будет исполнен автоматически при достижении указанной цены.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Regular market buy...
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
        token_info = await token_info_service.get_token_info(token_address)
        sol_price_usd = await token_info_service.get_token_info('So11111111111111111111111111111111111111112')
        # Get token price before transaction
        token_price_sol = token_info.price_usd / sol_price_usd.price_usd

        # Execute buy transaction
        logger.info("Executing buy transaction")
        tx_signature = await tx_handler.buy_token(
            token_address=token_address,
            amount_sol=amount_sol,
            slippage=slippage,
            user=user
        )

        if tx_signature:
            logger.info(f"Buy transaction successful: {tx_signature}")

            # Calculate token amount from SOL amount and price
            token_amount = amount_sol / token_price_sol
            logger.info(f"Amount sol: {amount_sol} \nToken Price Sol: {token_price_sol} \nToken amount:{token_amount}")
            # Update success message
            await status_message.edit_text(
                "✅ Токен успешно куплен!\n\n"
                f"💰 Потрачено: {_format_price(amount_sol)} SOL\n"
                f"📈 Получено: {_format_price(token_amount)} токенов\n"
                f"💵 Цена: {_format_price(token_price_sol)} SOL {'($' + _format_price(token_info.price_usd) + ')' if token_info and token_info.price_usd else ''}\n"
                f"🔗 Транзакция: [Explorer](https://solscan.io/tx/{tx_signature})",
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            # trade = Trade(
            #     user_id=user.id,
            #     token_address=token_address,
            #     amount=token_amount,
            #     price_usd=token_info.price_usd if token_info and token_info.price_usd else -1.0,
            #     amount_sol=amount_sol,
            #     created_at=datetime.now(),
            #     transaction_type=0,
            #     status="SUCCESS",
            #     gas_fee=buy_settings['gas_fee'],
            #     transaction_hash=str(tx_signature),
            # )
            # session.add(trade)
            # await session.commit()
            # if user.referral_id:
            #     ref_record = ReferralRecords(
            #         user_id=user.referral_id,
            #         trade_id=trade.id or None,
            #         amount_sol=amount_sol * 0.005,
            #         created_at=datetime.now(),
            #         is_sent=False
            #     )
            #     session.add(ref_record)
            #     await session.commit()
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


async def get_slippage_update_keyboard(state: FSMContext, prefix="buy", back_callback="back_to_buy"):
    data = await state.get_data()
    chosen_slippage = float(data.get('slippage', -1))
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{'✅️' if 0.1 == chosen_slippage else ''} 0.1%",
                                 callback_data=f"{prefix}_slippage_0.5"),
            InlineKeyboardButton(text=f"{'✅️' if 1 == chosen_slippage else ''} 1%",
                                 callback_data=f"{prefix}_slippage_1"),
            InlineKeyboardButton(text=f"{'✅️' if 2 == chosen_slippage else ''} 2%",
                                 callback_data=f"{prefix}_slippage_2")
        ],
        [
            InlineKeyboardButton(text=f"{'✅️' if 3 == chosen_slippage else ''} 3%",
                                 callback_data=f"{prefix}_slippage_3"),
            InlineKeyboardButton(text=f"{'✅️' if 5 == chosen_slippage else ''} 5%",
                                 callback_data=f"{prefix}_slippage_5"),
            InlineKeyboardButton(
                text=f"{('✅️ ' + str(_format_price(chosen_slippage)) + '%') if chosen_slippage not in [0.1, 1, 2, 3, 5, -1] else ''} Custom",
                callback_data=f"{prefix}_slippage_custom")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)]
    ])


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
        keyboard = await get_slippage_update_keyboard(
            state=state,
            prefix="buy",
            back_callback="back_to_buy"
        )

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
async def handle_slippage_choice(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle slippage choice"""
    try:
        # Verify we're in buy context
        data = await state.get_data()

        if data.get("menu_type") != "buy":
            return

        choice = callback_query.data.split("_")[2]  # buy_slippage_X -> X

        if choice == "custom":
            await callback_query.message.answer(
                "⚙️ Пользовательский Slippage для покупки\n\n"
                "Введите значение в процентах (например, 1.5):",
                reply_markup=ForceReply(selective=True)  # Указываем, что требуется ответ
            )
            # Устанавливаем состояние для ожидания ввода пользователя
            await state.set_state(BuyStates.waiting_for_slippage)
            return

        # Convert choice to float and save to state
        slippage = float(choice)
        user_id = get_real_user_id(callback_query)

        buy_setting = await get_user_setting(user_id, 'buy', session)
        buy_setting['slippage'] = slippage
        await update_user_setting(user_id, 'buy', buy_setting, session)
        await state.update_data(slippage=slippage)
        await show_buy_menu(callback_query.message, state, session, callback_query.from_user.id)

    except Exception as e:
        logger.error(f"Error handling slippage choice: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(BuyStates.waiting_for_slippage)
async def handle_custom_slippage(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle custom slippage input"""
    try:
        slippage = float(callback_query.text.strip().replace("%", "").replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage value")

        user_id = get_real_user_id(callback_query)

        buy_setting = await get_user_setting(user_id, 'buy', session)
        buy_setting['slippage'] = slippage
        await update_user_setting(user_id, 'buy', buy_setting, session)
        await state.update_data(slippage=slippage)

        # Отправляем новое сообщение об успешном изменении
        status_message = await callback_query.answer(f"✅ Slippage установлен: {slippage}%")

        # Показываем обновленное меню покупки
        await show_buy_menu(status_message, state, session, callback_query.from_user.id)

    except ValueError:
        await callback_query.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100 (можно с символом % или без):",
            reply_markup=ForceReply(selective=True)
        )


@router.callback_query(lambda c: c.data.startswith("buy_set_gas_fee"), flags={"priority": 11})
async def handle_set_gas_fee(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle gas fee"""
    try:
        await callback_query.message.answer(
            "⚙️ Пользовательский Gas Fee для покупки\n\n"
            "Введите значение в SOL (например, 0.01):",
            reply_markup=ForceReply(selective=True)  # Указываем, что требуется ответ
        )
        # Устанавливаем состояние для ожидания ввода пользователя
        await state.set_state(BuyStates.waiting_for_gas_fee)
        return


    except Exception as e:
        logger.error(f"Error handling gas_fee: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(BuyStates.waiting_for_gas_fee)
async def handle_custom_gas_fee(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle custom slippage input"""
    try:
        gas_fee = float(callback_query.text.replace(",", "."))
        if gas_fee <= 0 or gas_fee > 10:
            raise ValueError("Invalid gas_fee value")
        gas_fee *= 1e9
        user_id = get_real_user_id(callback_query)

        buy_setting = await get_user_setting(user_id, 'buy', session)
        buy_setting['gas_fee'] = gas_fee
        await update_user_setting(user_id, 'buy', buy_setting, session)
        await state.update_data(gas_fee=gas_fee)

        # Отправляем новое сообщение об успешном изменении
        status_message = await callback_query.answer(f"✅ Gas Fee установлен: {_format_price(gas_fee / 1e9)} SOL")

        # Показываем обновленное меню покупки
        await show_buy_menu(status_message, state, session, callback_query.from_user.id)

    except ValueError as e:
        logger.error(f"[BUY] Invalid gas_fee value: {e}")
        await callback_query.reply(
            "❌ Неверное значение. Введите число от 0 до 10:",
            reply_markup=ForceReply(selective=True)
        )


@router.callback_query(lambda c: c.data == "back_to_buy", flags={"priority": 10})
async def handle_back_to_buy(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Return to buy menu"""
    logger.info("[BUY] Handling back_to_buy")
    data = await state.get_data()
    logger.info(f"[BUY] Current state data: {data}")
    await show_buy_menu(callback_query.message, state, session, callback_query.from_user.id)
    logger.info("[BUY] Showed buy menu")


@router.callback_query(lambda c: c.data == "limit_buy", flags={"priority": 3})
async def handle_limit_buy(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обработчик для создания лимитного ордера на покупку"""
    try:
        # Устанавливаем флаг лимитного ордера в состоянии
        await state.update_data(is_limit_order=True)
        # Показываем обновленное меню покупки
        logger.info("[BUY] Showed buy menu with limit order")
        await show_buy_menu(callback_query.message, state, session, callback_query.from_user.id)
    except Exception as e:
        logger.error(f"Error handling limit buy: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.callback_query(lambda c: c.data == "market_buy", flags={"priority": 3})
async def handle_market_buy(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обработчик для создания лимитного ордера на покупку"""
    try:
        # Устанавливаем флаг лимитного ордера в состоянии
        await state.update_data(is_limit_order=False)
        # Показываем обновленное меню покупки
        logger.info("[BUY] Showed buy menu")
        await show_buy_menu(callback_query.message, state, session, callback_query.from_user.id)
    except Exception as e:
        logger.error(f"Error handling buy: {e}")
        await callback_query.answer("❌ Произошла ошибка")


async def show_buy_menu(message: types.Message, state: FSMContext, session: AsyncSession, user_id=None):
    """Показать меню покупки"""
    try:

        # Get current data
        user_id = user_id if user_id else message.from_user.id
        settings = await get_user_setting(user_id, 'buy', session)
        data = await state.get_data()
        token_address = data.get("token_address")
        amount_sol = data.get("amount_sol")
        slippage = settings["slippage"]
        is_limit_order = data.get("is_limit_order", False)
        trigger_price_percent = data.get("trigger_price_percent", 20)
        logger.info(f"[BUY] Current state data: {data}")

        if not token_address:
            await message.edit_text(
                "❌ Не указан адрес токена",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return

        # Получаем информацию о токене
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await message.edit_text(
                "❌ Не удалось получить информацию о токене",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return
        print(f"is_limit_order: {is_limit_order}")
        # Получаем баланс пользователя
        if not user_id:
            user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            await message.edit_text(
                "❌ Пользователь не найден",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return

        # Get wallet balance
        balance = data.get('balance')
        sol_price = data.get('sol_price')
        usd_balance = data.get('usd_balance')

        # Формируем клавиатуру
        keyboard = await get_sol_update_keyboard(
            state=state,
            prefix='buy'
        )
        # Настройки
        keyboard.append([InlineKeyboardButton(text=f"⚙️ Slippage: {slippage}%", callback_data="buy_set_slippage")])

        # Для лимитного ордера добавляем кнопку установки триггерной цены
        print(f"is_limit_order: {is_limit_order}")
        if is_limit_order:
            trigger_price_text = f"💵 Trigger Price: {trigger_price_percent}%" if trigger_price_percent else "💵 Set Trigger Price"
            if trigger_price_percent:
                # Рассчитываем цену в долларах
                trigger_price_usd = token_info.price_usd * (1 + (trigger_price_percent / 100))
                trigger_price_usd = format(trigger_price_usd, '.6f')
                trigger_price_text += f" (${_format_price(trigger_price_usd)})"
            keyboard.append([InlineKeyboardButton(text=trigger_price_text, callback_data="set_trigger_price")])

        # Кнопка подтверждения
        keyboard.append([
            InlineKeyboardButton(
                text="📝 Создать ордер" if is_limit_order else "💰 Купить",
                callback_data="confirm_buy"
            )
        ])

        # Кнопка назад
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])

        if is_limit_order:
            trigger_price_usd = format(token_info.price_usd * (1 + (trigger_price_percent / 100)), '.6f')
            addiction = (f"⚙️ Slippage: {slippage}%\n" if slippage else "") + (
                f"💵 Trigger Price: {trigger_price_percent}% (${_format_price(trigger_price_usd)})\n" if trigger_price_percent else "")
        else:
            addiction = ""

        # Формируем сообщение
        message_text = (
                f"💲{token_info.symbol} 📈 - {token_info.name}\n\n"
                f"📍 Адрес токена:\n`{token_address}`\n\n"
                f"💰 Баланс кошелька:\n"
                f"• SOL Balance: {_format_price(balance)} SOL (${_format_price(usd_balance)})\n\n"
                + (f"💰 Выбранная сумма: {_format_price(amount_sol)} SOL\n" if amount_sol else "")
                + addiction
                + f"\n📊 Информация о токене:\n"
                + f"• Price: ${_format_price(token_info.price_usd)}\n"
                + f"• MC: ${_format_price(token_info.market_cap)}\n"
                + f"• Renounced: {'✔️' if token_info.is_renounced else '✖️'} "
                + f"Burnt: {'✔️' if token_info.is_burnt else '✖️'}\n\n"
                + f"🔍 Анализ: [Pump](https://www.pump.fun/{token_address})"
        )

        # Отправляем или редактируем сообщение
        message = message.message if hasattr(message, 'message') else message
        await message.edit_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="MARKDOWN",
            disable_web_page_preview=True
        )

    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error showing buy menu: {e}")
        await message.edit_text(
            "❌ Произошла ошибка при отображении меню",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )


@router.callback_query(lambda c: c.data == "set_trigger_price", flags={"priority": 3})
async def handle_set_trigger_price(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик для установки триггерной цены"""
    try:
        await state.set_state(BuyStates.waiting_for_trigger_price)
        await callback_query.message.answer(
            "💵 Установка триггерной цены\n\n"
            "Введите процент изменения цены для срабатывания ордера.\n"
            "Например:\n"
            "• 10 - ордер сработает когда цена вырастет на 10%\n"
            "• -5 - ордер сработает когда цена упадет на 5%",
            reply_markup=ForceReply(selective=True)
        )
        return
    except Exception as e:
        logger.error(f"Error setting trigger price: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.message(BuyStates.waiting_for_trigger_price)
async def handle_trigger_price_input(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик ввода триггерной цены"""
    try:
        # Проверяем введенное значение
        try:
            trigger_price = float(message.text.strip().replace("%", "").replace(",", "."))
        except ValueError:
            await message.reply(
                "❌ Пожалуйста, введите числовое значение",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_buy")]
                ])
            )
            return

        # Сохраняем значение в состоянии
        await state.update_data(trigger_price_percent=trigger_price)

        # Отправляем сообщение об успешной установке
        status_message = await message.reply(f"✅ Триггерная цена установлена: {trigger_price}%")

        # Получаем ID пользователя
        user_id = message.from_user.id

        # Показываем обновленное меню покупки
        await show_buy_menu(status_message, state, session, user_id)

    except Exception as e:
        logger.error(f"Error handling trigger price input: {e}")
        await message.reply(
            "❌ Произошла ошибка при установке триггерной цены",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_buy")]
            ])
        )


@router.message(BuyStates.waiting_for_amount)
async def handle_custom_amount(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle custom percentage input"""
    try:
        amount = float(callback_query.text.replace(",", "."))
        if amount < 0:
            raise ValueError("Invalid amount value")
        await state.update_data(amount_sol=amount)
        # Отправляем новое сообщение об успешном изменении
        status_message = await callback_query.answer(f"✅ Количество установлено: {amount} SOL")
        # Показываем обновленное меню продажи
        await show_buy_menu(status_message, state, session, callback_query.from_user.id)

    except ValueError:
        await callback_query.reply(
            "❌ Неверное значение. Введите число от 1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_buy")]
            ])
        )


@router.callback_query(lambda c: c.data.startswith("buy") and "token" not in c.data, flags={"priority": 3})
async def handle_preset_amount(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Handle preset amount buttons"""
    try:
        # Skip if this is a buy_token_ callback
        if callback_query.data.startswith("buy_token_"):
            logger.info("Ignoring buy_token")
            return
            
        # Extract amount from callback data
        amount = callback_query.data.split('_')[1]
        if amount == "custom":
            await callback_query.message.answer(
                "⚙️ Количество для покупки\n\n"
                "Введите значение (например, 1.23):",
                reply_markup=ForceReply(selective=True)
            )
            await state.set_state(BuyStates.waiting_for_amount)
            return
        amount = float(amount)
        prev_amount = await state.get_value('amount_sol', -1)
        if amount == float(prev_amount):
            return
        await state.update_data(amount_sol=amount)

        await show_buy_menu(callback_query.message, state, session, callback_query.from_user.id)

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
    Отображает меню лимитного ордера (порог цены, сумма SOL, slippage, подтверждение).
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
        text = message.text.strip().replace("%", "").replace(",", ".")
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

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'🟢' if settings['type'] == 'buy' else '⚪️'} Покупка",
                callback_data="toggle_auto_buy_type_buy"
            ), InlineKeyboardButton(
                text=f"{'🟢' if settings['type'] == 'sell' else '⚪️'} Продажа",
                callback_data="toggle_auto_buy_type_sell"
            )],
            [InlineKeyboardButton(
                text=f"{'🟢 Активно' if settings['enabled'] else '🔴 Не Активно'} ",
                callback_data="toggle_auto_buy"
            )],
            [InlineKeyboardButton(
                text=f"💰 {'Сумма' if settings['type'] == 'buy' else 'Процент'}: {settings['amount_sol']}{' SOL' if settings['type'] == 'buy' else '%'}",
                callback_data="set_auto_buy_amount"
            )],
            [InlineKeyboardButton(
                text=f"⚙️ Slippage: {settings['slippage']}%",
                callback_data="set_auto_buy_slippage"
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])

        text = (
            "⚡️ Настройки Авто (Продажи / Покупки)\n\n"
            f"Статус: {'Включен' if settings['enabled'] else 'Выключен'}\n"
            f"{'Сумма покупки' if settings['type'] == 'buy' else 'Процент продажи'}: {settings['amount_sol']}{' SOL' if settings['type'] == 'buy' else '%'}\n"
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


@router.callback_query(lambda c: c.data.startswith("toggle_auto_buy_type_"), flags={"priority": 3})
async def toggle_auto_buy(callback: types.CallbackQuery, session: AsyncSession):
    """Включить/выключить автобай"""
    try:
        params = callback.data
        setting_type = params.replace('toggle_auto_buy_type_', '')
        user_id = get_real_user_id(callback)
        settings = await get_user_setting(user_id, 'auto_buy', session)
        if settings['type'] == setting_type:
            return await callback.answer("Этот тип уже выбран", show_alert=True)
        settings['type'] = setting_type
        if setting_type == 'sell':
            settings['amount_sol'] = 100
        else:
            settings['amount_sol'] = 0.01
        await update_user_setting(user_id, 'auto_buy', settings, session)
        await show_auto_buy_settings(callback, session)

    except Exception as e:
        logger.error(f"Error toggling auto-buy: {e}")
        await callback.answer("❌ Произошла ошибка")


@router.callback_query(F.data == "set_auto_buy_amount", flags={"priority": 3})
async def handle_set_auto_buy_amount(callback: types.CallbackQuery, state: FSMContext, session):
    """Установка суммы для автобая"""
    try:
        user_id = get_real_user_id(callback)
        setting = await get_user_setting(user_id, 'auto_buy', session)
        await callback.message.answer(
            f"💰 {'Введите сумму для автопокупки в SOL' if setting['type'] == 'buy' else 'Введите сумму для автопродажи в %'}\n"
            f"Например: {'0.1' if setting['type'] == 'buy' else '100'}",
            reply_markup=ForceReply(selective=True)
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
        user_id = get_real_user_id(message)
        settings = await get_user_setting(user_id, 'auto_buy', session)
        try:
            amount = float(message.text.strip())
            if amount <= 0 or (amount > 100 and settings['type'] == 'sell'):
                raise ValueError("Amount must be positive")
        except ValueError:
            await message.reply(
                "❌ Неверный формат суммы\n"
                "Пожалуйста, введите положительное число" + f" {'от 1 до 100' if settings['type'] == 'sell' else 'от 0'}",
                reply_markup=ForceReply(selective=True)
            )
            return

        settings['amount_sol'] = amount
        await update_user_setting(user_id, 'auto_buy', settings, session)

        # Очищаем состояние и показываем обновленные настройки
        await state.clear()
        await message.reply(
            f"✅ Сумма установлена: {amount}{' SOL' if settings['type'] == 'buy' else '%'}"
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
        keyboard = await get_slippage_update_keyboard(
            state=state,
            prefix="auto_buy",
            back_callback="auto_buy_settings"
        )

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
            await callback.message.answer(
                "⚙️ Введите значение slippage (в процентах)\n"
                "Например: 1.5",
                reply_markup=ForceReply(selective=True)
            )
            await state.set_state(AutoBuySettingsStates.ENTER_SLIPPAGE)
            return

        # Если выбрано предустановленное значение
        slippage = float(choice)
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
            slippage = float(message.text.strip().replace("%", "").replace(",", "."))
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


@router.message(F.text.len() == 44 and StateFilter(None), flags={"priority": 1})
async def handle_auto_buy(message: types.Message, state: FSMContext, session: AsyncSession,
                          solana_service: SolanaService):
    """Автоматическая покупка при получении mint адреса"""
    try:
        logger.info('Handler: AUTO-BUY start')
        user_id = get_real_user_id(message)

        auto_buy_settings = await get_user_setting(user_id, 'auto_buy', session)

        # Проверяем, является ли сообщение mint адресом
        token_address = message.text.strip()
        if not _is_valid_token_address(token_address):
            return await message.reply(
                f"❌ Не допустимый формат адреса токена, для автоматической {'покупки' if auto_buy_settings['type'] == 'buy' else 'продажи'}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )

        logger.info(f"Detected mint address: {token_address}")

        # Получаем информацию о пользователе
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            logger.warning(f"User not found for auto-buy: {user_id}")
            return

        # Проверяем включен ли автобай
        if not (await get_user_setting(user_id, 'auto_buy', session))['enabled']:
            logger.warning(f"Auto-buy is disabled for user: {user_id} Starting showing token info")
            # Если автобай выключен, показываем меню с информацией о токене
            client = solana_service.create_client(user.private_key)
            token_balance = await client.get_token_balance(Pubkey.from_string(token_address))
            
            # Получаем информацию о токене
            token_info = await token_info_service.get_token_info(token_address)
            if not token_info:
                await message.reply("❌ Не удалось получить информацию о токене")
                return

            # Форматируем сообщение с информацией о токене
            message_text = (
                f"💎 {token_info.symbol} ({token_info.name})\n"
                f"💵 Цена: ${_format_price(token_info.price_usd)}\n"
                f"💰 Market Cap: ${_format_price(token_info.market_cap)}\n"
                f"📍 Адрес: <code>{token_address}</code>\n"
            )

            # Создаем клавиатуру с кнопками купить/продать
            keyboard = []
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🟢 Купить {token_info.symbol}",
                    callback_data=f"buy_token_{token_address}"
                )
            ])
            
            # Добавляем кнопку продажи только если есть баланс токена
            if token_balance > 0:
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"🔴 Продать {token_info.symbol}",
                        callback_data=f"select_token_{token_address}"
                    )
                ])
            
            keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])

            await message.reply(
                message_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                parse_mode="HTML"
            )
            return

        # Получаем баланс кошелька для проверки
        balance = await solana_service.get_wallet_balance(user.solana_wallet)

        # Проверяем достаточно ли средств
        if balance < auto_buy_settings['amount_sol'] and auto_buy_settings['type'] == 'buy':
            await message.reply(
                f"❌ Недостаточно средств для автопокупки\n"
                f"Необходимо: {auto_buy_settings['amount_sol']} SOL\n"
                f"Доступно: {_format_price(balance)} SOL",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Отправляем сообщение о начале покупки
        status_message = await message.reply(
            f"🔄 Выполняется автоматическая {'продажа' if auto_buy_settings['type'] == 'sell' else 'покупка'} токена...\n"
            "Пожалуйста, подождите"
        )

        # Инициализируем обработчик транзакций
        try:
            user_id = get_real_user_id(message)
            auto_buy_settings = await get_user_setting(user_id, 'auto_buy', session)
            settings = await get_user_setting(user_id, auto_buy_settings['type'], session)
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

        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            token_info = await token_info_service.get_token_info(token_address)

        sol_price_usd = await token_info_service.get_token_info('So11111111111111111111111111111111111111112')
        token_price_sol = token_info.price_usd / sol_price_usd.price_usd
        slippage = auto_buy_settings['slippage']

        amount_sol = auto_buy_settings['amount_sol']  # will be changed

        sell_percentage = 100  # initial value in sell case

        if auto_buy_settings['type'] == 'buy':
            token_amount = float(amount_sol) / float(token_price_sol)
        else:
            token_balance = await tx_handler.client.get_token_balance(Pubkey.from_string(str(token_address)))
            sell_percentage = amount_sol  # we store it as amount_sol in sell case it's actually percentage
            token_amount = (token_balance * (sell_percentage / 100))
            amount_sol = token_amount * token_price_sol  # amount sol = token amount / token price per sol
            if not token_balance or token_balance == 0 or token_amount > token_balance:
                return await status_message.edit_text(
                    "❌ Ошибка: Не достаточно средств на балансе для продажи",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                    ])
                )

        if auto_buy_settings['type'] == 'buy':
            tx_signature = await tx_handler.buy_token(
                token_address=token_address,
                amount_sol=amount_sol,
                slippage=slippage,
                user=user
            )
        else:
            tx_signature = await tx_handler.sell_token(
                token_address=token_address,
                sell_percentage=sell_percentage,
                slippage=slippage
            )

        if tx_signature:
            logger.info(f"Auto-{auto_buy_settings['type']} successful: {tx_signature}")
            # Обновляем сообщение об успехе
            is_buy = auto_buy_settings['type'] == 'buy'
            amount_text = (
                f"💰 Потрачено: {_format_price(amount_sol)} SOL"
                if is_buy else
                f"💰 Продано: {_format_price(token_amount)} токенов"
            )
            # trade = Trade(
            #     user_id=user.id,
            #     token_address=token_address,
            #     amount=token_amount,
            #     price_usd=token_info.price_usd if token_info and token_info.price_usd else -1.0,
            #     amount_sol=amount_sol,
            #     created_at=datetime.now(),
            #     transaction_type=(0 if is_buy else 1),
            #     status="SUCCESS",
            #     gas_fee=settings['gas_fee'],
            #     transaction_hash=str(tx_signature),
            # )
            # session.add(trade)
            # await session.commit()
            # if user.referral_id:
            #     logger.info("User has referral")
            #     ref_record = ReferralRecords(
            #         user_id=user.referral_id,
            #         trade_id=trade.id or None,
            #         amount_sol=amount_sol * 0.005,
            #         created_at=datetime.now(),
            #         is_sent=False
            #     )
            #     session.add(ref_record)
            #     await session.commit()
            await status_message.edit_text(
                f"✅ Токен успешно {'Куплен' if is_buy else 'Продан'}!\n\n"
                f"🪙 Токен: {token_info.symbol if token_info else 'Unknown'} {token_info.name if token_info else ''}\n"
                f"{amount_text}\n"
                f"⚙️ Slippage: {slippage}%\n"
                f"💳 Баланс: {_format_price(balance - amount_sol if is_buy else balance)} SOL\n"
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


@router.callback_query(F.data == "limit_orders", flags={"priority": 3})
async def show_limit_orders(callback_query: types.CallbackQuery, session: AsyncSession):
    """Показать список активных лимитных ордеров"""
    try:
        user_id = get_real_user_id(callback_query)

        # Получаем пользователя
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return

        # Получаем активные ордера пользователя
        stmt = (
            select(LimitOrder)
            .where(
                LimitOrder.user_id == user.id,
                LimitOrder.status == 'active'
            )
            .order_by(LimitOrder.created_at.desc())
        )
        result = await session.execute(stmt)
        orders = result.scalars().all()

        if not orders:
            await callback_query.message.edit_text(
                "📊 Лимитные ордера\n\n"
                "У вас нет активных лимитных ордеров.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Формируем сообщение со списком ордеров
        message_text = "📊 Лимитные ордера\n\n"
        keyboard = []

        for order in orders:
            # Получаем информацию о токене
            token_info = await token_info_service.get_token_info(order.token_address)
            if not token_info:
                continue

            # Формируем информацию в зависимости от типа ордера
            if order.order_type == 'buy':
                amount_text = f"💰 Сумма: {_format_price(order.amount_sol)} SOL"
            else:  # sell
                amount_text = f"💰 Количество: {_format_price(order.amount_tokens)} токенов"

            # Добавляем информацию об ордере
            message_text += (
                f"🎯 Ордер #{order.id} ({order.order_type.upper()})\n"
                f"{amount_text}\n"
                f"📈 Триггер: {order.trigger_price_percent}% (${_format_price(order.trigger_price_usd)})\n"
                f"💎 Токен: {token_info.symbol}\n"
                f"⚙️ Slippage: {order.slippage}%\n"
                f"📅 Создан: {order.created_at.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)\n"
                "➖➖➖➖➖➖➖➖➖➖\n\n"
            )

            # Добавляем кнопку отмены для каждого ордера
            keyboard.append([
                InlineKeyboardButton(
                    text=f"❌ Отменить #{order.id}",
                    callback_data=f"cancel_limit_order_{order.id}"
                )
            ])

        # Добавляем кнопку "Назад"
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")])

        await callback_query.message.edit_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

    except Exception as e:
        logger.error(f"Error showing limit orders: {e}")
        await callback_query.answer("❌ Произошла ошибка")


@router.callback_query(lambda c: c.data.startswith("cancel_limit_order_"), flags={"priority": 3})
async def cancel_limit_order(callback_query: types.CallbackQuery, session: AsyncSession):
    """Отменить лимитный ордер"""
    try:
        user_id = get_real_user_id(callback_query)
        order_id = int(callback_query.data.split('_')[-1])

        # Получаем ордер
        stmt = (
            select(LimitOrder)
            .where(
                LimitOrder.id == order_id,
                LimitOrder.status == 'active'
            )
        )
        result = await session.execute(stmt)
        order = result.scalar_one_or_none()

        if not order:
            await callback_query.answer("❌ Ордер не найден или уже отменен")
            return

        # Проверяем, принадлежит ли ордер пользователю
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user or order.user_id != user.id:
            await callback_query.answer("❌ У вас нет прав на отмену этого ордера")
            return

        # Отменяем ордер
        order.status = 'cancelled'
        await session.commit()

        await callback_query.answer("✅ Ордер успешно отменен")

        # Обновляем список ордеров
        await show_limit_orders(callback_query, session)

    except Exception as e:
        logger.error(f"Error cancelling limit order: {e}")
        await callback_query.answer("❌ Произошла ошибка при отмене ордера")

@router.callback_query(lambda c: c.data.startswith("buy_token_"), flags={"priority": 5})
async def handle_buy_token_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, solana_service: SolanaService):
    """Обработчик кнопки покупки токена"""
    try:
        token_address = callback_query.data.replace("buy_token_", "")
        
        # Get user info
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            await callback_query.reply("❌ Пользователь не найден")
            return

        # Get token info
        token_info = await token_info_service.get_token_info(token_address)
        if not token_info:
            await callback_query.reply(
                "❌ Не удалось получить информацию о токене\n"
                "Пожалуйста, проверьте адрес и попробуйте снова"
            )
            return

        # Get wallet balance
        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        sol_price = await solana_service.get_sol_price()
        usd_balance = balance * sol_price
        settings = await get_user_setting(user_id, 'buy', session)
        # Save token address and initial slippage to state
        await state.update_data({
            'token_address': token_address,
            'slippage': settings['slippage'] if 'slippage' in settings else 1.0,
            'gas_fee': settings['gas_fee'] if 'gas_fee' in settings else None,
            'balance': balance,
            'sol_price': sol_price,
            'usd_balance': usd_balance,
        })
        await show_buy_menu(callback_query.message, state, session, user_id)
        
    except Exception as e:
        logger.error(f"Error handling buy token button: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )

@router.callback_query(lambda c: c.data.startswith("sell_token_"), flags={"priority": 3})
async def handle_sell_token_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обработчик кнопки продажи токена"""
    try:
        token_address = callback_query.data.replace("sell_token_", "")
        
        # Получаем информацию о пользователе
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()
        
        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return
            
        # Устанавливаем состояние и сохраняем адрес токена
        await state.set_state(SellStates.waiting_for_percentage)
        await state.update_data(token_address=token_address)
        
        # Показываем меню продажи
        from src.bot.handlers.sell import show_sell_menu
        await show_sell_menu(callback_query.message, state, session)
        
    except Exception as e:
        logger.error(f"Error handling sell token button: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )
