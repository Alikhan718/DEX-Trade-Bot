"""
DEX Trade Bot - Main entry point
"""

import asyncio
import logging
from src.utils.config import Config
from src.utils.logger import setup_logging
from src.bot.main import SolanaDEXBot

logger = setup_logging()
if __name__ == "__main__":
    try:
        bot = SolanaDEXBot()
        asyncio.run(bot.start())
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

        raise