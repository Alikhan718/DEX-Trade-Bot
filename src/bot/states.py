from aiogram.fsm.state import State, StatesGroup

class BuyStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_amount = State()
    waiting_for_slippage = State()

class SellStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_amount = State()
    waiting_for_slippage = State()

class WalletStates(StatesGroup):
    waiting_for_private_key = State()

class SmartMoneyStates(StatesGroup):
    waiting_for_token = State()

class RugCheckStates(StatesGroup):
    waiting_for_token = State()

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

class AutoBuySettingsStates(StatesGroup):
    ENTER_AMOUNT = State()
    ENTER_SLIPPAGE = State()