import os
import traceback
from typing import Union

from aiogram import Router, types, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Message
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.bot.handlers.buy import _format_price
from src.bot.handlers.withdraw import shorten_address
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
async def claim_referral_bonus(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession,
                               solana_service: SolanaService, bot: Bot):
    """Запросить адрес для вывода"""
    logger.info("[REFERRAL] Starting cash out process")
    try:
        # Получаем сохраненные данные
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        query = select(ReferralRecords).where(ReferralRecords.user_id == user.id)
        result = await session.execute(query)
        referral_records = result.unique().scalars().all()
        amount = 0
        for rec in referral_records:
            if rec.is_sent == False:
                amount += float(rec.amount_sol or 0)

        user_balance = await solana_service.get_wallet_balance(user.solana_wallet)

        key = os.getenv('SECRET_KEY').strip()
        key_parts = key.split(',')
        private_key_bytes = bytes([int(i) for i in key_parts])
        bonus_wallet_keypair = Keypair.from_bytes(private_key_bytes)
        public_key = str(bonus_wallet_keypair.pubkey())
        bonus_wallet_balance = await solana_service.get_wallet_balance(public_key)

        if amount < 0.01:
            await callback_query.answer("❌ Недостаточно накопленных бонусов, минимальный вывод 0.01 SOL")
            return
        if amount >= bonus_wallet_balance:
            await callback_query.answer("❌ Сервис вывода бонусов временно недоступен, попробуйте позже")
            # 🛑 Отправляем сообщение админу о необходимости пополнить баланс
            admin_message = (
                f"⚠️ Недостаточно средств на бонусном кошельке!\n"
                f"💰 Баланс: {_format_price(bonus_wallet_balance)} SOL\n"
                f"🔺 Требуемая сумма: {_format_price(amount)} SOL\n"
                f"🚀 Срочно пополните кошелек: {shorten_address(public_key)}"
            )
            return await bot.send_message(304280297, admin_message)

        # Отправляем уведомление о начале транзакции
        await callback_query.message.edit_text(
            f"⏳ Выполняется вывод {_format_price(amount)} SOL на адрес {shorten_address(user.solana_wallet)}",
        )

        # Создаем клиент и выполняем транзакцию
        client = solana_service.create_client(str(key))

        signature = await client.send_transfer_transaction(
            recipient_address=user.solana_wallet,
            amount_sol=amount,
            is_token_transfer=False,  # Это перевод SOL, а не токенов
        )

        if signature:
            # Транзакция успешна
            await session.execute(
                update(ReferralRecords)
                .where(ReferralRecords.user_id == user.id)
                .values(is_sent=True)
            )
            await session.commit()
            await callback_query.message.answer(
                f"✅ Бонусы успешно переведены на ваш кошелек: {_format_price(amount)} SOL\n"
                f"🔗 Транзакция: [Solscan](https://solscan.io/tx/{signature})",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
        else:
            raise Exception("Transaction failed")

    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error confirming withdrawal: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при выводе средств",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="referral_menu")]
            ])
        )
