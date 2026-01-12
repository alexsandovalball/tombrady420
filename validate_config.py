#!/usr/bin/env python3
"""
Configuration Validation Tool for Telegram Bot

This tool validates your .env configuration before deployment.
It checks:
- .env file format and presence
- BOT_TOKEN format and validity (calls Telegram API)
- ADMIN_IDS format
- CHANNEL_ID format (if provided)
- Webhook configuration (if enabled)
- Database path accessibility
- Generates render.yaml for deployment

Usage:
    python validate_config.py --all              # Run all checks
    python validate_config.py --env              # Check .env file only
    python validate_config.py --token            # Validate bot token
    python validate_config.py --channel          # Check channel access
    python validate_config.py --render           # Generate render.yaml
"""

import os
import sys
import argparse
import re
from pathlib import Path
from typing import Tuple, Optional

# Check for required packages
try:
    import httpx
    from dotenv import load_dotenv
except ImportError as e:
    print(f"❌ Missing required package: {e}")
    print("Install dependencies: pip install python-dotenv httpx")
    sys.exit(1)


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_success(msg: str) -> None:
    print(f"{Colors.GREEN}✓{Colors.RESET} {msg}")


def print_error(msg: str) -> None:
    print(f"{Colors.RED}✗{Colors.RESET} {msg}")


def print_warning(msg: str) -> None:
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {msg}")


def print_info(msg: str) -> None:
    print(f"{Colors.BLUE}ℹ{Colors.RESET} {msg}")


def print_header(msg: str) -> None:
    print(f"\n{Colors.BOLD}{msg}{Colors.RESET}")
    print("=" * len(msg))


def check_env_file() -> Tuple[bool, Optional[str]]:
    """Check if .env file exists and is readable"""
    print_header("Checking .env file")
    
    env_path = Path(".env")
    if not env_path.exists():
        print_error(".env file not found")
        print_info("Copy .env.example to .env and fill in your values:")
        print_info("  cp .env.example .env")
        return False, None
    
    print_success(".env file exists")
    
    try:
        with open(env_path, 'r') as f:
            content = f.read()
        print_success(f".env file is readable ({len(content)} bytes)")
        return True, content
    except Exception as e:
        print_error(f"Failed to read .env file: {e}")
        return False, None


