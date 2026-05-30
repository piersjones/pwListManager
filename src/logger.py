import logging
import os
from logging.handlers import RotatingFileHandler

# Define paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

def setup_logger(level_name: str = "INFO") -> logging.Logger:
    """Configures and returns the main application logger."""
    # Ensure logs directory exists
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Map string level to logging level
    level = getattr(logging, level_name.upper(), logging.INFO)
    
    logger = logging.getLogger("pwListManager")
    logger.setLevel(level)
    
    # Clear any existing handlers
    if logger.handlers:
        logger.handlers.clear()
        
    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File Handler (max 5MB file, keeping 3 backups)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"Failed to create file handler for logging: {e}")
        
    return logger
