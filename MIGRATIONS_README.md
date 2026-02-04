# Database Migration Scripts

This directory contains database migration scripts for the tombrady420 bot.

## Available Migrations

### Language Column Migration
**Script**: `migrate_language_column.py`  
**Test**: `test_language_migration.py`  
**Documentation**: `docs/DATABASE_MIGRATION_LANGUAGE.md`

**Purpose**: Fixes the "no such column: language" error by renaming `preferred_language` to `language` in the `bot_users` table.

**When to run**: 
- If you see the error: `no such column: language`
- After updating from a version before PR #34
- If the bot crashes on `/start` command

**Usage**:
```bash
# Run migration
python migrate_language_column.py catalog.db

# Test migration
python test_language_migration.py
```

## General Migration Best Practices

1. **Always backup your database first**
   ```bash
   cp catalog.db catalog_backup_$(date +%Y%m%d).db
   ```

2. **Test the migration on a copy**
   ```bash
   cp catalog.db catalog_test.db
   python migrate_language_column.py catalog_test.db
   ```

3. **Verify the migration**
   ```bash
   python test_language_migration.py
   ```

4. **Only run migrations once**
   - Each migration script checks if it's already been applied
   - Re-running is safe but unnecessary

## Troubleshooting

### Migration fails with "database is locked"
- Stop the bot before running migration
- Make sure no other process is using the database

### Migration says "not needed"
- The migration was already applied
- Your database is up to date

### Tests fail after migration
- Check the migration output for errors
- Restore from backup and try again
- Report the issue with full error logs

## Adding New Migrations

When adding new database schema changes:

1. Create a migration script: `migrate_<feature_name>.py`
2. Create tests: `test_<feature_name>_migration.py`
3. Add documentation: `docs/DATABASE_MIGRATION_<FEATURE>.md`
4. Update this README

## Support

For help with migrations:
- Check the documentation in `docs/`
- Review the migration script code
- Check GitHub issues for similar problems
