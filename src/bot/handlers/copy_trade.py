import logging
import time
import traceback
import uuid

from _decimal import Decimal
from aiogram import types
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.buy import _format_price
from src.bot.utils import get_real_user_id
from src.database.models import CopyTrade, ExcludedToken, User, CopyTradeTransaction
from src.bot.services.copy_trade_service import CopyTradeService
from src.bot.states import CopyTradeStates

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "copy_trade", flags={"priority": 4})
async def show_copy_trade_menu(callback: CallbackQuery, session: AsyncSession):
    """Показать главное меню копитрейдинга со списком конфигураций"""
    # Получаем пользователя
    user = await session.scalar(
        select(User).where(User.telegram_id == callback.from_user.id)
    )

    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    # Получаем копитрейды пользователя
    result = await session.execute(
        select(CopyTrade)
        .where(CopyTrade.user_id == user.id)
        .order_by(CopyTrade.created_at)
    )
    copy_trades = result.scalars().all()

    keyboard = []

    # Добавляем существующие копитрейды
    for ct in copy_trades:
        status = "✅" if ct.is_active else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status} {ct.name + ' ' if ct.name else ''}({ct.wallet_address[:6]}...)",
                callback_data=f"ct_settings:{ct.id}"
            )
        ])

    # Добавляем кнопки управления
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Добавить", callback_data="ct_add")],
        [InlineKeyboardButton(text="🚫 Исключить токены", callback_data="ct_exclude_tokens")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        "🤖 Copy Trading\n\n"
        f"Активных копитрейдов: {len(copy_trades)}/20",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data == "ct_add")
