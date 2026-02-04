#!/usr/bin/env python3
"""
Database migration script to rename 'preferred_language' column to 'language'.
This migration fixes the mismatch between the database schema and the code
after PR #34 (multi-language translation support).
"""
import sqlite3
import sys
import os

def migrate_database(db_path):
    """Migrate the database to rename preferred_language to language."""
    print(f"Starting migration for database: {db_path}")
    
    # Check if database exists
    if not os.path.exists(db_path):
        print(f"Error: Database file not found: {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check current schema
        cursor.execute("PRAGMA table_info(bot_users)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        print(f"Current columns in bot_users: {column_names}")
        
        # Check if migration is needed
        if 'language' in column_names and 'preferred_language' not in column_names:
            print("Migration not needed - 'language' column already exists")
            conn.close()
            return True
        
        if 'preferred_language' not in column_names:
            print("Error: 'preferred_language' column not found")
            conn.close()
            return False
        
        # SQLite doesn't support direct column rename, so we need to:
        # 1. Create a new table with the correct schema
        # 2. Copy data from old table to new table
        # 3. Drop old table
        # 4. Rename new table to old table name
        
        print("Creating backup of bot_users table...")
        cursor.execute("""
            CREATE TABLE bot_users_backup AS SELECT * FROM bot_users
        """)
        
        print("Dropping old bot_users table...")
        cursor.execute("DROP TABLE bot_users")
        
        print("Creating new bot_users table with correct schema...")
        cursor.execute("""
            CREATE TABLE bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                interaction_count INTEGER DEFAULT 1,
                language TEXT DEFAULT 'en',
                is_blocked INTEGER DEFAULT 0,
                notifications_enabled INTEGER DEFAULT 1
            )
        """)
        
        print("Copying data from backup to new table...")
        cursor.execute("""
            INSERT INTO bot_users (
                user_id, username, first_name, last_name, 
                first_seen, last_seen, interaction_count, 
                language, is_blocked, notifications_enabled
            )
            SELECT 
                user_id, username, first_name, last_name,
                first_seen, last_seen, interaction_count,
                preferred_language, is_blocked, notifications_enabled
            FROM bot_users_backup
        """)
        
        rows_migrated = cursor.rowcount
        print(f"Migrated {rows_migrated} rows")
        
        print("Dropping backup table...")
        cursor.execute("DROP TABLE bot_users_backup")
        
        # Verify the migration
        cursor.execute("PRAGMA table_info(bot_users)")
        new_columns = cursor.fetchall()
        new_column_names = [col[1] for col in new_columns]
        
        print(f"New columns in bot_users: {new_column_names}")
        
        if 'language' in new_column_names and 'preferred_language' not in new_column_names:
            print("Migration successful!")
            conn.commit()
            conn.close()
            return True
        else:
            print("Migration verification failed!")
            conn.rollback()
            conn.close()
            return False
            
    except Exception as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        conn.close()
        return False

if __name__ == "__main__":
    # Default database path
    db_path = "catalog.db"
    
    # Allow custom database path as argument
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    success = migrate_database(db_path)
    sys.exit(0 if success else 1)
