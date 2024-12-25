import asyncio
from bot import SolanaDEXBot
import logging

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
        print("/connect_wallet <solana_address> - Connect wallet")
        print("/top_traders - View top traders")
        print("/follow <trader_address> - Follow a trader")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        raise
    finally:
        if bot and bot.bot:
            await bot.bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_bot_functionality()) 