async def start_add_copy_trade(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начать процесс добавления нового копитрейда"""
    # Проверяем лимит
    count = await session.scalar(
        select(func.count(CopyTrade.id))
        .where(CopyTrade.user_id == callback.from_user.id)
    )

    if count >= 20:
        await callback.answer("Достигнут максимальный лимит копитрейдов (20)", show_alert=True)
        return

    await callback.message.answer(
        "Введите адрес кошелька для отслеживания:",
        reply_markup=ForceReply(selective=True)
    )
    await state.set_state(CopyTradeStates.ENTER_ADDRESS)


async def get_copy_trade_settings_keyboard(copy_trade_id: int, session: AsyncSession):
    result = await session.execute(
        select(CopyTrade).where(CopyTrade.id == copy_trade_id)
    )
    item = result.unique().scalar_one_or_none()
    if not item:
        return []
    # Структура кнопок в формате {"text": "callback_query"}
    buttons_data = [
        [
            {"📝 Название": f"ct_edit:name:{item.id}"},
            {"👛 Адрес кошелька": f"ct_edit:wallet:{item.id}"}
        ],
        [{f"📊 Процент копирования: {_format_price(item.copy_percentage)}%": f"ct_edit:copy_percentage:{item.id}"}],
        [{f"📉 Мин. сумма: {_format_price(item.min_amount)} SOL": f"ct_edit:min_amount:{item.id}"}],
        [{f"📈 Макс. сумма: {_format_price(item.max_amount) if item.max_amount else 'Без лимита'} SOL": f"ct_edit:max_amount:{item.id}"}],
        [{f"💰 Общая сумма: {_format_price(item.total_amount) if item.total_amount else 'Без лимита'} SOL": f"ct_edit:total_amount:{item.id}"}],
        [{
            f"🔄 Макс. копий токена: {item.max_copies_per_token or 'Без лимита'}": f"ct_edit:max_copies_per_token:{item.id}"}],
        [
            {f"⚡️ Buy Gas: {_format_price(item.buy_gas_fee / 1e9)}": f"ct_edit:buy_gas_fee:{item.id}"},
            {f"⚡️ Sell Gas: {_format_price(item.sell_gas_fee / 1e9)}": f"ct_edit:sell_gas_fee:{item.id}"}
        ],
        [
            {f"📊 Buy Slippage: {_format_price(item.buy_slippage)}%": f"ct_edit:buy_slippage:{item.id}"},
            {f"📊 Sell Slippage: {_format_price(item.sell_slippage)}%": f"ct_edit:sell_slippage:{item.id}"}
        ],
        [
            {f"{'✅' if item.copy_sells else '❌'} Копировать продажи": f"ct_edit:copy_sells:{item.id}"},
            {f"{'✅' if item.anti_mev else '❌'} Anti-MEV": f"ct_edit:anti_mev:{item.id}"}
        ],
        [{f"{'✅' if item.is_active else '🔴'} Активный": f"ct_edit:is_active:{item.id}"}],
        [{"🗑 Удалить": f"ct_delete:{item.id}"}],
        [{"⬅️ Назад": "copy_trade"}]
    ]

    # Преобразование в InlineKeyboardButton
    keyboard = [
        [
            InlineKeyboardButton(
                text=list(button.keys())[0],
                callback_data=list(button.values())[0]) for button in row
        ]
        for row in buttons_data
    ]

    return keyboard


@router.callback_query(lambda c: c.data.startswith("ct_settings:"))
async def show_copy_settings(callback: CallbackQuery, session: AsyncSession, copy_trade_id = None):
    """Показать настройки конкретного копитрейда"""
    if copy_trade_id is None:
        copy_trade_id = int(callback.data.split(":")[1])

    result = await session.execute(
        select(CopyTrade).where(CopyTrade.id == copy_trade_id)
    )
    ct = result.unique().scalar_one_or_none()

    if not ct:
        await callback.answer("Копитрейд не найден", show_alert=True)
        return
    stmt = await session.execute(
        select(
            CopyTradeTransaction
        ).where(
            CopyTradeTransaction.copy_trade_id == copy_trade_id
        )
    )
    ctt_list = stmt.unique().scalars().all()
    print([ctt.amount_sol for ctt in ctt_list ])
    ct_pnl = Decimal(0)
    if len(ctt_list):
        ct_pnl = sum([
            Decimal(ctt.amount_sol or 0) * (Decimal(1) if ctt.transaction_type == "SELL" else Decimal(-1)) for ctt in ctt_list
        ])
    keyboard = await get_copy_trade_settings_keyboard(ct.id, session)

    await callback.message.edit_text(
        f"⚙️ Настройки Copy Trading\n\n"
        f"📋 Название: {ct.name if ct.name else '(Не задано)'}\n" +
        f"👛 Кошелек: {ct.wallet_address[:6]}...{ct.wallet_address[-4:]}\n" +
        f"📊 Статус: {'Активен ✅' if ct.is_active else 'Неактивен 🔴'}\n\n" +
        (f"{'📉' if ct_pnl < 0 else '📈'} PNL: {_format_price(ct_pnl)} SOL\n\n" if len(ctt_list) else "") +
        "Выберите параметр для настройки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(lambda c: c.data.startswith("ct_edit"))
async def handle_edit_setting(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обработка редактирования настроек"""
    setting = callback.data.split(":")[1]
    copy_trade_id = int(callback.data.split(":")[2])

    # Сохраняем ID копитрейда в состоянии
    await state.update_data(copy_trade_id=copy_trade_id)

    messages = {
        "name": "Введите новое название:",
        "wallet": "Введите новый адрес кошелька:",
        "copy_percentage": "Введите процент копирования (1-100):",
        "min_amount": "Введите минимальную сумму в SOL:",
        "max_amount": "Введите максимальную сумму в SOL (0 для отключения):",
        "total_amount": "Введите общую сумму в SOL (0 для отключения):",
        "max_copies_per_token": "Введите максимальное количество копий токена (0 для отключения):",
        "buy_gas_fee": "Введите Gas Fee для покупки:",
        "sell_gas_fee": "Введите Gas Fee для продажи:",
        "buy_slippage": "Введите Slippage для покупки (%):",
        "sell_slippage": "Введите Slippage для продажи (%):"
    }

    states = {
        "name": CopyTradeStates.ENTER_NAME,
        "wallet": CopyTradeStates.ENTER_ADDRESS,
        "copy_percentage": CopyTradeStates.ENTER_PERCENTAGE,
        "min_amount": CopyTradeStates.ENTER_MIN_AMOUNT,
        "max_amount": CopyTradeStates.ENTER_MAX_AMOUNT,
        "total_amount": CopyTradeStates.ENTER_TOTAL_AMOUNT,
        "max_copies_per_token": CopyTradeStates.ENTER_MAX_COPIES,
        "buy_gas_fee": CopyTradeStates.ENTER_BUY_GAS,
        "sell_gas_fee": CopyTradeStates.ENTER_SELL_GAS,
        "buy_slippage": CopyTradeStates.ENTER_BUY_SLIPPAGE,
        "sell_slippage": CopyTradeStates.ENTER_SELL_SLIPPAGE
    }
    toggled_settings = ['copy_sells', 'is_active', 'anti_mev']

    if setting in toggled_settings:
        result = await session.execute(select(CopyTrade).where(CopyTrade.id == copy_trade_id))
        copy_trade = result.unique().scalar_one_or_none()
        if not copy_trade:
            await callback.answer("Копитрейд не найден", show_alert=True)
            return

        logger.info(f"Handling attribute: {setting}")
        if setting == 'is_active':
            service = CopyTradeService()
            if not getattr(copy_trade, setting):
                logger.info(f"Adding copy trade ID:{copy_trade.id}")
                await service.add_copy_trade(copy_trade, session)
            else: 
                logger.info(f"Removing copy trade ID:{copy_trade.id}")
                await service.remove_copy_trade(copy_trade, session)


        setattr(copy_trade, setting, not getattr(copy_trade, setting))
        await session.commit()
        return await show_copy_settings(callback, session, copy_trade_id)

    if setting in messages:
        await callback.message.answer(
            messages[setting],
            reply_markup=ForceReply(selective=True)
        )
        await state.set_state(states[setting])


def is_in_between(val, left, right, equal_limits=False):
    if equal_limits:
        return left <= val <= right
    return left < val < right


async def handle_copy_trade_settings_edit_base(
        attribute,
        message: types.Message, session: AsyncSession,
        state: FSMContext, retry_action
):
    data = await state.get_data()
    copy_trade_id = data.get('copy_trade_id')
    common_defaults = {
        "type": str,
        "unit": "",
        "min": 0,
        "max": 100,
        "equal_limits": True
    }

    attribute_name_dict = {
        "name": {"name": "Название", "max": 255, "equal_limits": False},
        "wallet": {"name": "Кошелек", "equal_limits": False},
        "copy_percentage": {"type": float, "name": "Процент копирования", "unit": "%"},
        "min_amount": {"type": float, "name": "Минимальная сумма", "unit": "SOL", "max": 10000},
        "max_amount": {"type": float, "name": "Максимальная сумма", "unit": "SOL", "max": 10000},
        "total_amount": {"type": float, "name": "Общая сумма", "unit": "SOL", "max": 10000},
        "max_copies_per_token": {"type": int, "name": "Максимальное количество копий токена", "max": 100000},
        "buy_gas_fee": {"type": float, "name": "Gas Fee для покупки", "unit": "SOL", "equal_limits": False},
        "sell_gas_fee": {"type": float, "name": "Gas Fee для продажи", "unit": "SOL", "equal_limits": False},
        "buy_slippage": {"type": float, "name": "Slippage для покупки", "unit": "%"},
        "sell_slippage": {"type": float, "name": "Slippage для продажи", "unit": "%"},
    }

    # Добавляем общие параметры к каждому атрибуту
    compact_attributes = {
        key: {**common_defaults, **values}
        for key, values in attribute_name_dict.items()
    }
    attribute_name = attribute
    try:
        # Получаем пользователя
        user_id = message.from_user.id
        user_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = user_res.unique().scalar_one_or_none()
        if not user:
            await message.reply("❌ Пользователь не найден")
            return
        # Получаем текущие настройки
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        item = result.unique().scalar_one_or_none()
        if not item \
                or attribute not in compact_attributes \
                or item.user_id != user.id:
            await message.reply("❌ Настройки не найдены")
            return
        attribute_info = compact_attributes.get(attribute)
        # Получаем значение из сообщения
        value = message.text.strip().replace("%", "").replace(",", ".")
        attribute_type = attribute_info.get('type')
        attribute_name = attribute_info.get('name')
        attribute_unit = attribute_info.get('unit')
        attr_min = attribute_info.get('min')
        attr_max = attribute_info.get('max')
        attr_equal_limits = attribute_info.get('equal_limits')
        # Проверяем
        try:
            value = attribute_type(value)
            if attribute_type is str and \
                    (len(value) > attr_max or len(value) < attr_min):
                raise ValueError
            if attribute_type in (int, float) \
                    and not is_in_between(value, attr_min, attr_max, attr_equal_limits):
                raise ValueError
        except ValueError:
            await message.reply(
                f"❌ Пожалуйста, введите корректное значение для {attribute_name} "
                + f"({'строку длиной c ' + str(attr_min) + ' до ' + str(attr_max) + 'символов' if attribute_type is str else f'{attr_min} - {attr_max}'})",
                reply_markup=ForceReply(selective=True))
            await state.set_state(retry_action)
            return
        if attribute in ["buy_gas_fee", "sell_gas_fee"]:
            value *= 1e9
        logger.info(f"Handling attribute: {attribute}")
        if attribute in ('copy_sells', 'is_active', 'anti_mev'):
            setattr(item, attribute, not getattr(item, attribute))
        else:
            setattr(item, attribute, value)
        await session.commit()

        # Отправляем подтверждение
        await message.reply(f"✅ {attribute_name} установлено: {value}{attribute_unit}")

        # Показываем обновленное меню настроек
        keyboard = await get_copy_trade_settings_keyboard(copy_trade_id, session)
        stmt = await session.execute(
            select(
                CopyTradeTransaction
            ).where(
                CopyTradeTransaction.copy_trade_id == copy_trade_id
            )
        )
        ctt_list = stmt.unique().scalars().all()
        ct_pnl = Decimal(0)
        if len(ctt_list):
            ct_pnl = sum([
                Decimal(ctt.amount_sol or 0) * (Decimal(1) if ctt.transaction_type == "SELL" else Decimal(-1)) for ctt in ctt_list
            ])
        await message.answer(
            f"⚙️ Настройки Copy Trading\n\n" +
            f"📋 Название: {item.name if item.name else '(Не задано)'}\n" +
            f"👛 Кошелек: {item.wallet_address[:6]}...{item.wallet_address[-4:]}\n" +
            f"📊 Статус: {'Активен ✅' if item.is_active else 'Неактивен 🔴'}\n\n" +
            (f"{'📉' if ct_pnl < 0 else '📈'} PNL: {_format_price(ct_pnl)} SOL\n\n" if len(ctt_list) else "") +
            "Выберите параметр для настройки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

    except Exception as e:
        logger.error(f"Error handling {copy_trade_id} {attribute}: {e}")
        traceback.print_exc()
        await message.reply(
            f"❌ Произошла ошибка при установке {attribute_name}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ct_settings:{copy_trade_id}")]
            ])
        )


