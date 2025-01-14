from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Float, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from .database import Base
from cryptography.fernet import Fernet
import os
from dotenv import load_dotenv
from datetime import datetime
import base64
import logging
from solders.keypair import Keypair

load_dotenv()

def get_encryption_key():
    """Get encryption key from environment variables or generate new one"""
    key = os.getenv('ENCRYPTION_KEY')
    if not key:
        # Generate new key if not in .env
        new_key = Fernet.generate_key()
        print(f"Generated new encryption key: {new_key.decode('ascii')}")
        print("Please add this key to your .env file as ENCRYPTION_KEY")
        return new_key
    
    try:
        # Verify key is valid
        Fernet(key.encode('ascii'))
        return key.encode('ascii')
    except Exception as e:
        print(f"Invalid encryption key in .env: {str(e)}")
        # Generate new key if current is invalid
        new_key = Fernet.generate_key()
        print(f"Generated new encryption key: {new_key.decode('ascii')}")
        print("Please update your .env file with this new key")
        return new_key

# Get encryption key
ENCRYPTION_KEY = get_encryption_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

logger = logging.getLogger(__name__)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True)
    solana_wallet = Column(String(44), unique=True, index=True)  # Base58 Solana address is 44 chars
    _private_key = Column("private_key", Text)  # Encrypted private key
    referral_code = Column(String(8), unique=True, index=True)
    total_volume = Column(Float, default=0.0)
    settings = Column(JSONB, default={})  # User settings in JSON format
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_activity = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    @property
    def private_key(self) -> str:
        """Decrypt and return private key"""
        if not self._private_key:
            logger.error("[DB] No encrypted private key found")
            return None
        try:
            logger.info("[DB] Attempting to decrypt private key")
            logger.debug(f"[DB] Encrypted key length: {len(self._private_key)}")
            
            # Decrypt the key
            decrypted = cipher_suite.decrypt(self._private_key.encode('ascii')).decode('ascii')
            logger.info("[DB] Successfully decrypted private key")
            logger.debug(f"[DB] Decrypted key length: {len(decrypted)}")
            
            # Validate decrypted key format
            try:
                key_parts = decrypted.split(',')
                logger.debug(f"[DB] Split decrypted key into {len(key_parts)} parts")
                
                if len(key_parts) != 64:
                    logger.error(f"[DB] Invalid decrypted key length: {len(key_parts)} (expected 64)")
                    return None
                
                # Verify each part is a valid integer
                key_bytes = [int(i) for i in key_parts]
                if not all(0 <= b <= 255 for b in key_bytes):
                    logger.error("[DB] Invalid byte values in decrypted key")
                    return None
                    
                # Try to create a keypair to verify the key is valid
                keypair = Keypair.from_bytes(bytes(key_bytes))
                logger.debug(f"[DB] Successfully verified key. Public key: {keypair.pubkey()}")
                
            except Exception as e:
                logger.error(f"[DB] Invalid decrypted key format: {str(e)}")
                logger.error(f"[DB] Error type: {type(e).__name__}")
                return None
            
            return decrypted
            
        except Exception as e:
            logger.error(f"[DB] Failed to decrypt private key: {str(e)}")
            logger.error(f"[DB] Error type: {type(e).__name__}")
            return None
    
    @private_key.setter
    def private_key(self, value: str):
        """Encrypt and save private key"""
        if value is None:
            logger.info("[DB] Setting private key to None")
            self._private_key = None
        else:
            try:
                logger.info("[DB] Validating private key before encryption")
                
                # Validate key format
                key_parts = value.split(',')
                if len(key_parts) != 64:
                    logger.error(f"[DB] Invalid key length: {len(key_parts)} (expected 64)")
                    raise ValueError(f"Invalid private key length: {len(key_parts)}")
                
                # Verify each part is a valid integer
                key_bytes = [int(i) for i in key_parts]
                if not all(0 <= b <= 255 for b in key_bytes):
                    logger.error("[DB] Invalid byte values in key")
                    raise ValueError("Invalid byte values in key")
                
                # Try to create a keypair to verify the key is valid
                keypair = Keypair.from_bytes(bytes(key_bytes))
                logger.debug(f"[DB] Successfully verified key. Public key: {keypair.pubkey()}")
                
                # Encrypt the key
                logger.info("[DB] Encrypting private key")
                encrypted = cipher_suite.encrypt(value.encode('ascii')).decode('ascii')
                logger.debug(f"[DB] Encrypted key length: {len(encrypted)}")
                
                self._private_key = encrypted
                logger.info("[DB] Private key successfully encrypted and stored")
                
            except Exception as e:
                logger.error(f"[DB] Failed to encrypt private key: {str(e)}")
                logger.error(f"[DB] Error type: {type(e).__name__}")
                raise ValueError(f"Failed to encrypt private key: {str(e)}")

class SmartMoneyTrader(Base):
    __tablename__ = "smart_money_traders"
    
    id = Column(Integer, primary_key=True)
    wallet_address = Column(String(44), unique=True, nullable=False, index=True)
    total_profit = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    successful_trades = Column(Integer, default=0)
    last_trade_at = Column(DateTime(timezone=True))
    extra_data = Column(JSONB, default={})  # Additional metadata in JSON format
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), index=True)
    token_address = Column(String(44), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    is_buy = Column(Boolean, default=True)
    status = Column(String(20), default='pending', index=True)  # pending, completed, failed
    gas_fee = Column(Float)
    transaction_hash = Column(String(88), unique=True)  # Solana transaction hash
    extra_data = Column(JSONB, default={})  # Additional trade data in JSON format
    
    user = relationship("User", backref="trades") 

class CopyTrade(Base):
    __tablename__ = "copy_trades"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String, nullable=False)
    wallet_address = Column(String(44), nullable=False)
    is_active = Column(Boolean, default=True)
    
    # Настройки копирования
    copy_percentage = Column(Float, default=100.0)
    min_amount = Column(Float, default=0.0)
    max_amount = Column(Float, nullable=True)  # NULL = без ограничений
    total_amount = Column(Float, nullable=True)  # NULL = без ограничений
    max_copies_per_token = Column(Integer, nullable=True)  # NULL = без ограничений
    copy_sells = Column(Boolean, default=True)
    retry_count = Column(Integer, default=1)
    
    # Настройки транзакций
    buy_gas_fee = Column(Integer, default=100000)
    sell_gas_fee = Column(Integer, default=100000)
    buy_slippage = Column(Float, default=5.0)
    sell_slippage = Column(Float, default=1.0)
    anti_mev = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", backref="copy_trades")
    transactions = relationship("CopyTradeTransaction", backref="copy_trade")

class ExcludedToken(Base):
    __tablename__ = "excluded_tokens"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    token_address = Column(String(44), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", backref="excluded_tokens")

class CopyTradeTransaction(Base):
    __tablename__ = "copy_trade_transactions"
    
    id = Column(Integer, primary_key=True)
    copy_trade_id = Column(Integer, ForeignKey('copy_trades.id'))
    token_address = Column(String(44))
    original_signature = Column(String)
    copied_signature = Column(String)
    transaction_type = Column(String)  # BUY/SELL
    status = Column(String)  # SUCCESS/FAILED
    error_message = Column(String)
    amount_sol = Column(Float)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow) 