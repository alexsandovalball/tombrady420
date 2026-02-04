# Database Migration: Language Column Fix

## Issue
After PR #34 (multi-language translation support), there was a mismatch between the database schema and the code expectations:
- **Database had**: `preferred_language` column
- **Code expected**: `language` column

This caused runtime errors: `no such column: language`

## Root Cause
The database migration for PR #34 was not applied correctly. The implementation summary documented that a migration should add a `language` column, but the actual database had `preferred_language` instead.

## Solution
Created and executed a migration script (`migrate_language_column.py`) that:
1. Renames `preferred_language` column to `language`
2. Preserves all existing user data
3. Maintains the same data types and defaults

## Migration Script Usage

### For Existing Installations
If you're experiencing the "no such column: language" error, run:

```bash
python migrate_language_column.py catalog.db
```

### For New Installations
No migration needed - the correct schema will be created automatically by `database.py` when `init_db()` is called.

## Verification
After migration, verify the schema:

```bash
sqlite3 catalog.db "PRAGMA table_info(bot_users);"
```

Expected output should include:
```
7|language|TEXT|0|'en'|0
```

## Technical Details

### Before Migration
```sql
CREATE TABLE bot_users (
    ...
    preferred_language TEXT DEFAULT 'en',
    ...
)
```

### After Migration
```sql
CREATE TABLE bot_users (
    ...
    language TEXT DEFAULT 'en',
    ...
)
```

### Migration Method
SQLite doesn't support direct column renaming, so the migration:
1. Creates a backup table
2. Drops the original table
3. Creates a new table with correct schema
4. Copies data from backup (mapping `preferred_language` → `language`)
5. Drops the backup table

## Impact
- **Fixed**: `/start` command no longer throws "no such column: language" error
- **Fixed**: Language preference queries work correctly
- **Preserved**: All existing user language preferences
- **No Breaking Changes**: Backward compatible with existing code

## Related Files
- `database.py` - Contains the schema definition and language methods
- `migrate_language_column.py` - Migration script
- `handlers/start.py` - Uses `get_user_language()`
- `handlers/language.py` - Uses `set_user_language()`

## Date
Applied: 2026-02-04
