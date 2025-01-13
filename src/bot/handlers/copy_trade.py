import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.models import CopyTrade, ExcludedToken, User
from ..services.copy_trade_service import CopyTradeService

router = Router()
logger = logging.getLogger(__name__)

class CopyTradeStates(StatesGroup):
    ENTER_NAME = State()
    ENTER_ADDRESS = State()
    ENTER_PERCENTAGE = State()
    ENTER_MIN_AMOUNT = State()
    ENTER_MAX_AMOUNT = State()
    ENTER_TOTAL_AMOUNT = State()
    ENTER_MAX_COPIES = State()
    ENTER_BUY_GAS = State()
    ENTER_SELL_GAS = State()
    ENTER_BUY_SLIPPAGE = State()
    ENTER_SELL_SLIPPAGE = State()
    ENTER_EXCLUDED_TOKEN = State()

@router.callback_query(F.data == "copy_trade")
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
                text=f"{status} {ct.name} ({ct.wallet_address[:6]}...)",
                callback_data=f"ct_settings:{ct.id}"
            )
        ])
    
    # Добавляем кнопки управления
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Добавить", callback_data="ct_add")],
        [InlineKeyboardButton(text="🚫 Исключить токены", callback_data="ct_exclude_tokens")],
        [InlineKeyboardButton(text="« Назад", callback_data="main_menu")]
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
    
    keyboard = [[
        InlineKeyboardButton(text="« Отмена", callback_data="copy_trade")
    ]]
    
    await callback.message.edit_text(
        "Введите название для нового копитрейда:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await state.set_state(CopyTradeStates.ENTER_NAME)

@router.callback_query(lambda c: c.data.startswith("ct_settings:"))
async def show_copy_settings(callback: CallbackQuery, session: AsyncSession):
    """Показать настройки конкретного копитрейда"""
    copy_trade_id = int(callback.data.split(":")[1])
    
    result = await session.execute(
        select(CopyTrade).where(CopyTrade.id == copy_trade_id)
    )
    ct = result.scalar_one_or_none()
    
    if not ct:
        await callback.answer("Копитрейд не найден", show_alert=True)
        return
    
    keyboard = [
        # Основные настройки
        [InlineKeyboardButton(text="📝 Название", callback_data=f"ct_edit_name:{ct.id}")],
        [InlineKeyboardButton(text="👛 Адрес кошелька", callback_data=f"ct_edit_wallet:{ct.id}")],
        
        # Настройки копирования
        [InlineKeyboardButton(text=f"📊 Процент копирования: {ct.copy_percentage}%", 
                            callback_data=f"ct_edit_percentage:{ct.id}")],
        [InlineKeyboardButton(text=f"📉 Мин. сумма: {ct.min_amount} SOL", 
                            callback_data=f"ct_edit_min:{ct.id}")],
        [InlineKeyboardButton(text=f"📈 Макс. сумма: {ct.max_amount or 'Без лимита'} SOL", 
                            callback_data=f"ct_edit_max:{ct.id}")],
        [InlineKeyboardButton(text=f"💰 Общая сумма: {ct.total_amount or 'Без лимита'} SOL", 
                            callback_data=f"ct_edit_total:{ct.id}")],
        
        # Настройки транзакций
        [InlineKeyboardButton(text=f"🔄 Макс. копий токена: {ct.max_copies_per_token or 'Без лимита'}", 
                            callback_data=f"ct_edit_copies:{ct.id}")],
        [InlineKeyboardButton(text=f"⚡️ Buy Gas: {ct.buy_gas_fee}", 
                            callback_data=f"ct_edit_buy_gas:{ct.id}")],
        [InlineKeyboardButton(text=f"⚡️ Sell Gas: {ct.sell_gas_fee}", 
                            callback_data=f"ct_edit_sell_gas:{ct.id}")],
        [InlineKeyboardButton(text=f"📊 Buy Slippage: {ct.buy_slippage}%", 
                            callback_data=f"ct_edit_buy_slip:{ct.id}")],
        [InlineKeyboardButton(text=f"📊 Sell Slippage: {ct.sell_slippage}%", 
                            callback_data=f"ct_edit_sell_slip:{ct.id}")],
        
        # Дополнительные настройки
        [InlineKeyboardButton(text=f"{'✅' if ct.copy_sells else '❌'} Копировать продажи", 
                            callback_data=f"ct_toggle_sells:{ct.id}")],
        [InlineKeyboardButton(text=f"{'✅' if ct.anti_mev else '❌'} Anti-MEV", 
                            callback_data=f"ct_toggle_mev:{ct.id}")],
        
        # Управление
        [InlineKeyboardButton(
            text=f"{'✅' if ct.is_active else '🔴'} Активный", 
            callback_data=f"ct_toggle_active:{ct.id}"
        )],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"ct_delete:{ct.id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="copy_trade")]
    ]
    
    await callback.message.edit_text(
        f"⚙️ Настройки Copy Trading\n\n"
        f"📋 Название: {ct.name}\n"
        f"👛 Кошелек: {ct.wallet_address[:6]}...{ct.wallet_address[-4:]}\n"
        f"📊 Статус: {'Активен ✅' if ct.is_active else 'Неактивен 🔴'}\n\n"
        "Выберите параметр для настройки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(F.data == "ct_exclude_tokens")
async def show_excluded_tokens(callback: CallbackQuery, session: AsyncSession):
    """Показать меню исключенных токенов"""
    user_id = callback.from_user.id
    
    result = await session.execute(
        select(ExcludedToken)
        .where(ExcludedToken.user_id == user_id)
        .order_by(ExcludedToken.created_at)
    )
    excluded_tokens = result.scalars().all()
    
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
        [InlineKeyboardButton(text="« Назад", callback_data="copy_trade")]
    ])
    
    await callback.message.edit_text(
        "🚫 Исключенные токены\n\n"
        "Эти токены не будут копироваться:\n"
        "Нажмите на токен, чтобы удалить его из списка.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    ) 

@router.message(CopyTradeStates.ENTER_NAME)
async def handle_name_input(message: Message, state: FSMContext):
    """Обработка ввода названия копитрейда"""
    name = message.text.strip()
    
    if len(name) > 32:
        await message.reply(
            "❌ Название слишком длинное. Максимум 32 символа.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Отмена", callback_data="copy_trade")]
            ])
        )
        return
    
    await state.update_data(name=name)
    await message.reply(
        "Введите адрес кошелька для отслеживания:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data="copy_trade")]
        ])
    )
    await state.set_state(CopyTradeStates.ENTER_ADDRESS)

