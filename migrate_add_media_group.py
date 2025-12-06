# migrate_add_media_group.py
import sqlite3
import sys

DB = "catalog.db"

def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(products)")
        cols = [r[1] for r in cur.fetchall()]
        if "media_group_id" not in cols:
            cur.execute("ALTER TABLE products ADD COLUMN media_group_id TEXT")
            conn.commit()
            print("Added media_group_id column")
        else:
            print("media_group_id already exists")
    except Exception as e:
        print("Migration error:", e)
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
