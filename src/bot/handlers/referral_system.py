import os
import traceback
from typing import Union

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from src.database import User

router = Router()

logger = logging.getLogger(__name__)


@router.callback_query(F.data == "referral_menu", flags={"priority": 3})
async def show_referral_menu(update: Union[types.Message, types.CallbackQuery], session: AsyncSession):
    """Отображение главного меню реферальной системы с данными из базы"""
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

        # Формируем текст меню
        query = select(func.count(User.id)).where(User.referral_id == user.id)
        result = await session.execute(query)
        referral_count = result.scalar()
        menu_text = (
            "👥 Реферальная Система\n\n"
            "Делитесь ссылкой с другими пользователями чтобы зарабатывать на комиссии с их транзакций! \n\nЗа каждого приведенного реферала вы будете получать 0.5% от суммы транзакции\n\n"
            f"Количество рефералов: {referral_count}"
        )
        buy_settings_keyboard = []

        referral_keyboard = [
            InlineKeyboardButton(text=f"🚀 Скопировать ссылку реферала",
                                 callback_data="copy_referral_link"),
        ]

        # Создаем список кнопок, распределяя их по строкам
        buttonRows = []
        max_len = max(len(buy_settings_keyboard), len(referral_keyboard))
        for i in range(max_len):
            row = []
            if i < len(buy_settings_keyboard):
                row.append(buy_settings_keyboard[i])
            if i < len(referral_keyboard):
                row.append(referral_keyboard[i])
            buttonRows.append(row)

        # Формирование клавиатуры
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=
            buttonRows +  # Добавляем кнопки покупки и продажи
            [
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
                "❌ Произошла ошибка при загрузке меню реферала",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
        else:  # CallbackQuery
            await update.message.edit_text(
                "❌ Произошла ошибка при загрузке меню реферала",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )


@router.callback_query(F.data == "copy_referral_link", flags={"priority": 3})
async def copy_referral_link(callback_query: types.CallbackQuery, session: AsyncSession):
    """Генерация и отправка ссылки реферала"""
    try:
        user_id = callback_query.from_user.id

        # Получаем данные пользователя из базы
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user or not user.referral_code:
            await callback_query.message.edit_text(
                "❌ Не удалось найти данные реферала. Убедитесь, что ваш профиль создан.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="referral_menu")]
                ])
            )
            return
        bot_username = os.getenv("BOT_USERNAME")
        # Формируем ссылку
        referral_link = f"https://t.me/{bot_username}?start=code_{user.referral_code}"

        # Отправляем ссылку
        await callback_query.message.edit_text(
            f"🔗 Ваша реферальная ссылка:\n\n`{referral_link}`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="referral_menu")]
            ]),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error copying referral link: {e}")
        traceback.print_exc()

        await callback_query.message.edit_text(
            "❌ Произошла ошибка при генерации реферальной ссылки.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="referral_menu")]
            ])
        )
