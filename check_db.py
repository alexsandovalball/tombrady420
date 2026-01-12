# check_db.py
import sqlite3

DB = "catalog.db"

def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, message_id, chat_id, media_group_id, file_type, caption FROM products ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        for r in rows:
            print(r)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
