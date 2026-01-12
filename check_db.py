# check_db.py
#!/usr/bin/env python3
"""
Simple database inspection script for checking product data.
Usage: python check_db.py
"""
import sqlite3
import sys
import os

# Use DB_PATH from config if available, otherwise fallback
try:
    from config import DB_PATH
    DB = DB_PATH
except ImportError:
    DB = "data/catalog.db"


def main():
    if not os.path.exists(DB):
        print(f"Database not found: {DB}")
        sys.exit(1)
    
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    try:
        print(f"Inspecting database: {DB}")
        print("=" * 80)
        
        # Show recent products
        print("\nRecent 20 products:")
        print("-" * 80)
        cur.execute(
            "SELECT id, message_id, chat_id, media_group_id, file_type, caption "
            "FROM products ORDER BY id DESC LIMIT 20"
        )
        rows = cur.fetchall()
        
        if not rows:
            print("No products found in database")
        else:
            for r in rows:
                product_id, msg_id, chat_id, mg_id, file_type, caption = r
                caption_preview = (caption[:50] + "...") if caption and len(caption) > 50 else (caption or "")
                print(f"ID: {product_id:4d} | Msg: {msg_id:10} | Chat: {chat_id:12} | "
                      f"MG: {mg_id or 'None':36} | Type: {file_type:8} | Caption: {caption_preview}")
        
        # Show stats
        print("\n" + "=" * 80)
        print("Database Statistics:")
        print("-" * 80)
        
        cur.execute("SELECT COUNT(*) FROM products")
        total = cur.fetchone()[0]
        print(f"Total products: {total}")
        
        cur.execute("SELECT COUNT(DISTINCT media_group_id) FROM products WHERE media_group_id IS NOT NULL")
        total_mg = cur.fetchone()[0]
        print(f"Media groups: {total_mg}")
        
        cur.execute("SELECT COUNT(DISTINCT category) FROM products WHERE category IS NOT NULL")
        total_cat = cur.fetchone()[0]
        print(f"Categories: {total_cat}")
        
        cur.execute("SELECT file_type, COUNT(*) as cnt FROM products GROUP BY file_type ORDER BY cnt DESC")
        type_counts = cur.fetchall()
        print("\nProducts by type:")
        for ft, cnt in type_counts:
            print(f"  {ft}: {cnt}")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
