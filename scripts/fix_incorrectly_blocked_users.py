#!/usr/bin/env python3
"""
Script to identify and optionally unblock users who may have been incorrectly blocked.

This script helps fix the issue where users were globally blocked due to:
1. Temporary errors being treated as permanent
2. Bot-specific errors causing global blocks
3. Overly aggressive error detection

Usage:
    # Dry run (show what would be unblocked):
    python scripts/fix_incorrectly_blocked_users.py --dry-run
    
    # Actually unblock users:
    python scripts/fix_incorrectly_blocked_users.py
    
    # Unblock specific user:
    python scripts/fix_incorrectly_blocked_users.py --user-id 12345

The script will:
1. Find all globally blocked users (is_blocked = 1)
2. Check if they have recent interactions (last 30 days)
3. Check if they're only invalid for specific bots (not globally)
4. Optionally unblock them and log the action
"""
import os
import sys
import asyncio
import argparse
import logging
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite
from database import Database
from configs.config import Config

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def find_incorrectly_blocked_users(db: Database) -> list:
    """
    Find users who are globally blocked but may have been blocked incorrectly.
    
    Returns:
        List of tuples: (user_id, username, last_seen, is_blocked, notifications_enabled)
    """
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("""
            SELECT user_id, username, first_name, last_seen, is_blocked, notifications_enabled
            FROM bot_users
            WHERE is_blocked = 1
            ORDER BY last_seen DESC
        """)
        rows = await cursor.fetchall()
        
        users = []
        for row in rows:
            user_id, username, first_name, last_seen, is_blocked, notifications_enabled = row
            users.append({
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_seen": last_seen,
                "is_blocked": is_blocked,
                "notifications_enabled": notifications_enabled
            })
        
        return users


async def check_bot_specific_invalidations(db: Database, user_id: int) -> list:
    """
    Check if user has bot-specific invalidations.
    
    Returns:
        List of tuples: (bot_id, reason, created_at)
    """
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("""
            SELECT bot_id, reason, created_at, is_active
            FROM bot_invalidations
            WHERE user_id = ? AND is_active = 1
        """, (user_id,))
        rows = await cursor.fetchall()
        
        invalidations = []
        for row in rows:
            bot_id, reason, created_at, is_active = row
            invalidations.append({
                "bot_id": bot_id,
                "reason": reason,
                "created_at": created_at,
                "is_active": is_active
            })
        
        return invalidations


async def unblock_user_safe(db: Database, user_id: int, dry_run: bool = False) -> bool:
    """
    Safely unblock a user by setting is_blocked = 0.
    
    Args:
        db: Database instance
        user_id: User ID to unblock
        dry_run: If True, don't actually unblock, just log
        
    Returns:
        True if unblocked (or would be unblocked in dry run), False otherwise
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would unblock user {user_id}")
        return True
    else:
        await db.unblock_user(user_id)
        logger.info(f"[UNBLOCKED] User {user_id} has been unblocked")
        return True


async def main():
    parser = argparse.ArgumentParser(description="Fix incorrectly blocked users")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--user-id", type=int, help="Specific user ID to check/unblock")
    parser.add_argument("--days", type=int, default=30, help="Consider users active in last N days (default: 30)")
    args = parser.parse_args()
    
    # Initialize database
    db = Database()
    await db.init_db()
    
    logger.info("=" * 80)
    logger.info("INCORRECTLY BLOCKED USERS FIXER")
    logger.info("=" * 80)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
    
    if args.user_id:
        # Check specific user
        logger.info(f"Checking user {args.user_id}...")
        
        is_blocked = await db.is_user_blocked(args.user_id)
        if not is_blocked:
            logger.info(f"User {args.user_id} is NOT blocked. Nothing to do.")
            return
        
        logger.info(f"User {args.user_id} is globally blocked.")
        
        # Check bot-specific invalidations
        invalidations = await check_bot_specific_invalidations(db, args.user_id)
        if invalidations:
            logger.info(f"User has {len(invalidations)} bot-specific invalidations:")
            for inv in invalidations:
                logger.info(f"  - Bot: {inv['bot_id']}, Reason: {inv['reason']}, Date: {inv['created_at']}")
        else:
            logger.info("User has NO bot-specific invalidations (may have been globally blocked by mistake)")
        
        # Ask for confirmation
        if not args.dry_run:
            response = input(f"Unblock user {args.user_id}? (yes/no): ")
            if response.lower() == "yes":
                await unblock_user_safe(db, args.user_id, dry_run=False)
            else:
                logger.info("Cancelled.")
        else:
            await unblock_user_safe(db, args.user_id, dry_run=True)
    
    else:
        # Check all blocked users
        logger.info("Finding all globally blocked users...")
        blocked_users = await find_incorrectly_blocked_users(db)
        
        logger.info(f"Found {len(blocked_users)} globally blocked users")
        logger.info("")
        
        # Analyze each user
        candidates_for_unblock = []
        
        for user in blocked_users:
            user_id = user["user_id"]
            username = user["username"] or "N/A"
            first_name = user["first_name"] or "N/A"
            last_seen = user["last_seen"]
            
            # Parse last_seen
            try:
                last_seen_dt = datetime.fromisoformat(last_seen) if last_seen else None
            except:
                last_seen_dt = None
            
            # Check bot-specific invalidations
            invalidations = await check_bot_specific_invalidations(db, user_id)
            
            # Determine if candidate for unblock
            is_candidate = False
            reason = []
            
            # If user has ONLY bot-specific invalidations but is globally blocked
            if invalidations:
                is_candidate = True
                reason.append(f"has {len(invalidations)} bot-specific invalidations but globally blocked")
            
            # If user was recently active (last 30 days) but is blocked
            if last_seen_dt and (datetime.now() - last_seen_dt) < timedelta(days=args.days):
                is_candidate = True
                reason.append(f"was active {(datetime.now() - last_seen_dt).days} days ago")
            
            if is_candidate:
                candidates_for_unblock.append({
                    "user": user,
                    "invalidations": invalidations,
                    "reason": reason
                })
                
                logger.info(f"User {user_id} (@{username}, {first_name}):")
                logger.info(f"  Last seen: {last_seen}")
                logger.info(f"  Bot-specific invalidations: {len(invalidations)}")
                for inv in invalidations:
                    logger.info(f"    - Bot: {inv['bot_id']}, Reason: {inv['reason']}")
                logger.info(f"  Candidate for unblock: {'; '.join(reason)}")
                logger.info("")
        
        # Summary
        logger.info("=" * 80)
        logger.info(f"SUMMARY: {len(candidates_for_unblock)} users are candidates for unblocking")
        logger.info("=" * 80)
        
        if candidates_for_unblock and not args.dry_run:
            response = input(f"Unblock all {len(candidates_for_unblock)} users? (yes/no): ")
            if response.lower() == "yes":
                for candidate in candidates_for_unblock:
                    user_id = candidate["user"]["user_id"]
                    await unblock_user_safe(db, user_id, dry_run=False)
                logger.info(f"Unblocked {len(candidates_for_unblock)} users")
            else:
                logger.info("Cancelled.")
        elif candidates_for_unblock and args.dry_run:
            logger.info(f"[DRY RUN] Would unblock {len(candidates_for_unblock)} users")
        else:
            logger.info("No users to unblock.")


if __name__ == "__main__":
    asyncio.run(main())
