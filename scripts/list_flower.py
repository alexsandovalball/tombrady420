# scripts/list_flower.py
import sqlite3, json, os
db_path = os.path.join("data", "catalog.db")
conn = sqlite3.connect(db_path)
cur = conn.execute(
    "SELECT id, caption, category, subcategory, chat_id, message_id, media_group_id, created_at "
    "FROM products WHERE UPPER(TRIM(category)) = ? ORDER BY id DESC LIMIT 50",
    ("FLOWER",),
)
rows = cur.fetchall()
print(json.dumps(rows, indent=2, ensure_ascii=False))
conn.close()
