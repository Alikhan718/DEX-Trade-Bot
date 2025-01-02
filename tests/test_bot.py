import asyncio
import logging
from src.bot.main import SolanaDEXBot

async def test_bot_functionality():
    """Test basic bot functionality"""
    bot = None
    try:
        bot = SolanaDEXBot()
        print("✅ Bot initialized successfully")
        
        # Get bot info
        bot_info = await bot.bot.get_me()
        print(f"Bot Information:\nUsername: @{bot_info.username}\nName: {bot_info.first_name}")
        
        # Don't start polling, just verify everything is set up
        print("\n✅ Bot is ready for manual testing!")
        print("\nTest commands:")
        print("/start - Start the bot")
        print("/smart <token_address> - Analyze smart money")
        print("/import_wallet <private_key> - Import wallet")
        print("/reset - Reset user data")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        raise
    finally:
        if bot and bot.bot:
            await bot.bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_bot_functionality()) 