@router.message(CopyTradeStates.ENTER_NAME, flags={"priority": 5})
async def handle_ct_name(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="name",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_NAME
    )


@router.message(CopyTradeStates.ENTER_PERCENTAGE, flags={"priority": 5})
async def handle_ct_copy_percentage(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="copy_percentage",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_PERCENTAGE
    )


@router.message(CopyTradeStates.ENTER_MIN_AMOUNT, flags={"priority": 5})
async def handle_ct_min_amount(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="min_amount",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_MIN_AMOUNT
    )


@router.message(CopyTradeStates.ENTER_MAX_AMOUNT, flags={"priority": 5})
async def handle_ct_max_amount(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="max_amount",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_MAX_AMOUNT
    )


@router.message(CopyTradeStates.ENTER_TOTAL_AMOUNT, flags={"priority": 5})
async def handle_ct_total_amount(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="total_amount",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_TOTAL_AMOUNT
    )


@router.message(CopyTradeStates.ENTER_MAX_COPIES, flags={"priority": 5})
async def handle_ct_max_copies_per_token(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="max_copies_per_token",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_MAX_COPIES
    )


@router.message(CopyTradeStates.ENTER_BUY_GAS, flags={"priority": 5})
async def handle_ct_buy_gas_fee(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="buy_gas_fee",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_BUY_GAS
    )


