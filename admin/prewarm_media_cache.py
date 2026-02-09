#!/usr/bin/env python
"""
Admin script to pre-warm the bot-specific file_id cache.

This script iterates through all products in the database and, for each configured
secondary bot, sends the media to an admin-private chat to generate and cache 
bot-specific file_ids. This helps reduce "Wrong file identifier" errors when
secondary bots try to display products to users.

Usage:
    python admin/prewarm_media_cache.py

Requirements:
    - At least one admin user must have started a conversation with each bot
      (send /start to each bot) to receive the cache-warming messages
    - The script will delete cache-warming messages after extracting file_ids
"""
import os
import sys
import asyncio
import logging
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from configs.config import Config
from telegram import Bot
from telegram.error import TelegramError, Forbidden

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def get_admin_chat_id() -> Optional[int]:
    """Get the first admin user ID for cache-warming messages."""
    admin_ids = Config.ADMIN_IDS
    if not admin_ids:
        logger.error("No admin IDs configured in ADMIN_IDS")
        return None
    return admin_ids[0]


async def warm_cache_for_product(
    bot: Bot,
    bot_key: str,
    db: Database,
    product: dict,
    admin_chat_id: int
) -> bool:
    """
    Warm the cache for a single product on a single bot.
    
    Args:
        bot: Telegram Bot instance
        bot_key: Bot environment key (e.g., 'BOT_TOKEN_1')
        db: Database instance
        product: Product dictionary from database
        admin_chat_id: Admin user ID to send cache-warming message to
        
    Returns:
        True if cache was warmed successfully, False otherwise
    """
    product_id = product['id']
    file_id = product['file_id']
    file_type = product['file_type']
    
    # Check if we already have a cached file_id for this bot+product
    cached_file_id = await db.get_bot_file_id(bot_key, product_id)
    if cached_file_id:
        logger.debug(f"[{bot_key}] Product {product_id} already cached, skipping")
        return True
    
    try:
        # Send the media to admin chat to get bot-specific file_id
        sent_message = None
        
        if file_type == "photo":
            sent_message = await bot.send_photo(
                chat_id=admin_chat_id,
                photo=file_id,
                caption=f"Cache warming: Product #{product_id}"
            )
        elif file_type == "video":
            sent_message = await bot.send_video(
                chat_id=admin_chat_id,
                video=file_id,
                caption=f"Cache warming: Product #{product_id}"
            )
        elif file_type == "document":
            sent_message = await bot.send_document(
                chat_id=admin_chat_id,
                document=file_id,
                caption=f"Cache warming: Product #{product_id}"
            )
        elif file_type == "animation":
            sent_message = await bot.send_animation(
                chat_id=admin_chat_id,
                animation=file_id,
                caption=f"Cache warming: Product #{product_id}"
            )
        else:
            logger.warning(f"[{bot_key}] Unsupported file_type: {file_type} for product {product_id}")
            return False
        
        if not sent_message:
            logger.warning(f"[{bot_key}] No message returned for product {product_id}")
            return False
        
        # Extract bot-specific file_id from sent message
        bot_file_id = None
        if file_type == "photo" and sent_message.photo:
            bot_file_id = sent_message.photo[-1].file_id
        elif file_type == "video" and sent_message.video:
            bot_file_id = sent_message.video.file_id
        elif file_type == "document" and sent_message.document:
            bot_file_id = sent_message.document.file_id
        elif file_type == "animation" and sent_message.animation:
            bot_file_id = sent_message.animation.file_id
        
        if bot_file_id:
            # Cache the bot-specific file_id
            success = await db.set_bot_file_id(bot_key, product_id, bot_file_id)
            if success:
                logger.info(
                    f"[{bot_key}] Cached file_id for product {product_id}: "
                    f"{bot_file_id[:30]}..."
                )
            
            # Delete the cache-warming message to keep admin chat clean
            try:
                await bot.delete_message(
                    chat_id=admin_chat_id,
                    message_id=sent_message.message_id
                )
            except Exception as e:
                logger.warning(f"[{bot_key}] Could not delete message: {e}")
            
            return success
        else:
            logger.warning(
                f"[{bot_key}] Could not extract file_id from sent message "
                f"for product {product_id}"
            )
            return False
        
    except Forbidden as e:
        logger.error(
            f"[{bot_key}] Forbidden error for product {product_id}: {e}\n"
            f"Admin (user {admin_chat_id}) must start a conversation with this bot "
            f"by sending /start to it."
        )
        return False
    except TelegramError as e:
        logger.error(f"[{bot_key}] Telegram error for product {product_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"[{bot_key}] Unexpected error for product {product_id}: {e}")
        return False


async def prewarm_cache():
    """Main function to pre-warm the file_id cache for all bots and products."""
    logger.info("=" * 60)
    logger.info("Starting bot file_id cache pre-warming")
    logger.info("=" * 60)
    
    # Initialize database
    db = Database()
    await db.init_db()
    
    # Get admin chat ID
    admin_chat_id = await get_admin_chat_id()
    if not admin_chat_id:
        logger.error("Cannot proceed without admin chat ID")
        return
    
    logger.info(f"Using admin chat ID: {admin_chat_id}")
    
    # Get all products
    products = await db.get_all_products()
    logger.info(f"Found {len(products)} total products")
    
    if not products:
        logger.info("No products to process")
        return
    
    # Get all bot tokens (skip primary if desired, or include it)
    bot_tokens = Config.BOT_TOKENS
    if not bot_tokens:
        logger.error("No bot tokens configured")
        return
    
    logger.info(f"Found {len(bot_tokens)} bot token(s)")
    
    # Process each bot
    total_cached = 0
    total_failed = 0
    
    for bot_index, token in enumerate(bot_tokens):
        # Determine bot key
        if bot_index == 0:
            bot_key = 'BOT_TOKEN'
        else:
            bot_key = f'BOT_TOKEN_{bot_index}'
        
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Processing bot: {bot_key} (token #{bot_index})")
        logger.info(f"{'=' * 60}")
        
        # Create bot instance
        bot = Bot(token=token)
        
        # Get bot info
        try:
            bot_info = await bot.get_me()
            logger.info(f"Bot username: @{bot_info.username}")
        except Exception as e:
            logger.error(f"Failed to get bot info: {e}")
            continue
        
        # Process each product
        cached_count = 0
        failed_count = 0
        
        for product_idx, product in enumerate(products, 1):
            logger.debug(
                f"[{bot_key}] Processing product {product_idx}/{len(products)}: "
                f"ID={product['id']}"
            )
            
            success = await warm_cache_for_product(
                bot, bot_key, db, product, admin_chat_id
            )
            
            if success:
                cached_count += 1
            else:
                failed_count += 1
            
            # Small delay to avoid rate limits
            if product_idx % 10 == 0:
                await asyncio.sleep(1)
        
        logger.info(
            f"[{bot_key}] Summary: {cached_count} cached, {failed_count} failed"
        )
        
        total_cached += cached_count
        total_failed += failed_count
    
    logger.info(f"\n{'=' * 60}")
    logger.info("Cache pre-warming complete")
    logger.info(f"Total cached: {total_cached}")
    logger.info(f"Total failed: {total_failed}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(prewarm_cache())
