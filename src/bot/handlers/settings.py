import traceback
from typing import Union

from aiogram import types
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.crud import get_user_settings, update_user_setting, get_user_setting, create_initial_user_settings
import logging

from src.bot.states import BuySettingStates, SellSettingStates
from src.bot.utils import get_real_user_id
from src.database import User

router = Router()

logger = logging.getLogger(__name__)


@router.callback_query(F.data == "settings_menu", flags={"priority": 3})
async def show_settings_menu(update: Union[types.Message, types.CallbackQuery], session: AsyncSession):
    """Отображение главного меню настроек с данными из базы"""
    try:
        # Определяем тип объекта и получаем нужные атрибуты
        if isinstance(update, types.Message):
            message = update
            user_id = update.from_user.id
        else:  # CallbackQuery
            message = update.message
            user_id = update.from_user.id

        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            logger.warning(f"No user found for ID {user_id}")
            if isinstance(update, types.Message):
                await message.reply(
                    "❌ Кошелек не найден. Используйте /start для создания.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                    ])
                )
            else:
                await message.edit_text(
                    "❌ Кошелек не найден. Используйте /start для создания.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                    ])
                )
            return

        settings_dict = await get_user_settings(user_id, session)
        if not settings_dict:
            await create_initial_user_settings(user_id, session)
            settings_dict = await get_user_settings(user_id, session)

        # Формируем текст меню
        menu_text = (
            "⚙️ Настройки\n\n"
            "Выберите настройку для изменения:"
        )
        buy_settings_keyboard = []
        if 'buy' in settings_dict:
            buy_settings_keyboard = [
                InlineKeyboardButton(
                    text=f"🚀 Покупка: Gas fee ({settings_dict['buy']['gas_fee']})",
                    callback_data="edit_buy_gasfee"
                ),
                InlineKeyboardButton(
                    text=f"⚙️ Покупка: Slippage ({settings_dict['buy']['slippage']}%)",
                    callback_data="edit_buy_slippage"
                )
            ]

        sell_settings_keyboard = []
        if 'sell' in settings_dict:
            sell_settings_keyboard = [
                InlineKeyboardButton(text=f"🚀 Продажа: Gas fee ({settings_dict['sell']['gas_fee']})",
                                     callback_data="edit_sell_gasfee"),
                InlineKeyboardButton(text=f"⚙️ Продажа: Slippage ({settings_dict['sell']['slippage']}%)",
                                     callback_data="edit_sell_slippage")
            ]

        # Создаем список кнопок, распределяя их по строкам
        buttonRows = []
        max_len = max(len(buy_settings_keyboard), len(sell_settings_keyboard))
        for i in range(max_len):
            row = []
            if i < len(buy_settings_keyboard):
                row.append(buy_settings_keyboard[i])
            if i < len(sell_settings_keyboard):
                row.append(sell_settings_keyboard[i])
            buttonRows.append(row)

        # Определение состояния Anti MEV
        anti_mev_text = '🟢 Anti MEV' if settings_dict.get('anti_mev', False) else '🔴 Anti MEV'
        anti_mev_button = InlineKeyboardButton(text=anti_mev_text, callback_data="edit_antimev")

        # Формирование клавиатуры
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=
            buttonRows +  # Добавляем кнопки покупки и продажи
            [
                [anti_mev_button],  # Кнопка Anti MEV
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="main_menu"
                    )
                ]
            ]
        )

        # Отправляем или редактируем сообщение в зависимости от типа объекта
        if isinstance(update, types.Message):
            await message.answer(menu_text, reply_markup=keyboard)
        else:  # CallbackQuery
            await message.edit_text(menu_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error showing settings menu: {e}")
        traceback.print_exc()

        if isinstance(update, types.Message):
            await update.reply(
                "❌ Произошла ошибка при загрузке меню настроек",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
        else:  # CallbackQuery
            await update.message.edit_text(
                "❌ Произошла ошибка при загрузке меню настроек",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )


@router.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_setting(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обработка редактирования настроек"""
    try:
        params = callback_query.data.split("_")
        setting_type = params[1]
        attribute = params[2] if len(params) > 2 else None
        user_id = get_real_user_id(callback_query)
        action = None
        example = ""
        setting_name = ""
        print(setting_type, attribute)
        if attribute and setting_type in ('buy', 'sell'):
            if attribute == 'slippage':
                attribute = "Slippage"
                example = "15"
            elif attribute == 'gasfee':
                attribute = "Gas Fee"
                example = "10000"
            if setting_type == "buy":
                setting_name = "Покупки"
            else:
                setting_name = 'Продажи'

            await callback_query.message.edit_text(
                f"⚙️ Редактирование настроек {setting_name}\n\n"
                f"Введите новое значение для {attribute} (например, {example}):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")]
                ])
            )
            if setting_type == "buy":
                if attribute == "Slippage":
                    await state.set_state(BuySettingStates.waiting_for_slippage)
                    logger.info("BuySettingStates.waiting_for_slippage")
                    return
                elif attribute == "Gas Fee":
                    if attribute == "Gas Fee":
                        await state.set_state(BuySettingStates.waiting_for_gas_fee)
                        await state.update_data(callback_query=callback_query)
                        logger.info("BuySettingStates.waiting_for_gas_fee")
                        return
            else:
                if attribute == "Slippage":
                    await state.set_state(SellSettingStates.waiting_for_slippage)
                    logger.info("SellSettingStates.waiting_for_slippage")
                    return
                elif attribute == "Gas Fee":
                    await state.set_state(SellSettingStates.waiting_for_gas_fee)
                    logger.info("SellSettingStates.waiting_for_gas_fee ")
                    return

        elif setting_type == "antimev":
            # Включить/выключить Anti MEV
            current_value = await get_user_setting(user_id, "anti_mev", session)
            new_value = not current_value
            await update_user_setting(user_id, "anti_mev", new_value, session)
            await show_settings_menu(callback_query, session)

    except Exception as e:
        logger.error(f"Error editing setting: {e}")
        await callback_query.answer("❌ Произошла ошибка при редактировании настройки")


@router.message(BuySettingStates.waiting_for_gas_fee, flags={"priority": 5})
async def handle_buy_gas_fee(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обработчик для установки значения Gas Fee"""
    try:
        # Получаем значение из сообщения
        amount = message.text.strip()
        
        # Проверяем, что введено число
        try:
            amount = float(amount)
        except ValueError:
            await message.reply("❌ Пожалуйста, введите числовое значение для Gas Fee")
            return

        # Получаем пользователя и его настройки
        user_id = message.from_user.id
        
        # Получаем текущие настройки
        settings_dict = await get_user_settings(user_id, session)
        if not settings_dict:
            await message.reply("❌ Настройки не найдены")
            return

        # Обновляем значение Gas Fee
        if 'buy' not in settings_dict:
            settings_dict['buy'] = {}
        settings_dict['buy']['gas_fee'] = amount
        
        # Сохраняем обновленные настройки
        await update_user_setting(user_id, 'buy', settings_dict['buy'], session)
        await state.clear()

        # Отправляем подтверждение
        await message.reply(f"✅ Gas Fee установлено: {amount}")
        
        # Показываем обновленное меню настроек
        await show_settings_menu(message, session)

    except Exception as e:
        logger.error(f"Error handling buy gas fee: {e}")
        traceback.print_exc()
        await message.reply(
            "❌ Произошла ошибка при установке Gas Fee",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu")]
            ])
        )
