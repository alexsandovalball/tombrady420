# Notification System Diagnostics

This directory contains diagnostic tools for inspecting and troubleshooting the notification system in production.

## diagnose_notifications.py

A read-only diagnostic script that inspects the notification system state without modifying any data.

### Purpose

- Detects database path from environment or default location
- Queries `notification_queue` and `custom_message_queue` tables
- Prints counts and sample rows for debugging
- Writes diagnostics output to `diagnostics_output.json`

### Usage

#### Local Development

```bash
cd /path/to/tombrady420
python diagnostics/diagnose_notifications.py
```

#### In Render Service Shell

1. Access the Render service shell:
   - Go to your Render dashboard
   - Select your service
   - Click "Shell" in the top right

2. Navigate to the project directory:
   ```bash
   cd /opt/render/project/src
   ```

3. Run the diagnostic script:
   ```bash
   python diagnostics/diagnose_notifications.py
   ```

4. View the output:
   - The script prints a formatted report to the console
   - A detailed JSON file is written to `diagnostics_output.json`

### Sample Output

```
======================================================================
NOTIFICATION SYSTEM DIAGNOSTICS
======================================================================

Database path: bot_data.db
Database exists: True
Database size: 1,234,567 bytes

Querying notification queues...

======================================================================
RESULTS
======================================================================

notification_queue:
  Total: 150
  Pending: 45
  Sent: 105

  Sample pending notifications (5 rows):
    - ID 123: user=456, product=789, created=2024-01-15 10:30:00
    - ID 124: user=457, product=790, created=2024-01-15 10:31:00
    ...

custom_message_queue:
  Total: 20
  Pending: 5
  Sent: 15

  Sample pending messages (5 rows):
    - ID 10: user=456, media=photo, msg="Check out this new...", created=2024-01-15 09:00:00
    ...

✅ Diagnostics written to diagnostics_output.json
```

### Output File Format

The `diagnostics_output.json` file contains:

```json
{
  "timestamp": "2024-01-15T10:35:00.123456+00:00",
  "db_path": "bot_data.db",
  "db_exists": true,
  "db_size_bytes": 1234567,
  "tables_exist": ["notification_queue", "custom_message_queue"],
  "notification_queue": {
    "total_count": 150,
    "pending_count": 45,
    "sent_count": 105,
    "sample_pending": [...]
  },
  "custom_message_queue": {
    "total_count": 20,
    "pending_count": 5,
    "sent_count": 15,
    "sample_pending": [...]
  }
}
```

### Troubleshooting

**Database not found:**
- Check that `DATABASE_PATH` environment variable is set correctly
- Verify the bot has been initialized and the database file exists

**Tables don't exist:**
- The database might not be initialized yet
- Run the bot once to create the schema

**Permission denied:**
- Ensure you have read access to the database file
- In production, you may need to run as the service user

## /debug_queues Admin Command

In addition to the diagnostic script, admins can use the `/debug_queues` command directly in Telegram to inspect queue status without SSH access.

### Usage

1. Send `/debug_queues` to the bot in a private chat
2. The bot will respond with current queue counts and sample rows

### Sample Response

```
📊 Queue Diagnostics

notification_queue:
• Pending: 45
• Sent: 105
• Total: 150

Sample pending notifications (up to 5):
• ID 123: user=456, product=789, created=2024-01-15 10:30
• ID 124: user=457, product=790, created=2024-01-15 10:31

custom_message_queue:
• Pending: 5
• Sent: 15
• Total: 20

Sample pending custom messages (up to 5):
• ID 10: user=456, media=photo, msg="Check out this new...", created=2024-01-15 09:00
```

### Permissions

- Only bot admins (defined in `ADMIN_USER_IDS` config) can run this command
- Non-admin users will receive an access denied message

## Related Changes

This diagnostic tooling was added alongside improvements to notification task resilience:

- Background tasks now stored in `application.bot_data` instead of ephemeral `context`
- Enhanced debug logging throughout the notification pipeline
- Better visibility into primary instance checks and queue processing

See the main PR description for complete details.
