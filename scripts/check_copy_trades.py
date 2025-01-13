import asyncio
import sys
import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.models import CopyTrade, User, CopyTradeTransaction
from src.utils.config import Config

async def check_copy_trades():
    """Check copy trades status in database"""
    # Create engine
    engine = create_async_engine(Config.DATABASE_URL)
    
    # Create session
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        # Get all copy trades
        result = await session.execute(
            select(CopyTrade, User)
            .join(User)
            .order_by(CopyTrade.id)
        )
        copy_trades = result.all()
        
        print("\n=== Active Copy Trades ===")
        for ct, user in copy_trades:
            if ct.is_active:
                print(f"\nCopy Trade ID: {ct.id}")
                print(f"Name: {ct.name}")
                print(f"User ID: {ct.user_id} (Telegram ID: {user.telegram_id})")
                print(f"Wallet Address: {ct.wallet_address}")
                print(f"Settings:")
                print(f"  - Copy Percentage: {ct.copy_percentage}%")
                print(f"  - Min Amount: {ct.min_amount} SOL")
                print(f"  - Max Amount: {ct.max_amount or 'No limit'} SOL")
                print(f"  - Total Amount: {ct.total_amount or 'No limit'} SOL")
                print(f"  - Max Copies Per Token: {ct.max_copies_per_token or 'No limit'}")
                print(f"  - Copy Sells: {ct.copy_sells}")
                print(f"  - Buy Gas Fee: {ct.buy_gas_fee}")
                print(f"  - Sell Gas Fee: {ct.sell_gas_fee}")
                print(f"  - Buy Slippage: {ct.buy_slippage}%")
                print(f"  - Sell Slippage: {ct.sell_slippage}%")
                
                # Get recent transactions
                result = await session.execute(
                    select(CopyTradeTransaction)
                    .where(CopyTradeTransaction.copy_trade_id == ct.id)
                    .order_by(CopyTradeTransaction.created_at.desc())
                    .limit(5)
                )
                transactions = result.scalars().all()
                
                if transactions:
                    print("\n  Recent Transactions:")
                    for tx in transactions:
                        print(f"  - {tx.created_at}: {tx.transaction_type} {tx.token_address} - {tx.status}")
                        if tx.error_message:
                            print(f"    Error: {tx.error_message}")
                else:
                    print("\n  No recent transactions")
        
        print("\n=== Inactive Copy Trades ===")
        for ct, user in copy_trades:
            if not ct.is_active:
                print(f"\nCopy Trade ID: {ct.id}")
                print(f"Name: {ct.name}")
                print(f"User ID: {ct.user_id} (Telegram ID: {user.telegram_id})")
                print(f"Wallet Address: {ct.wallet_address}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_copy_trades()) 