@router.message(CopyTradeStates.ENTER_ADDRESS)
async def handle_address_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода адреса кошелька"""
    address = message.text.strip()
    
    if len(address) != 44:
        await message.reply(
            "❌ Неверный формат адреса. Адрес должен состоять из 44 символов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Отмена", callback_data="copy_trade")]
            ])
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
                [InlineKeyboardButton(text="« Отмена", callback_data="copy_trade")]
            ])
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
            "❌ Этот адрес уже отслеживается.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Отмена", callback_data="copy_trade")]
            ])
        )
        return
    
    await state.update_data(wallet_address=address)
    
    # Создаем новый копитрейд с дефолтными настройками
    data = await state.get_data()
    new_copy_trade = CopyTrade(
        user_id=user.id,
        name=data['name'],
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
    await service.add_copy_trade(new_copy_trade)
    
    # Показываем настройки нового копитрейда
    keyboard = [
        # Основные настройки
        [InlineKeyboardButton(text="📝 Название", callback_data=f"ct_edit_name:{new_copy_trade.id}")],
        [InlineKeyboardButton(text="👛 Адрес кошелька", callback_data=f"ct_edit_wallet:{new_copy_trade.id}")],
        
        # Настройки копирования
        [InlineKeyboardButton(text=f"📊 Процент копирования: {new_copy_trade.copy_percentage}%", 
                            callback_data=f"ct_edit_percentage:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"📉 Мин. сумма: {new_copy_trade.min_amount} SOL", 
                            callback_data=f"ct_edit_min:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"📈 Макс. сумма: {new_copy_trade.max_amount or 'Без лимита'} SOL", 
                            callback_data=f"ct_edit_max:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"💰 Общая сумма: {new_copy_trade.total_amount or 'Без лимита'} SOL", 
                            callback_data=f"ct_edit_total:{new_copy_trade.id}")],
        
        # Настройки транзакций
        [InlineKeyboardButton(text=f"🔄 Макс. копий токена: {new_copy_trade.max_copies_per_token or 'Без лимита'}", 
                            callback_data=f"ct_edit_copies:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"⚡️ Buy Gas: {new_copy_trade.buy_gas_fee}", 
                            callback_data=f"ct_edit_buy_gas:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"⚡️ Sell Gas: {new_copy_trade.sell_gas_fee}", 
                            callback_data=f"ct_edit_sell_gas:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"📊 Buy Slippage: {new_copy_trade.buy_slippage}%", 
                            callback_data=f"ct_edit_buy_slip:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"📊 Sell Slippage: {new_copy_trade.sell_slippage}%", 
                            callback_data=f"ct_edit_sell_slip:{new_copy_trade.id}")],
        
        # Дополнительные настройки
        [InlineKeyboardButton(text=f"{'✅' if new_copy_trade.copy_sells else '❌'} Копировать продажи", 
                            callback_data=f"ct_toggle_sells:{new_copy_trade.id}")],
        [InlineKeyboardButton(text=f"{'✅' if new_copy_trade.anti_mev else '❌'} Anti-MEV", 
                            callback_data=f"ct_toggle_mev:{new_copy_trade.id}")],
        
        # Управление
        [InlineKeyboardButton(
            text=f"{'✅' if new_copy_trade.is_active else '🔴'} Активный", 
            callback_data=f"ct_toggle_active:{new_copy_trade.id}"
        )],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"ct_delete:{new_copy_trade.id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="copy_trade")]
    ]
    
    await message.reply(
        f"⚙️ Настройки Copy Trading\n\n"
        f"📋 Название: {new_copy_trade.name}\n"
        f"👛 Кошелек: {new_copy_trade.wallet_address[:6]}...{new_copy_trade.wallet_address[-4:]}\n"
        f"📊 Статус: {'Активен ✅' if new_copy_trade.is_active else 'Неактивен 🔴'}\n\n"
        "Выберите параметр для настройки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await state.clear()

@router.callback_query(lambda c: c.data.startswith("ct_edit_"))
async def handle_edit_setting(callback: CallbackQuery, state: FSMContext):
    """Обработка редактирования настроек"""
    setting = callback.data.split("_")[2].split(":")[0]
    copy_trade_id = int(callback.data.split(":")[1])
    
    # Сохраняем ID копитрейда в состоянии
    await state.update_data(copy_trade_id=copy_trade_id)
    
    messages = {
        "name": "Введите новое название:",
        "wallet": "Введите новый адрес кошелька:",
        "percentage": "Введите процент копирования (1-100):",
        "min": "Введите минимальную сумму в SOL:",
        "max": "Введите максимальную сумму в SOL (0 для отключения):",
        "total": "Введите общую сумму в SOL (0 для отключения):",
        "copies": "Введите максимальное количество копий токена (0 для отключения):",
        "buy_gas": "Введите Gas Fee для покупки:",
        "sell_gas": "Введите Gas Fee для продажи:",
        "buy_slip": "Введите Slippage для покупки (%):",
        "sell_slip": "Введите Slippage для продажи (%):"
    }
    
    states = {
        "name": CopyTradeStates.ENTER_NAME,
        "wallet": CopyTradeStates.ENTER_ADDRESS,
        "percentage": CopyTradeStates.ENTER_PERCENTAGE,
        "min": CopyTradeStates.ENTER_MIN_AMOUNT,
        "max": CopyTradeStates.ENTER_MAX_AMOUNT,
        "total": CopyTradeStates.ENTER_TOTAL_AMOUNT,
        "copies": CopyTradeStates.ENTER_MAX_COPIES,
        "buy_gas": CopyTradeStates.ENTER_BUY_GAS,
        "sell_gas": CopyTradeStates.ENTER_SELL_GAS,
        "buy_slip": CopyTradeStates.ENTER_BUY_SLIPPAGE,
        "sell_slip": CopyTradeStates.ENTER_SELL_SLIPPAGE
    }
    
    if setting in messages:
        await callback.message.edit_text(
            messages[setting],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{copy_trade_id}"
                )]
            ])
        )
        await state.set_state(states[setting])

@router.callback_query(lambda c: c.data.startswith("ct_toggle_"))
async def handle_toggle_setting(callback: CallbackQuery, session: AsyncSession):
    """Обработка переключения настроек"""
    setting = callback.data.split("_")[2].split(":")[0]
    copy_trade_id = int(callback.data.split(":")[1])
    
    result = await session.execute(
        select(CopyTrade).where(CopyTrade.id == copy_trade_id)
    )
    ct = result.scalar_one_or_none()
    
    if not ct:
        await callback.answer("Копитрейд не найден", show_alert=True)
        return
    
    if setting == "active":
        ct.is_active = not ct.is_active
        message = f"Копитрейд {'активирован' if ct.is_active else 'деактивирован'}"
    elif setting == "sells":
        ct.copy_sells = not ct.copy_sells
        message = f"Копирование продаж {'включено' if ct.copy_sells else 'отключено'}"
    elif setting == "mev":
        ct.anti_mev = not ct.anti_mev
        message = f"Anti-MEV {'включен' if ct.anti_mev else 'отключен'}"
    
    await session.commit()
    await callback.answer(message)
    await show_copy_settings(callback, session)

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
    await service.remove_copy_trade(ct)
    
    await session.delete(ct)
    await session.commit()
    
    await callback.answer("Копитрейд удален")
    await show_copy_trade_menu(callback, session)

@router.callback_query(F.data == "ct_add_excluded")
async def start_add_excluded_token(callback: CallbackQuery, state: FSMContext):
    """Начать процесс добавления исключенного токена"""
    await callback.message.edit_text(
        "Введите адрес токена, который нужно исключить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data="ct_exclude_tokens")]
        ])
    )
    await state.set_state(CopyTradeStates.ENTER_EXCLUDED_TOKEN)

@router.message(CopyTradeStates.ENTER_EXCLUDED_TOKEN)
async def handle_excluded_token_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода адреса исключаемого токена"""
    token_address = message.text.strip()
    
    if len(token_address) != 44:
        await message.reply(
            "❌ Неверный формат адреса. Адрес должен состоять из 44 символов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Отмена", callback_data="ct_exclude_tokens")]
            ])
        )
        return
    
    # Проверяем, не исключен ли уже этот токен
    exists = await session.scalar(
        select(ExcludedToken)
        .where(ExcludedToken.user_id == message.from_user.id)
        .where(ExcludedToken.token_address == token_address)
    )
    
    if exists:
        await message.reply(
            "❌ Этот токен уже исключен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Отмена", callback_data="ct_exclude_tokens")]
            ])
        )
        return
    
    # Добавляем токен в исключения
    new_excluded = ExcludedToken(
        user_id=message.from_user.id,
        token_address=token_address
    )
    
    session.add(new_excluded)
    await session.commit()
    
    await state.clear()
    await show_excluded_tokens(
        callback=CallbackQuery(message=message, data="ct_exclude_tokens"),
        session=session
    )

