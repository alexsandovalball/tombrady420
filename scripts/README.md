# Scripts

Utility scripts for managing the Telegram bot.

## Migration Scripts

### migrate_prune_inactive_bots.py

Prunes users and products associated with inactive/deleted bots.

**Usage:**

```bash
# Dry run (preview what would be deleted)
python scripts/migrate_prune_inactive_bots.py

# Actually perform the pruning
python scripts/migrate_prune_inactive_bots.py --confirm

# Use custom database path
python scripts/migrate_prune_inactive_bots.py --db-path /path/to/catalog.db
```

**What it does:**

- Identifies users who started bots that are no longer in the active pool
- Finds products associated with inactive bots
- Removes pending notifications and custom messages for these users
- Compares bot usernames in the database against configured bot tokens

**When to use:**

- After deleting/replacing a bot token
- When cleaning up old data from decommissioned bot instances
- Before deploying with a new set of bot tokens

## Admin Commands (via Bot)

Instead of running migration scripts manually, you can use these bot commands:

### /prunebots

Preview and prune users from inactive bots directly through Telegram.

```
/prunebots          # Preview what would be deleted
/prunebots confirm  # Actually perform the pruning
```

### /botusers

View users grouped by bot instance. Shows active and inactive bots with warning indicators.

### /deleteuser <user_id>

Delete a specific user from the database.
