#!/usr/bin/env python3
"""
Database migration script for multi-bot support.
Adds necessary columns and creates FTS5 search index.
"""
import asyncio
import aiosqlite
import logging
import sys
from pathlib import Path

# Add parent directory to path to import config
sys.path.insert(0, str(Path(__file__).parent.parent))

from configs.config import Config

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def migrate_database():
    """Run database migrations for multi-bot support."""
    db_path = Config.DB_PATH
    logger.info(f"Starting database migration for: {db_path}")
    
    try:
        async with aiosqlite.connect(db_path) as db:
            # Enable WAL mode for better concurrent access
            await db.execute("PRAGMA journal_mode=WAL")
            logger.info("✅ Enabled WAL mode")
            
            # Check if bot_instance column exists in bot_users
            cursor = await db.execute("PRAGMA table_info(bot_users)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'bot_instance' not in column_names:
                logger.info("Adding bot_instance column to bot_users table...")
                await db.execute("""
                    ALTER TABLE bot_users 
                    ADD COLUMN bot_instance INTEGER DEFAULT 1
                """)
                logger.info("✅ Added bot_instance column to bot_users")
            else:
                logger.info("✓ bot_instance column already exists in bot_users")
            
            # Create index for bot_instance
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_bot_users_instance 
                ON bot_users(bot_instance)
            """)
            logger.info("✅ Created index on bot_instance")
            
            # Check if notification_queue has bot_instance column
            cursor = await db.execute("PRAGMA table_info(notification_queue)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'bot_instance' not in column_names:
                logger.info("Adding bot_instance column to notification_queue...")
                await db.execute("""
                    ALTER TABLE notification_queue 
                    ADD COLUMN bot_instance INTEGER
                """)
                logger.info("✅ Added bot_instance column to notification_queue")
            else:
                logger.info("✓ bot_instance column already exists in notification_queue")
            
            # Check if custom_message_queue has bot_instance column
            cursor = await db.execute("PRAGMA table_info(custom_message_queue)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'bot_instance' not in column_names:
                logger.info("Adding bot_instance column to custom_message_queue...")
                await db.execute("""
                    ALTER TABLE custom_message_queue 
                    ADD COLUMN bot_instance INTEGER
                """)
                logger.info("✅ Added bot_instance column to custom_message_queue")
            else:
                logger.info("✓ bot_instance column already exists in custom_message_queue")
            
            # Check if FTS5 table exists
            cursor = await db.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='products_fts'
            """)
            fts_exists = await cursor.fetchone()
            
            if not fts_exists:
                logger.info("Creating FTS5 virtual table for fast search...")
                
                # Create FTS5 table
                await db.execute("""
                    CREATE VIRTUAL TABLE products_fts 
                    USING fts5(caption, content='products', content_rowid='id')
                """)
                logger.info("✅ Created products_fts virtual table")
                
                # Populate FTS table with existing data
                logger.info("Populating FTS table with existing products...")
                await db.execute("""
                    INSERT INTO products_fts(rowid, caption) 
                    SELECT id, caption FROM products WHERE caption IS NOT NULL
                """)
                logger.info("✅ Populated FTS table")
                
                # Create triggers to keep FTS in sync
                await db.execute("""
                    CREATE TRIGGER products_fts_insert AFTER INSERT ON products BEGIN
                        INSERT INTO products_fts(rowid, caption) 
                        VALUES (new.id, new.caption);
                    END
                """)
                
                await db.execute("""
                    CREATE TRIGGER products_fts_update AFTER UPDATE ON products BEGIN
                        UPDATE products_fts 
                        SET caption = new.caption 
                        WHERE rowid = new.id;
                    END
                """)
                
                await db.execute("""
                    CREATE TRIGGER products_fts_delete AFTER DELETE ON products BEGIN
                        DELETE FROM products_fts WHERE rowid = old.id;
                    END
                """)
                logger.info("✅ Created FTS sync triggers")
            else:
                logger.info("✓ FTS5 table already exists")
            
            await db.commit()
            
            # Display migration summary
            cursor = await db.execute("SELECT COUNT(*) FROM bot_users")
            user_count = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM products")
            product_count = (await cursor.fetchone())[0]
            
            logger.info("\n" + "="*50)
            logger.info("Migration Summary:")
            logger.info(f"  Database: {db_path}")
            logger.info(f"  Total users: {user_count}")
            logger.info(f"  Total products: {product_count}")
            logger.info(f"  WAL mode: Enabled")
            logger.info(f"  FTS5 search: Enabled")
            logger.info(f"  Bot instance tracking: Enabled")
            logger.info("="*50)
            logger.info("✅ Migration completed successfully!")
            
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    logger.info("Multi-Bot Database Migration Tool")
    logger.info("="*50)
    asyncio.run(migrate_database())
