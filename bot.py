"""
DEX Trade Bot - Main entry point
"""

import asyncio
from src.bot.main import SolanaDEXBot

if __name__ == '__main__':
    asyncio.run(SolanaDEXBot().start())