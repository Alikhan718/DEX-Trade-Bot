import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(lambda c: c.data == "copy_trade")
async def show_copy_trade_menu(callback_query: CallbackQuery):
    """Show main copy trade menu with list of configurations"""
    # Example data (will be replaced with real data later)
    example_configs = [
        {"id": 1, "name": "Whale Trader #1", "is_active": True},
        {"id": 2, "name": "DeFi Expert", "is_active": False},
        {"id": 3, "name": "SOL Trader", "is_active": True},
    ]
    
    keyboard = []
    
    # Add existing copy trade configurations
    for config in example_configs:
        status = "🟢" if config["is_active"] else "⭕️"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status} {config['name']}", 
                callback_data=f"copy_settings:{config['id']}"
            )
        ])
    
    # Add control buttons
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Добавить", callback_data="new_copy_trade")],
        [InlineKeyboardButton(text="🚫 Исключить Токены", callback_data="exclude_tokens")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    
    await callback_query.message.edit_text(
        "📊 Copy Trading\n\n"
        "Ваши настроенные конфигурации копитрейдинга:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(lambda c: c.data.startswith("copy_settings:"))
async def show_copy_settings(callback_query: CallbackQuery):
    """Show settings for specific copy trade configuration"""
    config_id = callback_query.data.split(":")[1]
    
    # Example data (will be replaced with real data later)
    is_active = True
    wallet = "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
    name = "Whale Trader #1"
    
    keyboard = [
        # Basic settings
        [InlineKeyboardButton(text="📝 Название", callback_data=f"edit_name:{config_id}")],
        [InlineKeyboardButton(text="👛 Кошелек для копирования", callback_data=f"edit_wallet:{config_id}")],
        [InlineKeyboardButton(text="⚙️ Слипаж", callback_data=f"edit_slippage:{config_id}")],
        [InlineKeyboardButton(text="⛽️ Gas Fee", callback_data=f"edit_gas:{config_id}")],
        
        # Trade settings
        [InlineKeyboardButton(text="📊 % от суммы сделки", callback_data=f"edit_percentage:{config_id}")],
        [InlineKeyboardButton(text="📉 Минимальная сумма SOL", callback_data=f"edit_min_sol:{config_id}")],
        [InlineKeyboardButton(text="📈 Максимальная сумма SOL", callback_data=f"edit_max_sol:{config_id}")],
        [InlineKeyboardButton(text="💰 Total Investment SOL", callback_data=f"edit_total_investment:{config_id}")],
        
        # Additional settings
        [InlineKeyboardButton(text="🔄 Количество покупок токена", callback_data=f"edit_buy_times:{config_id}")],
        [InlineKeyboardButton(text="📉 Копировать продажи", callback_data=f"edit_copy_sells:{config_id}")],
        [InlineKeyboardButton(text="🔁 Количество попыток", callback_data=f"edit_retries:{config_id}")],
        
        # Control buttons
        [InlineKeyboardButton(
            text=f"{'🟢' if is_active else '⭕️'} Активный", 
            callback_data=f"toggle_active:{config_id}"
        )],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_copy:{config_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="copy_trade")]
    ]
    
    await callback_query.message.edit_text(
        f"⚙️ Настройки Copy Trading\n\n"
        f"📋 Название: {name}\n"
        f"👛 Кошелек: {wallet[:4]}...{wallet[-4:]}\n"
        f"📊 Статус: {'Активен' if is_active else 'Неактивен'}\n\n"
        "Выберите параметр для настройки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(lambda c: c.data == "new_copy_trade")
async def new_copy_trade(callback_query: CallbackQuery):
    """Show new copy trade configuration menu"""
    keyboard = [
        [InlineKeyboardButton(text="👛 Добавить кошелек", callback_data="add_wallet")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="copy_trade")]
    ]
    
    await callback_query.message.edit_text(
        "➕ Новая конфигурация Copy Trading\n\n"
        "Для начала добавьте кошелек, за которым хотите следить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query(lambda c: c.data == "exclude_tokens")
async def exclude_tokens(callback_query: CallbackQuery):
    """Show token exclusion menu"""
    # Example data (will be replaced with real data later)
    excluded_tokens = [
        {"symbol": "TOKEN1", "address": "..."},
        {"symbol": "TOKEN2", "address": "..."},
    ]
    
    keyboard = []
    
    # Add excluded tokens
    for token in excluded_tokens:
        keyboard.append([
            InlineKeyboardButton(
                text=f"❌ {token['symbol']}", 
                callback_data=f"remove_excluded:{token['address']}"
            )
        ])
    
    # Add control buttons
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Добавить токен", callback_data="add_excluded_token")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="copy_trade")]
    ])
    
    await callback_query.message.edit_text(
        "🚫 Исключенные токены\n\n"
        "Эти токены не будут копироваться:\n"
        "Нажмите на токен чтобы удалить его из списка.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    ) 