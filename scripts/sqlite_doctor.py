"""Inspect and optionally repair common SQLite catalog corruption cases.

Default mode is read-only diagnosis. Run against an offline copy first:

    python scripts/sqlite_doctor.py /path/to/catalog.db

Repair modes:

    python scripts/sqlite_doctor.py /path/to/catalog.db --reindex
    python scripts/sqlite_doctor.py /path/to/catalog.db --rebuild-product-search
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


PRODUCT_SEARCH_OBJECTS = (
    "product_search",
    "products_ai_search",
    "products_ad_search",
    "products_au_search",
)


def print_rows(title: str, rows: list[tuple]) -> None:
    print(f"\n== {title} ==")
    if not rows:
        print("(none)")
        return
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return list(conn.execute(sql, params).fetchall())


def run_checks(conn: sqlite3.Connection) -> None:
    print_rows("PRAGMA quick_check", fetch_all(conn, "PRAGMA quick_check"))
    print_rows("PRAGMA integrity_check", fetch_all(conn, "PRAGMA integrity_check"))
    print_rows(
        "product objects",
        fetch_all(
            conn,
            """
            SELECT type, name, tbl_name
            FROM sqlite_master
            WHERE tbl_name IN ('products', 'product_search')
               OR name LIKE 'idx_products%'
               OR name IN ('products_ai_search', 'products_ad_search', 'products_au_search')
            ORDER BY type, name
            """,
        ),
    )
    print_rows("products count", fetch_all(conn, "SELECT count(*) FROM products"))
    try:
        print_rows("product_search count", fetch_all(conn, "SELECT count(*) FROM product_search"))
    except sqlite3.DatabaseError as exc:
        print(f"\n== product_search count ==\nERROR: {exc}")


def reindex(conn: sqlite3.Connection) -> None:
    conn.execute("REINDEX")
    conn.commit()
    print("\nREINDEX completed.")


def rebuild_product_search(conn: sqlite3.Connection) -> None:
    for name in PRODUCT_SEARCH_OBJECTS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute("DROP TABLE IF EXISTS product_search")
    conn.execute(
        """
        CREATE VIRTUAL TABLE product_search USING fts5(
            caption,
            category,
            subcategory,
            content='products',
            content_rowid='id',
            tokenize='unicode61'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER products_ai_search AFTER INSERT ON products BEGIN
            INSERT INTO product_search(rowid, caption, category, subcategory)
            VALUES (new.id, coalesce(new.caption, ''), coalesce(new.category, ''), coalesce(new.subcategory, ''));
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER products_ad_search AFTER DELETE ON products BEGIN
            INSERT INTO product_search(product_search, rowid, caption, category, subcategory)
            VALUES ('delete', old.id, coalesce(old.caption, ''), coalesce(old.category, ''), coalesce(old.subcategory, ''));
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER products_au_search AFTER UPDATE ON products BEGIN
            INSERT INTO product_search(product_search, rowid, caption, category, subcategory)
            VALUES ('delete', old.id, coalesce(old.caption, ''), coalesce(old.category, ''), coalesce(old.subcategory, ''));
            INSERT INTO product_search(rowid, caption, category, subcategory)
            VALUES (new.id, coalesce(new.caption, ''), coalesce(new.category, ''), coalesce(new.subcategory, ''));
        END
        """
    )
    conn.execute(
        """
        INSERT INTO product_search(rowid, caption, category, subcategory)
        SELECT id, coalesce(caption, ''), coalesce(category, ''), coalesce(subcategory, '')
        FROM products
        """
    )
    conn.commit()
    print("\nproduct_search rebuilt.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--rebuild-product-search", action="store_true")
    args = parser.parse_args()

    if not args.db_path.exists():
        raise SystemExit(f"database not found: {args.db_path}")

    conn = sqlite3.connect(args.db_path)
    try:
        run_checks(conn)
        if args.reindex:
            reindex(conn)
            run_checks(conn)
        if args.rebuild_product_search:
            rebuild_product_search(conn)
            run_checks(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
