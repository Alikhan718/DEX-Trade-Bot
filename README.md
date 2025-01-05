# DEX-Trade-Bot
This bot is a cryptocurrency trading assistant with features like copy-trading, affiliate systems, limit orders, top trader rankings, trending coins, scam checks (RugCheck), staking, and wallet monitoring. Users earn bonuses for trading milestones, track other traders, and get activity reminders.

## Database Setup

### PostgreSQL Installation

1. Install PostgreSQL:
   ```bash
   # For Windows:
   # Download and install from https://www.postgresql.org/download/windows/
   
   # For Ubuntu/Debian:
   sudo apt update
   sudo apt install postgresql postgresql-contrib
   
   # For macOS with Homebrew:
   brew install postgresql
   ```

2. Create Database and User:
   ```bash
   # Connect to PostgreSQL
   sudo -u postgres psql
   
   # Create database
   CREATE DATABASE dex_bot;
   
   # Create user (replace 'your_password' with a secure password)
   CREATE USER dex_bot_user WITH PASSWORD 'your_password';
   
   # Grant privileges
   GRANT ALL PRIVILEGES ON DATABASE dex_bot TO dex_bot_user;
   
   # Exit psql
   \q
   ```

3. Configure Environment Variables:
   Create a `.env` file in the project root with:
   ```env
   DATABASE_URL=postgresql+asyncpg://dex_bot_user:your_password@localhost:5432/dex_bot
   TELEGRAM_BOT_TOKEN=your_bot_token
   ENCRYPTION_KEY=your_encryption_key
   ```

4. Initialize Database:
   ```bash
   python scripts/init_database.py
   ```

## Installation and Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the bot:
   ```bash
   python bot.py
   ```

## Features

- Copy Trading with customizable settings
- Smart Money tracking
- Token security checks with RugCheck
- Multi-level referral system
- Limit orders
- Wallet management
- Trading analytics

## Development

- Python 3.8+
- PostgreSQL 12+
- Async architecture
- SQLAlchemy with asyncpg
- Aiogram for Telegram interactions
