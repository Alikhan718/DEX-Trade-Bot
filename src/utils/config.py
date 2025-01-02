import os
import logging
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Centralized configuration management with secure loading"""
    
    @staticmethod
    def get_env_variable(var_name: str, default: Optional[str] = None) -> str:
        """
        Safely retrieve environment variables
        
        :param var_name: Name of the environment variable
        :param default: Default value if variable is not set
        :return: Value of the environment variable
        """
        value = os.getenv(var_name, default)
        if value is None or value.strip() == "":
            raise ValueError(f"Critical environment variable {var_name} is not set!")
        return value.strip()
    
    # Core bot configuration
    TELEGRAM_BOT_TOKEN = get_env_variable('TELEGRAM_BOT_TOKEN')
    SOLANA_RPC_URLS = [
        'https://api.mainnet-beta.solana.com',  # Public RPC
        'https://solana-api.projectserum.com',  # Public RPC
        'https://rpc.ankr.com/solana',  # Requires API key
        'https://solana.getblock.io/mainnet/',  # Requires API key
        'https://mainnet.rpcpool.com/',  # Requires API key
        'https://api.metaplex.solana.com/',  # Public RPC
        'https://solana-mainnet.g.alchemy.com/v2/demo',  # Demo endpoint
        'https://free.rpcpool.com',  # Public RPC
        'https://api.mainnet.solana.com',  # Public RPC
    ]
    SOLANA_RPC_URL = SOLANA_RPC_URLS[0]  # Use public RPC by default
    DATABASE_URL = get_env_variable('DATABASE_URL', 'sqlite:///solana_dex_bot.db')
    BOT_USERNAME = get_env_variable('BOT_USERNAME', 'DEX_Copy_Trade_Bot')
    
    # Logging configuration
    LOGGING_CONFIG = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                'encoding': 'utf-8'
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
                'level': get_env_variable('LOG_LEVEL', 'INFO'),
                'stream': 'ext://sys.stdout'
            },
            'file': {
                'class': 'logging.FileHandler',
                'filename': 'bot.log',
                'formatter': 'standard',
                'level': 'ERROR',
                'encoding': 'utf-8'
            }
        },
        'loggers': {
            '': {
                'handlers': ['console', 'file'],
                'level': get_env_variable('LOG_LEVEL', 'INFO'),
                'propagate': True
            }
        }
    } 