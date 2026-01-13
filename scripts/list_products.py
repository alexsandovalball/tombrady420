import sqlite3, json,sys
DB = r"C:\Users\UsER\Desktop\tombrady\catalog.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT id, media_group_id, file_id, file_type, caption FROM products ORDER BY id")
rows = cur.fetchall()
print("id\tmedia_group_id\tfile_id\tfile_type\tcaption")
for r in rows:
    fid = r["file_id"] or ""
    mg = r["media_group_id"] or ""
    ft = r["file_type"] or ""
    cap = (r["caption"] or "")[:40].replace("\n"," ")
    print(f"{r['id']}\t{mg}\t{fid[:30]}\t{ft}\t{cap}")
conn.close()
