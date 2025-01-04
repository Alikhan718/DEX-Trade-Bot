import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ...services.rugcheck import RugCheckService

logger = logging.getLogger(__name__)

router = Router()

class RugCheckStates(StatesGroup):
    waiting_for_token = State()

def _is_valid_token_address(address: str) -> bool:
    """Проверяет валидность адреса токена"""
    try:
        if len(address) != 44:
            return False
        valid_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
        return all(c in valid_chars for c in address)
    except Exception:
        return False

@router.callback_query(lambda c: c.data == "rugcheck")
async def on_rugcheck_button(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик нажатия кнопки Проверка на скам"""
    try:
        await callback_query.message.edit_text(
            "🔍 Введите адрес токена для проверки на скам:",
            parse_mode="MARKDOWN",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )
        await state.set_state(RugCheckStates.waiting_for_token)
    except Exception as e:
        logger.error(f"Error in rugcheck button handler: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@router.message(RugCheckStates.waiting_for_token)
async def handle_token_input(message: types.Message, state: FSMContext):
    """Обработчик ввода адреса токена для проверки"""
    try:
        token_address = message.text.strip()
        
        # Проверяем валидность адреса
        if not _is_valid_token_address(token_address):
            await message.reply(
                "❌ Неверный адрес токена\n"
                "Пожалуйста, отправьте корректный адрес токена",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            return

        # Отправляем сообщение о начале проверки
        status_message = await message.reply("🔍 Анализируем токен...")
        
        # Создаем сервис и получаем данные
        rugcheck_service = RugCheckService()
        try:
            result = await rugcheck_service.check_token(token_address)
            
            # Определяем статусы и эмодзи
            verification_status = "✅ Верифицирован" if result.verification else "⚠️ Не верифицирован"
            
            # Определяем статус безопасности на основе рисков
            critical_risks = sum(1 for r in result.risks if r.level == "CRITICAL")
            high_risks = sum(1 for r in result.risks if r.level == "HIGH")
            
            if critical_risks > 0:
                safety_emoji = "🚫"
                safety_status = "КРИТИЧЕСКИ ОПАСНО! Обнаружены критические риски!"
            elif high_risks >= 2:
                safety_emoji = "⚠️"
                safety_status = "ОПАСНО! Обнаружены множественные риски!"
            elif high_risks == 1:
                safety_emoji = "⚠️"
                safety_status = "Требуется внимание! Обнаружен риск!"
            else:
                safety_emoji = "✅"
                safety_status = "Признаков скама не обнаружено"
            
            # Определяем эмодзи для скора
            if len(result.risks) == 0:
                score_emoji = "🟢"  # Зеленый для 0 рисков
            elif critical_risks > 0:
                score_emoji = "🔴"  # Красный для критических рисков
            elif high_risks > 0:
                score_emoji = "🟠"  # Оранжевый для высоких рисков
            else:
                score_emoji = "🟡"  # Желтый для остальных случаев
            
            # Формируем базовую информацию
            message_text = (
                f"🛡️ Результаты проверки токена\n\n"
                f"📍 Токен: {result.token_meta.name} ({result.token_meta.symbol})\n"
                f"📝 Адрес: `{result.mint}`\n\n"
            )
            
            # Если это не ошибка загрузки, добавляем детальную информацию
            if result.token_meta.name != "Error Loading Token":
                message_text += (
                    f"{score_emoji} Количество рисков: {len(result.risks)}\n"
                    f"✨ Статус: {verification_status}\n"
                    f"{safety_emoji} Статус безопасности: {safety_status}\n"
                )
                
                # Добавляем информацию о ликвидности, если она есть
                if result.total_market_liquidity > 0:
                    message_text += f"💰 Ликвидность: ${result.total_market_liquidity:,.2f}\n"
                
                message_text += "\n"
            
            # Добавляем информацию о рисках
            if result.risks:
                message_text += "🔍 Обнаруженные риски:\n"
                for risk in result.risks:
                    emoji = rugcheck_service.format_risk_level(risk.level)
                    message_text += f"{emoji} {risk.name}: {risk.description}\n"
            else:
                message_text += "✅ Рисков не обнаружено\n"
            
            # Добавляем рекомендации только если это не ошибка
            if result.token_meta.name != "Error Loading Token":
                message_text += (
                    f"\n💡 Рекомендации:\n"
                )
                
                if critical_risks > 0:
                    message_text += (
                        f"• ⚠️ КАТЕГОРИЧЕСКИ не рекомендуется для покупки!\n"
                        f"• Обнаружены критические риски\n"
                        f"• Высокая вероятность потери средств\n"
                    )
                elif high_risks >= 2:
                    message_text += (
                        f"• ⚠️ НЕ рекомендуется для покупки!\n"
                        f"• Обнаружены множественные риски\n"
                        f"• Высокий риск потери средств\n"
                    )
                elif high_risks == 1:
                    message_text += (
                        f"• ⚠️ Торговля с повышенным риском\n"
                        f"• Внимательно изучите риски\n"
                        f"• Используйте минимальный размер позиции\n"
                    )
                else:
                    message_text += (
                        f"• DYOR (Проведите собственное исследование)\n"
                        f"• Используйте разумный размер позиции\n"
                        f"• Следите за обновлениями в сообществе\n"
                    )
                
                message_text += f"\n🔍 Подробный анализ: [RugCheck](https://rugcheck.xyz/tokens/{token_address})"

            await status_message.edit_text(
                message_text,
                parse_mode="MARKDOWN",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
                ])
            )
            
        finally:
            await rugcheck_service.close()
        
        # Очищаем состояние
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error processing token address: {e}")
        await message.reply(
            "❌ Произошла ошибка при проверке токена\n"
            "Пожалуйста, попробуйте позже",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
            ])
        )
        await state.clear() 