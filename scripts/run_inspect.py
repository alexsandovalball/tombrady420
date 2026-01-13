import sqlite3, json, sys
DB = r"C:\Users\UsER\Desktop\tombrady\catalog.db"
if len(sys.argv) < 2:
    print("Usage: python run_inspect.py <PRODUCT_ID>")
    sys.exit(1)
try:
    PID = int(sys.argv[1])
except Exception:
    print("Invalid PRODUCT_ID:", sys.argv[1])
    sys.exit(1)
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, media_group_id, file_id, file_unique_id, file_type, chat_id, message_id, caption FROM products WHERE id = ?", (PID,))
    prod = cur.fetchone()
    print("--- PRODUCT ROW ---")
    print(json.dumps(dict(prod) if prod else {"found": False}, indent=2))
    if prod and prod["media_group_id"]:
        mg = prod["media_group_id"]
        print("\\n--- ALL ROWS FOR media_group_id=" + str(mg) + " ---")
        cur.execute("SELECT id, media_group_id, file_id, file_unique_id, file_type, chat_id, message_id FROM products WHERE media_group_id = ? ORDER BY id", (mg,))
        rows = [dict(r) for r in cur.fetchall()]
        print(json.dumps(rows, indent=2))
        cur.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN file_id IS NULL OR file_id = '' THEN 1 ELSE 0 END) AS missing_file_id, SUM(CASE WHEN file_unique_id IS NULL OR file_unique_id = '' THEN 1 ELSE 0 END) AS missing_unique FROM products WHERE media_group_id = ?", (mg,))
        stats = dict(cur.fetchone())
        print("\\n--- STATS ---")
        print(json.dumps(stats, indent=2))
    else:
        print("\\nNo media_group_id found on product or product missing.")
    conn.close()
except Exception as e:
    print("ERROR:", str(e))
    sys.exit(1)
