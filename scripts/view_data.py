from sqlalchemy import create_engine, text
from src.utils.config import Config
from src.database import User, CopyTrader, Trade

def view_database():
    """View all data in the database"""
    engine = create_engine(Config.DATABASE_URL)
    
    # Create a connection
    with engine.connect() as conn:
        # View Users
        print("\n=== Users ===")
        result = conn.execute(text("SELECT * FROM users"))
        for row in result:
            print(f"\nTelegram ID: {row.telegram_id}")
            print(f"Wallet: {row.solana_wallet[:8]}...{row.solana_wallet[-4:]}")
            print(f"Private Key: {row.private_key[:8]}...")
            print(f"Referral Code: {row.referral_code}")
            print(f"Total Volume: {row.total_volume}")
            print(f"Created: {row.created_at}")
        
        # View Copy Traders
        print("\n=== Copy Traders ===")
        result = conn.execute(text("SELECT * FROM copy_traders"))
        for row in result:
            print(f"\nWallet: {row.wallet_address[:8]}...")
            print(f"Success Rate: {row.success_rate}%")
            print(f"Total Trades: {row.total_trades}")
            print(f"Followers: {row.followers_count}")
        
        # View Trades
        print("\n=== Trades ===")
        result = conn.execute(text("SELECT * FROM trades"))
        for row in result:
            print(f"\nTrader ID: {row.trader_id}")
            print(f"Token: {row.token_address}")
            print(f"Amount: {row.amount}")
            print(f"Type: {row.trade_type}")
            print(f"Time: {row.timestamp}")

if __name__ == "__main__":
    view_database() 