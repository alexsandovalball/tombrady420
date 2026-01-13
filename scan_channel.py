"""
Channel scanner utility for importing existing channel messages.
This script uses Pyrogram to scan a channel and import all media messages
that are not already in the database.

This is a one-time utility to run when:
- Setting up the bot for the first time
- After losing the database
- After a redeploy where the database was reset

Requirements:
- Pyrogram library installed (pip install pyrogram)
- API_ID and API_HASH from https://my.telegram.org/apps
- The channel must be public or you must be a member
"""
import os
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

try:
    from pyrogram import Client
    from pyrogram.errors import FloodWait
except ImportError:
    print("ERROR: Pyrogram is not installed.")
    print("Install it with: pip install pyrogram")
    print("You also need TgCrypto for better performance: pip install tgcrypto")
    exit(1)

from database import Database
from utils.helpers import get_channel_id, get_channel_username

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db = Database()


async def scan_channel():
    """
    Scan the channel and import all media messages that are not in the database.
    """
    # Get API credentials
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    
    if not api_id or not api_hash:
        logger.error(
            "API_ID and API_HASH are required!\n"
            "Get them from https://my.telegram.org/apps\n"
            "Add them to your .env file:\n"
            "API_ID=your_api_id\n"
            "API_HASH=your_api_hash"
        )
        return
    
    # Get channel info
    channel_id = get_channel_id()
    channel_username = get_channel_username()
    
    if not channel_id and not channel_username:
        logger.error("CHANNEL_ID or CHANNEL_USERNAME must be set in .env")
        return
    
    channel_identifier = channel_id or channel_username
    
    logger.info(f"Starting channel scan for: {channel_identifier}")
    
    # Initialize Pyrogram client
    async with Client("scanner_session", api_id=api_id, api_hash=api_hash) as app:
        # Initialize database
        await db.init_db()
        
        total_messages = 0
        imported_messages = 0
        skipped_no_media = 0
        skipped_exists = 0
        skipped_ignored = 0
        
        # Iterate through channel messages
        async for message in app.get_chat_history(channel_identifier):
            try:
                total_messages += 1
                
                # Skip messages without media
                if not (message.photo or message.video or message.document or 
                       message.animation or message.audio):
                    skipped_no_media += 1
                    continue
                
                # Check if message is in ignore list
                if await db.is_message_ignored(message.id, message.chat.id):
                    logger.debug(f"Message {message.id} is in ignore list, skipping")
                    skipped_ignored += 1
                    continue
                
                # Check if message already exists in database
                existing = await db.get_product_by_message(message.id, message.chat.id)
                if existing:
                    logger.debug(f"Message {message.id} already exists in database")
                    skipped_exists += 1
                    continue
                
                # Extract file information
                file_id = None
                file_type = None
                
                if message.photo:
                    file_id = message.photo.file_id
                    file_type = "photo"
                elif message.video:
                    file_id = message.video.file_id
                    file_type = "video"
                elif message.document:
                    file_id = message.document.file_id
                    file_type = "document"
                elif message.animation:
                    file_id = message.animation.file_id
                    file_type = "animation"
                elif message.audio:
                    file_id = message.audio.file_id
                    file_type = "audio"
                
                if not file_id:
                    continue
                
                # Get caption
                caption = message.caption or message.text or ""
                
                # Handle media groups
                # Note: In Pyrogram, media_group_id is None when message isn't part of a group
                media_group_id = getattr(message, 'media_group_id', None)
                
                # Add product to database without category (needs admin categorization)
                try:
                    product_id = await db.add_product(
                        file_id=file_id,
                        file_type=file_type,
                        caption=caption,
                        message_id=message.id,
                        chat_id=message.chat.id,
                        media_group_id=media_group_id,
                        category=None,
                        subcategory=None
                    )
                    
                    if product_id > 0:
                        imported_messages += 1
                        logger.info(
                            f"Imported message {message.id} as product {product_id} "
                            f"({file_type})"
                        )
                    else:
                        skipped_exists += 1
                except Exception as e:
                    logger.error(f"Error importing message {message.id}: {e}")
                
                # Progress update every 50 messages
                if total_messages % 50 == 0:
                    logger.info(
                        f"Progress: {total_messages} messages scanned, "
                        f"{imported_messages} imported"
                    )
                
                # Small delay to avoid rate limiting
                # Note: This is conservative. If scanning large channels (10K+ messages),
                # consider reducing to 0.05 or removing if FloodWait handles it
                await asyncio.sleep(0.1)
                
            except FloodWait as e:
                logger.warning(f"Rate limited by Telegram. Waiting {e.value} seconds...")
                await asyncio.sleep(e.value)
                # Continue scanning after waiting
                continue
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                # Continue with next message
                continue
        
        # Summary
        logger.info("=" * 60)
        logger.info("CHANNEL SCAN COMPLETE")
        logger.info(f"Total messages scanned: {total_messages}")
        logger.info(f"Messages imported: {imported_messages}")
        logger.info(f"Skipped (no media): {skipped_no_media}")
        logger.info(f"Skipped (already exists): {skipped_exists}")
        logger.info(f"Skipped (in ignore list): {skipped_ignored}")
        logger.info("=" * 60)
        
        if imported_messages > 0:
            logger.info(
                "\n⚠️  IMPORTANT: Newly imported products need categorization!\n"
                "Run /recategorize command in the bot to send categorization "
                "requests to admins."
            )


if __name__ == "__main__":
    asyncio.run(scan_channel())
