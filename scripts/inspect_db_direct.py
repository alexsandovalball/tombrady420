# scripts/inspect_db_direct.py
import os, sqlite3, json
db_path = os.path.join("data", "catalog.db")
print("Looking for DB at:", os.path.abspath(db_path))
print("DB exists:", os.path.exists(db_path))
if not os.path.exists(db_path):
    raise SystemExit("DB file not found at the path above")
conn = sqlite3.connect(db_path)
cur = conn.execute(
    "SELECT id, caption, category, subcategory, chat_id, message_id, media_group_id, created_at "
    "FROM products ORDER BY id DESC LIMIT 20"
)
rows = cur.fetchall()
print("Recent rows (up to 20):")
print(json.dumps(rows, indent=2, ensure_ascii=False))
conn.close()
