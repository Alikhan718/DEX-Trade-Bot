import os
import traceback
from typing import Union

from aiogram import Router, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Message
from solders.pubkey import Pubkey
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.bot.handlers.buy import _format_price
from src.bot.states import WithdrawStates
from src.bot.utils import get_real_user_id
from src.database import User, ReferralRecords
from src.services import SolanaService

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
        query = select(ReferralRecords).where(ReferralRecords.user_id == user.id)
        result = await session.execute(query)
        referral_records = result.unique().scalars().all()
        referral_available_sol = 0
        referral_cashed_out_sol = 0
        for rec in referral_records:
            if rec.is_sent == False:
                referral_available_sol += float(rec.amount_sol or 0)
            else:
                referral_cashed_out_sol += float(rec.amount_sol or 0)

        menu_text = (
            "👥 Реферальная Система\n\n"
            "Делитесь ссылкой с другими пользователями чтобы зарабатывать на комиссии с их транзакций! \n\nЗа каждого приведенного реферала вы будете получать 0.5% от суммы транзакции\n\n"
            f"Количество рефералов: <b>{referral_count}</b>\n\n"
            f"⚠️<b><i>Бонусы можно вывести от 0.01 SOL</i></b>⚠️\n\n"
            f"Бонусы с рефералов: {_format_price(referral_available_sol)} SOL\n"
            f"Уже выведено: {_format_price(referral_cashed_out_sol)} SOL"
        )
        buy_settings_keyboard = []

        referral_keyboard = [
            InlineKeyboardButton(text=f"🚀 Скопировать ссылку реферала",
                                 callback_data="copy_referral_link"),
            InlineKeyboardButton(text=f"💸 Вывести бонусы",
                                 callback_data="claim_referral_bonus"),
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
            await message.answer(menu_text, reply_markup=keyboard, parse_mode='HTML')
        else:  # CallbackQuery
            await message.edit_text(menu_text, reply_markup=keyboard, parse_mode='HTML')

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


@router.callback_query(F.data == "claim_referral_bonus", flags={"priority": 3})
async def claim_referral_bonus(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Запросить адрес для вывода"""
    logger.info("[REFERRAL] Starting address input process")

    # Сохраняем текущие данные
    current_data = await state.get_data()
    withdraw_amount = current_data.get("withdraw_amount")
    logger.info(f"[REFERRAL] Preserved withdraw amount: {withdraw_amount}")

    # Очищаем состояние и сохраняем данные обратно

    await state.update_data(withdraw_amount=withdraw_amount)
    logger.info("[REFERRAL] Previous state cleared, amount restored")

    # Устанавливаем новое состояние
    await state.set_state(WithdrawStates.waiting_for_address)
    current_state = await state.get_state()
    logger.info(f"[REFERRAL] State set to: {current_state}")

    await callback_query.message.answer(
        "📍 ЗДЕСЬ МОГЛА БЫ БЫТЬ ВАША РЕКЛАМА Введите адрес кошелька для вывода:",
        reply_markup=ForceReply(selective=True)
    )
    logger.info("[REFERRAL] Sent address input request with ForceReply")


@router.message(StateFilter(WithdrawStates.waiting_for_address), flags={"priority": 20})
async def handle_withdraw_address(message: Message, state: FSMContext, session: AsyncSession,
                                  solana_service: SolanaService):
    """Обработать введенный адрес"""
    logger.info(f"[WITHDRAW] Received address message: {message.text}")
    try:
        address = message.text.strip()
        # Проверяем валидность адреса
        try:
            Pubkey.from_string(address)
        except ValueError:
            await message.answer(
                "❌ Некорректный адрес кошелька",
                reply_markup=ForceReply(selective=True)
            )
            return

        # Сохраняем адрес
        await state.update_data(withdraw_address=address)

        # Показываем меню вывода с обновленной информацией
        user_id = get_real_user_id(message)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        balance = await solana_service.get_wallet_balance(user.solana_wallet)
        data = await state.get_data()
        amount = data.get("withdraw_amount", "Не указана")

        await message.answer(
            f"💰 Выполняется вывод бонусов\n"
            f"💰 Сумма для вывода: {_format_price(amount) if isinstance(amount, (int, float)) else amount}\n"
            f"📍 Адрес получателя: {address}",
            reply_markup=withdraw_menu_keyboard
        )

    except Exception as e:
        logger.error(f"Error handling withdraw address: {e}")
        await message.answer(
            "❌ Произошла ошибка при обработке адреса",
            reply_markup=withdraw_menu_keyboard
        )
