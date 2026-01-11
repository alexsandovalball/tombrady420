"""
Configuration settings for the Telegram bot.
Loads environment variables and provides constants for use throughout the project.
"""

import os
from pathlib import Path

# Load environment variables from .env if present
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / '.env'
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x]
DB_PATH = os.getenv('DB_PATH', str(BASE_DIR / 'catalog.db'))
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', '8443'))
WEBHOOK_LISTEN = os.getenv('WEBHOOK_LISTEN', '0.0.0.0')
DEBUG = os.getenv('DEBUG', '0') == '1'
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', 'en')

# Categories and subcategories can be loaded from a config file or defined here
CATEGORIES = {
    "Flowers": ["Sativa", "Indica", "Hybrid"],
    "Edibles": ["Gummies", "Chocolates", "Beverages"],
    "Concentrates": ["Wax", "Shatter", "Oil"],
}

# Pagination settings
PRODUCTS_PER_PAGE = int(os.getenv('PRODUCTS_PER_PAGE', '5'))