def check_bot_token(token: Optional[str], validate_api: bool = True) -> bool:
    """Validate BOT_TOKEN format and optionally test with Telegram API"""
    print_header("Checking BOT_TOKEN")
    
    if not token:
        print_error("BOT_TOKEN is not set")
        print_info("Get a token from @BotFather on Telegram")
        return False
    
    # Check format: should be like 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
    token_pattern = r'^\d+:[A-Za-z0-9_-]+$'
    if not re.match(token_pattern, token):
        print_error("BOT_TOKEN has invalid format")
        print_info("Token should be in format: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        return False
    
    print_success("BOT_TOKEN format is valid")
    
    if validate_api:
        print_info("Testing token with Telegram API...")
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            response = httpx.get(url, timeout=10.0)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    bot_username = bot_info.get("username", "unknown")
                    bot_name = bot_info.get("first_name", "unknown")
                    print_success(f"Token is valid! Bot: @{bot_username} ({bot_name})")
                    return True
                else:
                    print_error(f"Telegram API error: {data.get('description', 'Unknown error')}")
                    return False
            else:
                print_error(f"API request failed with status {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Failed to validate token with API: {e}")
            print_warning("Token format is valid but API validation failed (might be network issue)")
            return False
    
    return True


def check_admin_ids(admin_ids_str: Optional[str]) -> bool:
    """Validate ADMIN_IDS format"""
    print_header("Checking ADMIN_IDS")
    
    if not admin_ids_str:
        print_error("ADMIN_IDS is not set")
        print_info("Set comma-separated user IDs (get from @userinfobot)")
        print_info("Example: ADMIN_IDS=123456789,987654321")
        return False
    
    # Split and validate each ID
    ids = [x.strip() for x in admin_ids_str.split(",") if x.strip()]
    
    if not ids:
        print_error("ADMIN_IDS is empty")
        return False
    
    valid = True
    for id_str in ids:
        if not id_str.isdigit():
            print_error(f"Invalid admin ID: {id_str} (must be numeric)")
            valid = False
        else:
            try:
                user_id = int(id_str)
                if user_id <= 0:
                    print_error(f"Invalid admin ID: {id_str} (must be positive)")
                    valid = False
            except ValueError:
                print_error(f"Invalid admin ID: {id_str}")
                valid = False
    
    if valid:
        print_success(f"ADMIN_IDS format is valid ({len(ids)} admin(s))")
        for id_str in ids:
            print_info(f"  - Admin ID: {id_str}")
    
    return valid


def check_channel_id(channel_id_str: Optional[str]) -> bool:
    """Validate CHANNEL_ID format"""
    print_header("Checking CHANNEL_ID")
    
    if not channel_id_str:
        print_warning("CHANNEL_ID is not set (optional)")
        print_info("Channel ingestion will be disabled")
        return True
    
    # Channel IDs should be negative integers
    try:
        channel_id = int(channel_id_str)
        if channel_id >= 0:
            print_error(f"Invalid CHANNEL_ID: {channel_id_str}")
            print_info("Channel IDs must be negative numbers (e.g., -1001234567890)")
            return False
        print_success(f"CHANNEL_ID format is valid: {channel_id}")
        return True
    except ValueError:
        print_error(f"Invalid CHANNEL_ID: {channel_id_str} (must be a number)")
        return False


def check_webhook_config(use_webhook: Optional[str], webhook_url: Optional[str]) -> bool:
    """Validate webhook configuration"""
    print_header("Checking Webhook Configuration")
    
    use_webhook_enabled = use_webhook and use_webhook.lower() in ("1", "true", "yes")
    
    if not use_webhook_enabled:
        print_info("Webhook mode is disabled (polling mode)")
        return True
    
    print_info("Webhook mode is enabled")
    
    if not webhook_url:
        print_error("WEBHOOK_URL is required when USE_WEBHOOK=true")
        print_info("Set WEBHOOK_URL to your app's public URL")
        print_info("Example: WEBHOOK_URL=https://yourapp.onrender.com")
        return False
    
    # Validate URL format
    if not webhook_url.startswith(("http://", "https://")):
        print_error(f"Invalid WEBHOOK_URL: {webhook_url}")
        print_info("URL must start with http:// or https://")
        return False
    
    if not webhook_url.startswith("https://"):
        print_warning("WEBHOOK_URL uses http:// - Telegram requires https:// for webhooks")
        print_info("Use https:// for production deployment")
    
    print_success(f"Webhook configuration is valid: {webhook_url}")
    return True


def check_database_path(db_path: Optional[str]) -> bool:
    """Check if database path is accessible"""
    print_header("Checking Database Configuration")
    
    db_path = db_path or "data/catalog.db"
    print_info(f"Database path: {db_path}")
    
    # Check if directory exists or can be created
    db_dir = os.path.dirname(db_path) or "."
    
    try:
        os.makedirs(db_dir, exist_ok=True)
        print_success(f"Database directory is accessible: {db_dir}")
        
        # Check if we can write to the directory
        test_file = os.path.join(db_dir, ".write_test")
        try:
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
            print_success("Database directory is writable")
            return True
        except Exception as e:
            print_error(f"Database directory is not writable: {e}")
            return False
    except Exception as e:
        print_error(f"Failed to create database directory: {e}")
        return False


def generate_render_yaml() -> bool:
    """Generate render.yaml for deployment"""
    print_header("Generating render.yaml")
    
    render_yaml_content = """# Render deployment configuration
# This file is auto-generated by validate_config.py
# Edit .env.example and regenerate if needed

services:
  - type: web
    name: telegram-bot
    env: python
    region: oregon
    plan: free
    branch: main
    buildCommand: pip install -r requirements.txt
    startCommand: python webhook_server.py
    envVars:
      - key: BOT_TOKEN
        sync: false
      - key: ADMIN_IDS
        sync: false
      - key: CHANNEL_ID
        sync: false
      - key: DB_PATH
        value: /opt/render/project/src/data/catalog.db
      - key: LOG_LEVEL
        value: INFO
      - key: USE_WEBHOOK
        value: "true"
      - key: WEBHOOK_URL
        sync: false
      - key: REDIS_URL
        sync: false
      - key: PREVIEW_TTL_SECONDS
        value: "3600"
      - key: DEBUG_LOG_UPDATES
        value: "false"
    
    disk:
      name: bot-data
      mountPath: /opt/render/project/src/data
      sizeGB: 1
"""
    
    try:
        with open("render.yaml", "w") as f:
            f.write(render_yaml_content)
        print_success("render.yaml generated successfully")
        print_info("Set secret environment variables in Render Dashboard:")
        print_info("  - BOT_TOKEN")
        print_info("  - ADMIN_IDS")
        print_info("  - CHANNEL_ID (optional)")
        print_info("  - WEBHOOK_URL")
        return True
    except Exception as e:
        print_error(f"Failed to generate render.yaml: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Validate Telegram bot configuration before deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validate_config.py --all              # Run all checks
  python validate_config.py --env              # Check .env file only
  python validate_config.py --token            # Validate bot token
  python validate_config.py --render           # Generate render.yaml
        """
    )
    
    parser.add_argument("--all", action="store_true", help="Run all validation checks")
    parser.add_argument("--env", action="store_true", help="Check .env file")
    parser.add_argument("--token", action="store_true", help="Validate bot token")
    parser.add_argument("--admin", action="store_true", help="Validate admin IDs")
    parser.add_argument("--channel", action="store_true", help="Validate channel ID")
    parser.add_argument("--webhook", action="store_true", help="Validate webhook config")
    parser.add_argument("--database", action="store_true", help="Check database path")
    parser.add_argument("--render", action="store_true", help="Generate render.yaml")
    parser.add_argument("--no-api", action="store_true", help="Skip Telegram API validation")
    
    args = parser.parse_args()
    
    # If no specific check is requested, show help
    if not any([args.all, args.env, args.token, args.admin, args.channel, 
                args.webhook, args.database, args.render]):
        parser.print_help()
        sys.exit(0)
    
    # Load .env file
    env_file_ok = True
    if args.all or args.env:
        env_file_ok, _ = check_env_file()
    
    if env_file_ok:
        load_dotenv()
    
    # Get environment variables
    bot_token = os.getenv("BOT_TOKEN")
    admin_ids = os.getenv("ADMIN_IDS")
    channel_id = os.getenv("CHANNEL_ID")
    use_webhook = os.getenv("USE_WEBHOOK")
    webhook_url = os.getenv("WEBHOOK_URL")
    db_path = os.getenv("DB_PATH")
    
    # Run checks
    results = []
    
    if args.all or args.token:
        validate_api = not args.no_api
        results.append(check_bot_token(bot_token, validate_api))
    
    if args.all or args.admin:
        results.append(check_admin_ids(admin_ids))
    
    if args.all or args.channel:
        results.append(check_channel_id(channel_id))
    
    if args.all or args.webhook:
        results.append(check_webhook_config(use_webhook, webhook_url))
    
    if args.all or args.database:
        results.append(check_database_path(db_path))
    
    if args.all or args.render:
        results.append(generate_render_yaml())
    
    # Print summary
    print_header("Validation Summary")
    
    if all(results):
        print_success("All checks passed! ✨")
        print_info("Your configuration is ready for deployment.")
        sys.exit(0)
    else:
        print_error("Some checks failed. Please fix the issues above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
