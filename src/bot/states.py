from aiogram.fsm.state import State, StatesGroup


class BuyStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_amount = State()
    waiting_for_slippage = State()
    waiting_for_trigger_price = State()
    waiting_for_gas_fee = State()


class LimitBuyStates(StatesGroup):
    idle = State() 
    set_trigger_price = State()
    set_amount_sol = State()
    set_slippage = State()
    confirm = State()


class LimitSellStates(StatesGroup):
    idle = State()
    set_trigger_price = State()
    set_percentage = State()
    set_slippage = State()
    confirm = State()


class SellStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_percentage = State()
    waiting_for_slippage = State()
    waiting_for_gas_fee = State()


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


class BuySettingStates(StatesGroup):
    waiting_for_gas_fee = State()
    waiting_for_slippage = State()


class SellSettingStates(StatesGroup):
    waiting_for_gas_fee = State()
    waiting_for_slippage = State()


class WithdrawStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_address = State()
