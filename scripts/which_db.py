# scripts/which_db.py
import os, sqlite3, json
from database import DB_PATH
print("DB_PATH from database.py:", DB_PATH)
try:
    st = os.stat(DB_PATH)
    print("DB file exists:", True)
    print("DB absolute path:", os.path.abspath(DB_PATH))
    print("DB size bytes:", st.st_size)
except FileNotFoundError:
    print("DB file exists:", False)
# show top 10 rows regardless of category
conn = sqlite3.connect(DB_PATH)
cur = conn.execute("SELECT id, caption, category, subcategory, chat_id, message_id, media_group_id, created_at FROM products ORDER BY id DESC LIMIT 10")
rows = cur.fetchall()
print("Recent rows (up to 10):")
print(json.dumps(rows, indent=2, ensure_ascii=False))
conn.close()
