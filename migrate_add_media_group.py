# migrate_add_media_group.py
"""
Add a media_group_id column to the products table if it does not exist.

Usage:
    python migrate_add_media_group.py            # runs against catalog.db and applies changes
    python migrate_add_media_group.py --db my.db --dry-run
    python migrate_add_media_group.py --db my.db --create-index
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Optional

DEFAULT_DB = "catalog.db"
BACKUP_SUFFIX = ".bak"

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Add media_group_id column to products table (idempotent).")
    p.add_argument("--db", "-d", default=DEFAULT_DB, help="Path to SQLite database file")
    p.add_argument("--dry-run", action="store_true", help="Show actions but do not modify the database")
    p.add_argument("--create-index", action="store_true", help="Create an index on media_group_id after adding the column")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p.parse_args()


def table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute("PRAGMA table_info(%s)" % table)
    cols = [row[1] for row in cur.fetchall()]
    return column in cols


def add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    """
    Add a column to a table. SQLite supports ALTER TABLE ADD COLUMN for simple additions.
    """
    sql = f"ALTER TABLE {table} ADD COLUMN {column_def}"
    conn.execute(sql)


def create_index_if_missing(conn: sqlite3.Connection, table: str, column: str, index_name: Optional[str] = None) -> None:
    if index_name is None:
        index_name = f"idx_{table}_{column}"
    # Check if index exists
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (index_name,))
    if cur.fetchone():
        logger.info("Index %s already exists", index_name)
        return
    conn.execute(f"CREATE INDEX {index_name} ON {table}({column})")
    logger.info("Created index %s on %s(%s)", index_name, table, column)


def backup_db(db_path: Path) -> Path:
    backup_path = db_path.with_suffix(db_path.suffix + BACKUP_SUFFIX)
    shutil.copy2(db_path, backup_path)
    logger.info("Created backup: %s", backup_path)
    return backup_path


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("Database file not found: %s", db_path)
        return 2

    # Dry-run: open DB read-only to inspect schema
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception as e:
        logger.exception("Failed to open database %s: %s", db_path, e)
        return 3

    try:
        if table_has_column(conn, "products", "media_group_id"):
            logger.info("Column 'media_group_id' already exists in 'products' table. No action required.")
            # Optionally create index if requested and missing
            if args.create_index and not args.dry_run:
                try:
                    create_index_if_missing(conn, "products", "media_group_id")
                except Exception:
                    logger.exception("Failed to create index on media_group_id")
            return 0

        logger.info("Column 'media_group_id' not found in 'products' table.")
        if args.dry_run:
            logger.info("Dry-run mode: would add column 'media_group_id TEXT' to 'products' and optionally create index=%s", args.create_index)
            return 0

        # Backup DB before making changes
        try:
            backup_db(db_path)
        except Exception:
            logger.exception("Failed to create backup; aborting migration")
            return 4

        # Perform migration inside a transaction
        try:
            with conn:
                add_column(conn, "products", "media_group_id TEXT")
                logger.info("Added column 'media_group_id' to 'products' table")
                if args.create_index:
                    create_index_if_missing(conn, "products", "media_group_id")
        except Exception:
            logger.exception("Migration failed; database should be unchanged due to transaction rollback")
            return 5

    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info("Migration completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())