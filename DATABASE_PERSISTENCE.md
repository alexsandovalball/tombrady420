# Database Persistence Guide

This guide explains how to ensure your product catalog persists across bot redeploys and how to import existing channel messages.

## Problem

After redeploying the bot, you may lose your product catalog if the database is not properly persisted. This requires re-uploading all products manually.

## Solution

### Option 1: Persistent Disk Storage (Recommended for Production)

#### Render.com

1. **Add a Persistent Disk** to your Render service:
   - Go to your Render dashboard
   - Select your web service
   - Click "Disks" in the left sidebar
   - Click "Add Disk"
   - Configure:
     - **Name**: `catalog-data`
     - **Mount Path**: `/data`
     - **Size**: 1 GB (or more depending on your needs)

2. **Update Database Path** in your environment variables:
   ```env
   DB_PATH=/data/catalog.db
   ```

3. **Redeploy** your service

Now your database will persist across redeploys!

#### Railway

Railway automatically persists data in the `/app` directory, so no additional configuration is needed. Just ensure `catalog.db` is in the project root.

#### Docker

Mount a volume for database persistence:

```yaml
# docker-compose.yml
version: '3.8'

services:
  bot:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data  # Persistent volume
    environment:
      - DB_PATH=/app/data/catalog.db
```

#### VPS / Dedicated Server

The database file is automatically persistent. Just ensure you don't delete `catalog.db` between restarts.

### Option 2: PostgreSQL Database (Best for Production)

For production deployments, use PostgreSQL instead of SQLite:

1. Add PostgreSQL to your deployment platform
2. Modify `database.py` to use `asyncpg` or `psycopg2`
3. Set `DATABASE_URL` environment variable

### Option 3: Database Backups

Even with persistent storage, regular backups are recommended:

```bash
# Backup database
cp catalog.db catalog_backup_$(date +%Y%m%d).db

# Or use automated backup
*/6 * * * * cp /path/to/catalog.db /path/to/backups/catalog_$(date +\%Y\%m\%d_\%H\%M).db
```

## Importing Existing Channel Messages

If you've lost your database or are setting up the bot for the first time, you can scan the channel and import all existing media messages.

### Prerequisites

1. **Get Telegram API Credentials**:
   - Go to https://my.telegram.org/apps
   - Create a new application
   - Note your `API_ID` and `API_HASH`

2. **Install Additional Dependencies**:
   ```bash
   pip install pyrogram tgcrypto
   ```

3. **Add to .env**:
   ```env
   API_ID=your_api_id
   API_HASH=your_api_hash
   ```

### Run Channel Scanner

```bash
python scan_channel.py
```

This script will:
- Scan all messages in your configured channel
- Import media messages that don't exist in the database
- Skip messages that are in the ignore list
- Skip messages without media
- Log progress and summary

**First-time setup**: On first run, Pyrogram will ask you to log in with your phone number. This creates a session file (`scanner_session.session`) that can be reused.

### After Scanning

1. **Categorize Products**: 
   - Run `/recategorize` command in the bot
   - This sends categorization requests to all admins
   - Admins can assign categories to uncategorized products

2. **Verify Import**:
   - Run `/stats` to see total product count
   - Use `/menu` to browse the catalog

## Best Practices

1. **Use Persistent Storage**: Always configure persistent disk/volume for production
2. **Regular Backups**: Automate database backups (daily or weekly)
3. **Monitor Disk Usage**: Check disk space regularly if using persistent disks
4. **Test Persistence**: After setup, redeploy once to verify database persists
5. **Keep Credentials Safe**: Never commit API_ID, API_HASH, or session files to git

## Troubleshooting

### Database is empty after redeploy

- Check if `DB_PATH` environment variable points to persistent storage
- Verify persistent disk is properly mounted (Render, Docker)
- Check deployment logs for database initialization errors

### Channel scanner not working

- Verify `API_ID` and `API_HASH` are set correctly
- Ensure Pyrogram and TgCrypto are installed
- Check that the channel is public or you're a member
- Look for rate limiting errors in logs

### Permission errors

- Ensure the database directory is writable
- Check file permissions: `chmod 755 /data` (if using /data)
- Verify the bot process has write access

### Products not appearing after scan

- Check logs for import errors
- Verify the channel ID/username is correct
- Ensure messages have media (photos, videos, etc.)
- Run `/recategorize` to send categorization requests

## Migration Guide

### From Local to Production

1. **Backup local database**:
   ```bash
   cp catalog.db catalog_backup.db
   ```

2. **Upload to production** (method depends on platform):
   
   **Render.com**:
   - Use Render Shell to upload database
   - Or use SFTP if available
   
   **Docker**:
   ```bash
   docker cp catalog.db container_name:/app/data/catalog.db
   ```
   
   **VPS**:
   ```bash
   scp catalog.db user@server:/path/to/bot/catalog.db
   ```

3. **Verify migration**:
   - Restart the bot
   - Run `/stats` to check product count
   - Browse catalog with `/menu`

### From SQLite to PostgreSQL

1. **Export SQLite data**:
   ```bash
   sqlite3 catalog.db .dump > catalog_dump.sql
   ```

2. **Modify for PostgreSQL compatibility**:
   - Update `database.py` to use `asyncpg`
   - Adjust schema if needed
   
3. **Import to PostgreSQL**:
   ```bash
   psql -h hostname -U username -d database_name -f catalog_dump.sql
   ```

## FAQ

**Q: Will the database slow down with many products?**  
A: SQLite handles 100K+ rows efficiently. For millions of products, consider PostgreSQL.

**Q: Can I use cloud storage (S3, Google Drive) for the database?**  
A: Not recommended for SQLite (needs local filesystem). Use PostgreSQL with cloud hosting instead.

**Q: How do I know if persistence is working?**  
A: Add a test product, redeploy the bot, check if the product still exists.

**Q: What happens if I lose the database?**  
A: Run `scan_channel.py` to re-import channel messages (requires API credentials).

**Q: Should I commit the database to git?**  
A: No, databases should not be in git. Use persistent storage and backups instead.
