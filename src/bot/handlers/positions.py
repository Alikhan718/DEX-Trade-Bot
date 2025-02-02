from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.bot.utils.user import get_real_user_id
from src.database.models import User
from src.solana_module.transaction_handler import UserTransactionHandler
from src.services.token_info import TokenInfoService
from src.bot.handlers.buy import _format_price
import logging
import traceback

router = Router()
logger = logging.getLogger(__name__)
token_info_service = TokenInfoService()


async def format_token_info(token_address: str, balance: float, token_info: dict) -> str:
    """Форматирует информацию о токене для отображения"""
    token_value_usd = balance * token_info.price_usd
    return (
        f"💎 {token_info.symbol} ({token_info.name})\n"
        f"└ Баланс: {_format_price(balance)} ({_format_price(token_value_usd)}$)\n"
        f"└ Цена: ${_format_price(token_info.price_usd)}\n"
        f"└ Market Cap: ${_format_price(token_info.market_cap)}\n"
        f"└ Renounced: {'✅' if token_info.is_renounced else '❌'}\n"
        f"└ Burnt: {'✅' if token_info.is_burnt else '❌'}\n"
        f"└ Адрес: `{token_address}`\n"
    )

@router.callback_query(F.data == "open_positions")
async def show_positions(callback_query: types.CallbackQuery, session: AsyncSession):
    """Показывает открытые позиции пользователя"""
    try:
        user_id = get_real_user_id(callback_query)
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            await callback_query.answer("❌ Пользователь не найден")
            return

        # Создаем обработчик транзакций
        tx_handler = UserTransactionHandler(user.private_key, 10000000)
        
        try:
            # Получаем баланс SOL
            sol_balance = await tx_handler.client.get_sol_balance(user.solana_wallet)
            sol_info = await token_info_service.get_token_info('So11111111111111111111111111111111111111112')
            sol_balance_usd = sol_balance * sol_info.price_usd

            # Получаем список токенов пользователя
            tokens = await tx_handler.client.get_tokens(user.solana_wallet, tx_handler)
            
            if not tokens:
                await callback_query.message.edit_text(
                    f"💰 Баланс кошелька: {_format_price(sol_balance)} SOL (${_format_price(sol_balance_usd)})\n\n"
                    "У вас нет открытых позиций",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                    ])
                )
                return

            # Подсчитываем общую стоимость портфеля
            total_value_usd = sol_balance_usd
            tokens_info = []
            
            for token_address, market_cap, name, symbol, balance in tokens:
                token_info = await token_info_service.get_token_info(token_address)
                if token_info:
                    token_value_usd = balance * token_info.price_usd
                    total_value_usd += token_value_usd
                    tokens_info.append((token_address, balance, token_info, token_value_usd))

            # Формируем сообщение
            message_text = (
                f"💼 Портфель\n\n"
                f"💰 Баланс SOL: {_format_price(sol_balance)} (${_format_price(sol_balance_usd)})\n"
                f"📈 Общая стоимость: ${_format_price(total_value_usd)}\n\n"
                f"🔷 Открытые позиции:\n\n"
            )

            # Добавляем информацию о каждом токене
            for token_address, balance, token_info, token_value_usd in tokens_info:
                message_text += await format_token_info(token_address, balance, token_info) + "\n"

            # Создаем клавиатуру с кнопками для каждого токена
            keyboard = []
            for token_address, _, token_info, _ in tokens_info:
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"Продать {token_info.symbol}",
                        callback_data=f"select_token_{token_address}"
                    )
                ])

            # Добавляем кнопку возврата
            keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])

            await callback_query.message.edit_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                parse_mode="MARKDOWN"
            )
        except Exception as e:
            logger.error(f"Error processing wallet data: {str(e)}")
            traceback.print_exc()
            await callback_query.message.edit_text(
                "❌ Произошла ошибка при получении данных кошелька",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
                ])
            )

    except Exception as e:
        logger.error(f"Error showing positions: {str(e)}")
        traceback.print_exc()
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при получении информации о позициях",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        ) 