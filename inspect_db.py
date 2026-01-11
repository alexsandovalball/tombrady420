# inspect_db.py
import sqlite3
import os
import sys

DB = os.path.join("data", "catalog.db")

def show_top_media_groups(limit=50):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT media_group_id, COUNT(*) AS cnt, GROUP_CONCAT(id) AS ids "
        "FROM products WHERE media_group_id IS NOT NULL AND media_group_id <> '' "
        "GROUP BY media_group_id ORDER BY cnt DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    print("media_group_id | count | ids")
    for r in rows:
        print(r[0], "|", r[1], "|", r[2])
    conn.close()

def show_rows_for_media_group(mg):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, file_id, file_type, caption, media_group_id, message_id, chat_id, created_at "
        "FROM products WHERE media_group_id = ? ORDER BY id",
        (mg,),
    )
    rows = cur.fetchall()
    print(f"Rows for media_group_id={mg}: {len(rows)}")
    for r in rows:
        print(dict(r))
    conn.close()

def show_rows_for_message(mid):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, file_id, file_type, caption, media_group_id, message_id, chat_id, created_at "
        "FROM products WHERE message_id = ? ORDER BY id",
        (int(mid),),
    )
    rows = cur.fetchall()
    print(f"Rows for message_id={mid}: {len(rows)}")
    for r in rows:
        print(dict(r))
    conn.close()

def usage():
    print("Usage:")
    print("  python inspect_db.py top [limit]")
    print("  python inspect_db.py mg <MEDIA_GROUP_ID>")
    print("  python inspect_db.py msg <MESSAGE_ID>")

if __name__ == "__main__":
    if not os.path.exists(DB):
        print("Database not found at", DB)
        sys.exit(1)

    if len(sys.argv) < 2:
        usage()
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "top":
        lim = int(sys.argv[2]) if len(sys.argv) >= 3 else 50
        show_top_media_groups(limit=lim)
    elif cmd == "mg" and len(sys.argv) >= 3:
        show_rows_for_media_group(sys.argv[2])
    elif cmd == "msg" and len(sys.argv) >= 3:
        show_rows_for_message(sys.argv[2])
    else:
        usage()