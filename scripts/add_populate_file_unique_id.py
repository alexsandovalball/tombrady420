import sqlite3, sys
DB = r"C:\Users\UsER\Desktop\tombrady\catalog.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()
# check columns
cur.execute("PRAGMA table_info(products)")
cols = [r[1] for r in cur.fetchall()]
print("Existing columns:", cols)
if "file_unique_id" not in cols:
    print("Adding column file_unique_id (nullable)...")
    cur.execute("ALTER TABLE products ADD COLUMN file_unique_id TEXT")
    conn.commit()
else:
    print("file_unique_id already exists; skipping ALTER TABLE")
# counts before
cur.execute("SELECT COUNT(*) FROM products")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM products WHERE file_unique_id IS NULL OR file_unique_id = ''")
missing_before = cur.fetchone()[0]
print(f"Total rows: {total}; missing file_unique_id before: {missing_before}")
# populate from file_id where possible
cur.execute("UPDATE products SET file_unique_id = file_id WHERE (file_unique_id IS NULL OR file_unique_id = '') AND (file_id IS NOT NULL AND file_id != '')")
conn.commit()
cur.execute("SELECT COUNT(*) FROM products WHERE file_unique_id IS NULL OR file_unique_id = ''")
missing_after = cur.fetchone()[0]
print(f"missing file_unique_id after population: {missing_after}")
conn.close()
print('Done')
