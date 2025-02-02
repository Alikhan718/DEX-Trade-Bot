# /path/to/handlers/smart_money_handlers.py
import re
import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import asyncio
from aiogram import F
from aiogram.filters import Command
from src.services.smart_money import SmartMoneyTracker, token_info  # Импортируем класс
from src.bot.states import SmartMoneyStates
from src.bot.handlers.buy import _format_price
from solders.pubkey import Pubkey
from src.solana_module.scrape import scrape_dune_wallet_data

logger = logging.getLogger(__name__)

router = Router()
smart_money_tracker = SmartMoneyTracker()  # Создаём экземпляр класса


def _is_valid_token_address(address: str) -> bool:
    """Проверяет валидность адреса токена"""
    try:
        # Проверяем длину адреса
        if len(address) != 44:
            return False

        # Проверяем, что адрес содержит только допустимые символы
        valid_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
        return all(c in valid_chars for c in address)

    except Exception:
        return False


# Хендлер для нажатия кнопки "Smart Money"
@router.callback_query(F.data == "smart_money", flags={"priority": 5})
async def on_smart_money_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Smart Money"""
    try:
        await callback_query.message.answer(
            "🧠 Smart Money Анализ\n\n"
            "Отправьте адрес токена для анализа.\n"
            "Например: `HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
            parse_mode="MARKDOWN",
            reply_markup=ForceReply(selective=True)
        )
        await state.set_state(SmartMoneyStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопки Smart Money: {e}")
        await callback_query.answer("❌ Произошла ошибка")


# Хендлер для команды /smart
@router.message(Command("smart"), flags={"priority": 5})
async def handle_smart_money_command(message: types.Message):
    """Обработчик команды для Smart Money анализа"""
    try:
        # Получаем адрес токена из команды
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply(
                "❌ Пожалуйста, укажите адрес токена после команды\n"
                "Пример: `/smart HtLFhnhxcm6HWr1Bcwz27BJdks9vecbSicVLGPPmpump`",
                parse_mode="MARKDOWN"
            )
            return

        token_address = parts[1]

        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, проверьте адрес и попробуйте снова"
            )
            return

        # Отправляем сообщение о начале анализа
        status_message = await message.reply(
            "🔍 Анализируем токен и получаем информацию о трейдерах...\n"
            "Это может занять некоторое время"
        )

        try:
            # Анализируем токен через SmartMoneyTracker
            metadata, traders = await asyncio.wait_for(
                smart_money_tracker.analyze_accounts(token_address),
                timeout=60  # 60 секунд таймаут
            )

            # Форматируем результат
            result_message = format_smart_money_message(metadata, traders)

            await status_message.edit_text(
                result_message,
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )

        except asyncio.TimeoutError:
            await status_message.edit_text(
                "❌ Превышено время ожидания при анализе токена\n"
                "Попробуйте позже"
            )
            return

    except Exception as e:
        logger.error(f"Ошибка в команде Smart Money: {e}")
        await message.reply(
            "❌ Произошла ошибка при анализе токена\n"
            "Пожалуйста, попробуйте позже или проверьте адрес токена"
        )


# Хендлер для ввода адреса токена
@router.message(SmartMoneyStates.waiting_for_token)
async def handle_token_address_input(message: types.Message, state: FSMContext):
    """Обработчик ввода адреса токена для Smart Money анализа"""
    try:
        token_address = message.text.strip()

        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, отправьте корректный адрес токена или нажмите Назад для возврата в меню",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )
            return

        # Сбрасываем состояние
        await state.clear()

        # Отправляем сообщение о начале анализа
        status_message = await message.reply(
            "🔍 Анализируем токен и получаем информацию о трейдерах...\n"
            "Это может занять некоторое время"
        )

        # Анализируем токен через веб-скрейпинг
        traders = scrape_dune_wallet_data(token_address)
        metadata = token_info(token_address)
        result_message = format_smart_money_message(metadata, traders)

        # Отправляем результат
        await status_message.edit_text(
            result_message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка обработки ввода токена: {e}")
        await message.reply(
            "❌ Произошла ошибка при анализе токена\n"
            "Пожалуйста, попробуйте позже или проверьте адрес токена"
        )
        await state.clear()


def format_smart_money_message(metadata, traders):
    """Форматируем сообщение с результатами анализа"""
    metadata_message = (
        f"🔹 <b>Токен:</b> {metadata.get('baseTokenName')} ({metadata['baseToken'].get('symbol')})\n"
        f"💰 <b>Цена:</b> {_format_price(metadata.get('priceUsd'))} USD\n"
        f"📈 <b>Объём:</b> {_format_price(metadata.get('marketCap'))} USD\n\n"
    )

    traders_message = "🧑‍💼 <b>Крупнейшие трейдеры:</b>\n\n"
    for trader in traders[:15]:  # Ограничиваем список до 5 трейдеров
        traders_message += (
            #f"📜 **Адрес:** [{trader.get('wallet', 'Неизвестно')}](https://t.me/test2737237bot?wallet={trader.get('wallet', 'Неизвестно')})\n"
            f"📜 <b>Адрес:</b> <code>{trader.get('wallet', 'Неизвестно')}</code>\n"
            f"💰 <b>Куплено:</b> {trader.get('sum_buys', 0)} USD\n"
            f"💵 <b>Продано:</b> {trader.get('sum_sells', 0)} USD\n"
            f"📈 <b>Прибыль PnL:</b> {trader.get('sum_pnl', 0)} USD\n"
            f"📊 <b>ROI:</b> {trader.get('roi_real', 0)}\n"
            f"🔍 <a href=\"{re.search(r'href=\"([^\"]+)\"', trader['solscan']).group(1)}\">Solscan</a> | "
            f"📊 <a href=\"{re.search(r'href=\"([^\"]+)\"', trader['wallet_pnl']).group(1)}\">Wallet PnL</a> | "
            f"🤖 <a href=\"{re.search(r'href=\"([^\"]+)\"', trader['gmgn']).group(1)}\">gmgn</a> | "
            f"🌐 <a href=\"{re.search(r'href=\"([^\"]+)\"', trader['cielo']).group(1)}\">cielo</a>\n\n"
        )

    return metadata_message + traders_message


@router.message(Command('wallet'))
async def handle_wallet_command(message: types.Message):
    """Обработчик команды для ввода адреса кошелька"""
    parts = message.text.split()[1]
    if len(parts) < 2:
        await message.reply(
            "�� Пожалуйста, укажите адрес кошелька после команды\n"
            "Пример: `/wallet 0x90F8bf6A479f320ead074411a4B0e7944Ea8c9C1`",
            parse_mode="MARKDOWN"
        )
        return
    print('AAAAAAAAAAAAAAAAAAAA', parts)

    wallet_address = parts[1]

    # Проверяем валидность адреса
    