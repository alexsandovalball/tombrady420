#!/usr/bin/env python3
"""
Migration script to prune users and products associated with inactive/deleted bots.

This script identifies and removes:
- Users who started bots that are no longer in the active pool
- Products associated with inactive bots
- Pending notifications and custom messages for these users

Usage:
    # Dry run (preview what would be deleted)
    python scripts/migrate_prune_inactive_bots.py

    # Actually perform the pruning
    python scripts/migrate_prune_inactive_bots.py --confirm

The script compares bot usernames in the database against the list of active
bots configured in the environment (BOT_TOKEN, BOT_TOKEN_1, etc.).
"""
import asyncio
import argparse
import logging
import sys
import os
from typing import List

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Import after setting up logging
from configs.config import Config
from database import Database

async def get_active_bot_usernames() -> List[str]:
    """
    Get list of active bot usernames from configured tokens.
    
    Returns:
        List of bot usernames (without @ prefix)
    """
    from telegram import Bot
    
    active_bots = []
    tokens = Config.BOT_TOKENS
    
    if not tokens:
        logger.warning("No bot tokens configured!")
        return []
    
    logger.info(f"Checking {len(tokens)} configured bot token(s)...")
    
    for idx, token in enumerate(tokens):
        try:
            # Create a temporary bot instance to get username
            bot = Bot(token=token)
            # Use async context manager for proper cleanup
            async with bot:
                me = await bot.get_me()
                username = me.username
                active_bots.append(username)
                logger.info(f"  Bot {idx + 1}: @{username}")
        except Exception as e:
            logger.error(f"  Bot {idx + 1}: Error getting bot info - {e}")
            # Continue with other bots even if one fails
    
    return active_bots


async def main():
    """Main migration function."""
    parser = argparse.ArgumentParser(
        description="Prune users and products from inactive/deleted bots"
    )
    parser.add_argument(
        '--confirm',
        action='store_true',
        help='Actually perform the pruning (default is dry run)'
    )
    parser.add_argument(
        '--db-path',
        default=None,
        help='Path to database file (default: from config)'
    )
    
    args = parser.parse_args()
    
    # Initialize database
    db = Database(db_path=args.db_path)
    
    try:
        # Validate config
        Config.validate()
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        return 1
    
    # Get active bot usernames
    logger.info("=" * 60)
    logger.info("STEP 1: Identifying active bots")
    logger.info("=" * 60)
    
    active_bots = await get_active_bot_usernames()
    
    if not active_bots:
        logger.error("No active bots found! Cannot proceed with pruning.")
        logger.error("Please check your BOT_TOKEN configuration.")
        return 1
    
    logger.info(f"\n✅ Found {len(active_bots)} active bot(s):")
    for bot in sorted(active_bots):
        logger.info(f"  • @{bot}")
    
    # Get database statistics before pruning
    logger.info("\n" + "=" * 60)
    logger.info("STEP 2: Analyzing database")
    logger.info("=" * 60)
    
    all_bot_usernames = await db.get_bot_usernames()
    logger.info(f"\nBot usernames in database: {len(all_bot_usernames)}")
    
    active_bots_lower = [bot.lower() for bot in active_bots]
    inactive_bots = [bot for bot in all_bot_usernames if bot.lower() not in active_bots_lower]
    
    if inactive_bots:
        logger.warning(f"\n⚠️  Found {len(inactive_bots)} INACTIVE bot(s) with users:")
        for bot in sorted(inactive_bots):
            count = await db.get_users_count_by_bot(bot)
            logger.warning(f"  • @{bot}: {count} users")
    else:
        logger.info("\n✅ No inactive bots found in database.")
    
    # Perform pruning (dry run or actual)
    logger.info("\n" + "=" * 60)
    if args.confirm:
        logger.info("STEP 3: PRUNING (ACTUAL - DELETING DATA)")
    else:
        logger.info("STEP 3: DRY RUN (preview only, no changes)")
    logger.info("=" * 60)
    
    stats = await db.prune_inactive_bot_users(active_bots, dry_run=not args.confirm)
    
    if stats['users'] == 0:
        logger.info("\n✅ No data to prune. Database is clean.")
    else:
        logger.info(f"\n{'[DRY RUN] Would delete:' if not args.confirm else 'DELETED:'}")
        logger.info(f"  • Users: {stats['users']}")
        logger.info(f"  • Products: {stats['products']}")
        logger.info(f"  • Pending Notifications: {stats['notifications']}")
        logger.info(f"  • Custom Messages: {stats['custom_messages']}")
        
        if not args.confirm:
            logger.info("\n" + "⚠️ " * 20)
            logger.info("This was a DRY RUN. No changes were made.")
            logger.info("To actually perform the pruning, run:")
            logger.info("  python migrate_prune_inactive_bots.py --confirm")
            logger.info("⚠️ " * 20)
        else:
            logger.info("\n" + "✅ " * 20)
            logger.info("Pruning completed successfully!")
            logger.info("✅ " * 20)
    
    logger.info("\n" + "=" * 60)
    logger.info("MIGRATION COMPLETE")
    logger.info("=" * 60)
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("\n\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n\nFatal error: {e}", exc_info=True)
        sys.exit(1)