@router.message(CopyTradeStates.ENTER_SELL_GAS, flags={"priority": 5})
async def handle_ct_sell_gas_fee(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="sell_gas_fee",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_SELL_GAS
    )


@router.message(CopyTradeStates.ENTER_BUY_SLIPPAGE, flags={"priority": 5})
async def handle_ct_buy_slippage(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="buy_slippage",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_BUY_SLIPPAGE
    )


@router.message(CopyTradeStates.ENTER_SELL_SLIPPAGE, flags={"priority": 5})
async def handle_ct_sell_slippage(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    return await handle_copy_trade_settings_edit_base(
        attribute="sell_slippage",
        message=message,
        session=session,
        state=state,
        retry_action=CopyTradeStates.ENTER_SELL_SLIPPAGE
    )


@router.callback_query(lambda c: c.data.startswith("ct_delete:"))
async def handle_delete_copy_trade(callback: CallbackQuery, session: AsyncSession):
    """Обработка удаления копитрейда"""
    copy_trade_id = int(callback.data.split(":")[1])

    result = await session.execute(
        select(CopyTrade).where(CopyTrade.id == copy_trade_id)
    )
    ct = result.scalar_one_or_none()

    if not ct:
        await callback.answer("Копитрейд не найден", show_alert=True)
        return

    # Удаляем из сервиса перед удалением из базы
    service = CopyTradeService()
    await service.remove_copy_trade(ct, session)

    await session.delete(ct)
    await session.commit()

    await callback.answer("Копитрейд удален")
    await show_copy_trade_menu(callback, session)


@router.callback_query(F.data == "ct_add_excluded")
async def start_add_excluded_token(callback: CallbackQuery, state: FSMContext):
    """Начать процесс добавления исключенного токена"""
    await callback.message.answer(
        "Введите адрес токена, который нужно исключить:",
        reply_markup=ForceReply(selective=True)
    )
    await state.set_state(CopyTradeStates.ENTER_EXCLUDED_TOKEN)


@router.message(CopyTradeStates.ENTER_EXCLUDED_TOKEN)
async def handle_excluded_token_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода адреса исключаемого токена"""
    token_address = message.text.strip()

    if len(token_address) != 44:
        await message.reply(
            "❌ Неверный формат адреса. Адрес должен состоять из 44 символов.",
            reply_markup=ForceReply(selective=True)
        )
        return
    telegram_id = get_real_user_id(message)
    stmt = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
    )
    user = stmt.unique().scalar_one_or_none()
    if not user:
        await message.reply(
            "❌ Пользователь не найден, создайте его через команду /start.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="ct_exclude_tokens")]
            ])
        )
        return
    # Проверяем, не исключен ли уже этот токен
    exists = await session.scalar(
        select(ExcludedToken)
        .where(ExcludedToken.user_id == user.id)
        .where(ExcludedToken.token_address == token_address)
    )

    if exists:
        await message.reply(
            "❌ Этот токен уже исключен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="ct_exclude_tokens")]
            ])
        )
        return

    # Добавляем токен в исключения
    new_excluded = ExcludedToken(
        user_id=user.id,
        token_address=token_address
    )

    session.add(new_excluded)
    await session.commit()

    await state.clear()
    success_message = await message.reply(f"✅ {token_address[:6]}...{token_address[-4:]} исключен из списка токенов.")

    fake_callback_query = CallbackQuery(
        id=str(uuid.uuid4())[:8],                           # любое уникальное значение
        from_user=message.from_user,            # в aiogram 3 поле называется from_user
        message=success_message,
        chat_instance=str(message.chat.id),     # или любой другой осмысленный текст
        data="ct_exclude_tokens"                # именно data=..., а не callback_data=...
    )
    await show_excluded_tokens(
        callback=fake_callback_query,
        session=session
    )


@router.callback_query(lambda c: c.data.startswith("ct_remove_excluded:"))
async def handle_remove_excluded_token(callback: CallbackQuery, session: AsyncSession):
    """Обработка удаления исключенного токена"""
    token_id = int(callback.data.split(":")[1])

    telegram_id = get_real_user_id(callback)
    stmt = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
    )
    user = stmt.unique().scalar_one_or_none()
    if not user:
        await callback.answer(
            "❌ Пользователь не найден, создайте его через команду /start.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="ct_exclude_tokens")]
            ])
        )
        return
    result = await session.execute(
        select(ExcludedToken)
        .where(
            ExcludedToken.id == token_id,
            ExcludedToken.user_id == user.id
        )
    )
    token = result.scalar_one_or_none()

    if not token:
        await callback.answer("Токен не найден", show_alert=True)
        return

    await session.delete(token)
    await session.commit()

    await callback.answer("Токен удален из исключений")
    await show_excluded_tokens(callback, session)


@router.callback_query(F.data == "ct_exclude_tokens")
async def show_excluded_tokens(callback: CallbackQuery, session: AsyncSession):
    """Показать меню исключенных токенов"""

    telegram_id = get_real_user_id(callback)
    stmt = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
    )
    user = stmt.unique().scalar_one_or_none()
    if not user:
        await callback.answer(
            "❌ Пользователь не найден, создайте его через команду /start.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="ct_exclude_tokens")]
            ])
        )
        return
    result = await session.execute(
        select(ExcludedToken)
        .where(ExcludedToken.user_id == user.id)
        .order_by(ExcludedToken.created_at)
    )
    excluded_tokens = result.unique().scalars().all()

    keyboard = []

    # Добавляем исключенные токены
    for token in excluded_tokens:
        keyboard.append([
            InlineKeyboardButton(
                text=f"❌ {token.token_address[:6]}...{token.token_address[-4:]}",
                callback_data=f"ct_remove_excluded:{token.id}"
            )
        ])

    # Добавляем кнопки управления
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Добавить токен", callback_data="ct_add_excluded")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="copy_trade")]
    ])

    await callback.message.edit_text(
        "🚫 Исключенные токены\n\n"
        "Эти токены не будут копироваться:\n"
        "Нажмите на токен, чтобы удалить его из списка.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.message(CopyTradeStates.ENTER_ADDRESS)
async def handle_address_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода адреса кошелька"""
    address = message.text.strip()

    if len(address) != 44:
        await state.set_state(CopyTradeStates.ENTER_ADDRESS)
        await message.reply(
            "❌ Неверный формат адреса. Адрес должен состоять из 44 символов.",
            reply_markup=ForceReply(selective=True)
        )
        return

    # Проверяем существование пользователя
    user = await session.scalar(
        select(User).where(User.telegram_id == message.from_user.id)
    )

    if not user:
        await message.reply(
            "❌ Пользователь не найден. Пожалуйста, используйте /start для создания аккаунта.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="copy_trade")]
            ])
        )
        return
    if user.solana_wallet == address:
        await state.set_state(CopyTradeStates.ENTER_ADDRESS)
        await message.reply(
            "❌ Нельзя отслеживать свои же кошелек.",
            reply_markup=ForceReply(selective=True)
        )
        return

    # Проверяем, не отслеживается ли уже этот адрес
    exists = await session.scalar(
        select(CopyTrade)
        .where(CopyTrade.user_id == user.id)
        .where(CopyTrade.wallet_address == address)
    )

    if exists:
        await message.reply(
            "❌ Этот адрес уже отслеживается введите другой адрес.",
            reply_markup=ForceReply(selective=True)
        )
        return

    await state.update_data(wallet_address=address)

    # Создаем новый копитрейд с дефолтными настройками
    data = await state.get_data()
    new_copy_trade = CopyTrade(
        user_id=user.id,
        wallet_address=address,
        is_active=True,
        copy_percentage=100.0,
        min_amount=0.0,
        max_amount=None,
        total_amount=None,
        max_copies_per_token=None,
        copy_sells=True,
        retry_count=1,
        buy_gas_fee=100000,
        sell_gas_fee=100000,
        buy_slippage=1.0,
        sell_slippage=1.0,
        anti_mev=False
    )

    session.add(new_copy_trade)
    await session.commit()
    await session.refresh(new_copy_trade)

    # Добавляем копитрейд в сервис
    service = CopyTradeService()
    await service.add_copy_trade(new_copy_trade, session)

    # Показываем настройки нового копитрейда
    keyboard = await get_copy_trade_settings_keyboard(new_copy_trade.id, session)

    await message.reply(
        f"⚙️ Настройки Copy Trading\n\n" +
        f"📋 Название: {new_copy_trade.name if new_copy_trade.name else '(Не задано)'} \n" +
        f"👛 Кошелек: {new_copy_trade.wallet_address[:6]}...{new_copy_trade.wallet_address[-4:]}\n" +
        f"📊 Статус: {'Активен ✅' if new_copy_trade.is_active else 'Неактивен 🔴'}\n\n" +
        "Выберите параметр для настройки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await state.clear()
