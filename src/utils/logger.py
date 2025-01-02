import logging.config
from .config import Config

def setup_logging():
    """Configure logging for the application"""
    logging.config.dictConfig(Config.LOGGING_CONFIG)
    return logging.getLogger(__name__) 