@router.callback_query(lambda c: c.data.startswith("ct_remove_excluded:"))
async def handle_remove_excluded_token(callback: CallbackQuery, session: AsyncSession):
    """Обработка удаления исключенного токена"""
    token_id = int(callback.data.split(":")[1])
    
    result = await session.execute(
        select(ExcludedToken).where(ExcludedToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    
    if not token:
        await callback.answer("Токен не найден", show_alert=True)
        return
    
    await session.delete(token)
    await session.commit()
    
    await callback.answer("Токен удален из исключений")
    await show_excluded_tokens(callback, session)

@router.message(CopyTradeStates.ENTER_PERCENTAGE)
async def handle_percentage_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода процента копирования"""
    try:
        percentage = float(message.text.replace(",", "."))
        if percentage <= 0 or percentage > 100:
            raise ValueError("Invalid percentage")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.copy_percentage = percentage
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_MIN_AMOUNT)
async def handle_min_amount_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода минимальной суммы"""
    try:
        amount = float(message.text.replace(",", "."))
        if amount < 0:
            raise ValueError("Invalid amount")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.min_amount = amount
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите положительное число:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_MAX_AMOUNT)
async def handle_max_amount_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода максимальной суммы"""
    try:
        amount = float(message.text.replace(",", "."))
        if amount < 0:
            raise ValueError("Invalid amount")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.max_amount = amount if amount > 0 else None
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите положительное число (0 для отключения):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_TOTAL_AMOUNT)
async def handle_total_amount_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода общей суммы"""
    try:
        amount = float(message.text.replace(",", "."))
        if amount < 0:
            raise ValueError("Invalid amount")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.total_amount = amount if amount > 0 else None
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите положительное число (0 для отключения):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_MAX_COPIES)
async def handle_max_copies_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода максимального количества копий"""
    try:
        copies = int(message.text)
        if copies < 0:
            raise ValueError("Invalid copies")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.max_copies_per_token = copies if copies > 0 else None
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите целое положительное число (0 для отключения):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_BUY_GAS)
async def handle_buy_gas_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода Gas Fee для покупки"""
    try:
        gas = int(message.text)
        if gas <= 0:
            raise ValueError("Invalid gas")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.buy_gas_fee = gas
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите положительное целое число:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_SELL_GAS)
async def handle_sell_gas_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода Gas Fee для продажи"""
    try:
        gas = int(message.text)
        if gas <= 0:
            raise ValueError("Invalid gas")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.sell_gas_fee = gas
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите положительное целое число:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_BUY_SLIPPAGE)
async def handle_buy_slippage_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода Slippage для покупки"""
    try:
        slippage = float(message.text.replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.buy_slippage = slippage
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.message(CopyTradeStates.ENTER_SELL_SLIPPAGE)
async def handle_sell_slippage_input(message: Message, state: FSMContext, session: AsyncSession):
    """Обработка ввода Slippage для продажи"""
    try:
        slippage = float(message.text.replace(",", "."))
        if slippage <= 0 or slippage > 100:
            raise ValueError("Invalid slippage")
        
        data = await state.get_data()
        copy_trade_id = data["copy_trade_id"]
        
        result = await session.execute(
            select(CopyTrade).where(CopyTrade.id == copy_trade_id)
        )
        ct = result.scalar_one_or_none()
        
        if not ct:
            await message.reply("❌ Копитрейд не найден")
            return
        
        ct.sell_slippage = slippage
        await session.commit()
        
        await state.clear()
        await show_copy_settings(
            callback=CallbackQuery(message=message, data=f"ct_settings:{copy_trade_id}"),
            session=session
        )
        
    except ValueError:
        await message.reply(
            "❌ Неверное значение. Введите число от 0.1 до 100:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="« Отмена", 
                    callback_data=f"ct_settings:{data['copy_trade_id']}"
                )]
            ])
        )

@router.callback_query(lambda c: c.data.startswith("ct_toggle_active:"))
async def handle_toggle_active(callback: CallbackQuery, session: AsyncSession):
    """Обработка включения/выключения копитрейда"""
    copy_trade_id = int(callback.data.split(":")[1])
    
    result = await session.execute(
        select(CopyTrade).where(CopyTrade.id == copy_trade_id)
    )
    ct = result.scalar_one_or_none()
    
    if not ct:
        await callback.answer("Копитрейд не найден", show_alert=True)
        return
    
    ct.is_active = not ct.is_active
    
    # Обновляем статус в сервисе
    service = CopyTradeService()
    await service.toggle_copy_trade(ct, session)
    
    message = f"Копитрейд {'активирован' if ct.is_active else 'деактивирован'}"
    await callback.answer(message)
    await show_copy_settings(callback, session) 