# check_db.py
"""
Simple inspection tool for the products table.

Usage:
    python check_db.py                 # uses catalog.db and limit 20
    python check_db.py --db my.db --limit 50
    python check_db.py --media-group MG123
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from typing import Optional

DEFAULT_DB = "catalog.db"
DEFAULT_LIMIT = 20

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect recent rows in products table")
    p.add_argument("--db", "-d", default=DEFAULT_DB, help="Path to SQLite database file")
    p.add_argument("--limit", "-n", type=int, default=DEFAULT_LIMIT, help="Number of rows to fetch (positive integer)")
    p.add_argument("--media-group", "-m", dest="media_group", default=None, help="Optional media_group_id to filter by")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p.parse_args()


def validate_limit(limit: int) -> int:
    if limit is None or limit <= 0:
        raise ValueError("limit must be a positive integer")
    return int(limit)


def fetch_products(conn: sqlite3.Connection, limit: int, media_group: Optional[str] = None):
    """
    Fetch recent product rows. If media_group is provided, filter by it.
    Returns a list of sqlite3.Row objects.
    """
    cur = conn.cursor()
    if media_group:
        # Use parameterized query for the media_group filter
        sql = """
            SELECT id, message_id, chat_id, media_group_id, file_id, file_unique_id, file_type, caption, caption_parse_mode
            FROM products
            WHERE media_group_id = ?
            ORDER BY id DESC
            LIMIT {}
        """.format(limit)
        cur.execute(sql, (media_group,))
    else:
        sql = """
            SELECT id, message_id, chat_id, media_group_id, file_id, file_unique_id, file_type, caption, caption_parse_mode
            FROM products
            ORDER BY id DESC
            LIMIT {}
        """.format(limit)
        cur.execute(sql)
    rows = cur.fetchall()
    return rows


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        limit = validate_limit(args.limit)
    except Exception as e:
        logger.error("Invalid --limit value: %s", e)
        return 2

    db_path = args.db
    try:
        # Use row factory for named access and easier JSON conversion
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        logger.exception("Failed to open database %s: %s", db_path, e)
        return 3

    try:
        with conn:
            rows = fetch_products(conn, limit=limit, media_group=args.media_group)
            if not rows:
                logger.info("No rows returned (db=%s limit=%d media_group=%s)", db_path, limit, args.media_group)
                return 0

            for r in rows:
                try:
                    # Convert sqlite3.Row to plain dict for readable JSON output
                    d = {k: r[k] for k in r.keys()}
                    print(json.dumps(d, ensure_ascii=False))
                except Exception:
                    # Fallback to tuple print if conversion fails
                    print(tuple(r))
    except Exception as e:
        logger.exception("Error while querying database: %s", e)
        return 4
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())