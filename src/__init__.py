"""
DEX Trade Bot - Telegram bot for DEX trading with smart money tracking
"""

from .bot.main import SolanaDEXBot

__version__ = '1.0.0'
__all__ = ['SolanaDEXBot', 'SolanaClient', 'get_bonding_curve_address', 'find_associated_bonding_curve'] 