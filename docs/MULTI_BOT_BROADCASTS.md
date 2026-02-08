# Multi-Bot Synchronized Broadcasts - Implementation Guide

This document describes the multi-bot broadcast support implementation that allows multiple bot processes to coordinate broadcasts and notifications.

## Overview

The implementation enables multiple bot processes (using different environment variable names for their tokens) to:
- Share a single database
- Route messages to users based on their preferred bot
- Coordinate broadcasts without duplicates
- Support categorization only on the primary bot

## Key Concepts

### Bot Environment Keys

Instead of hardcoding bot usernames, the system uses environment variable names as identifiers:
- **Primary bot**: Uses `BOT_TOKEN` environment variable
- **Secondary bots**: Use `BOT_TOKEN_1`, `BOT_TOKEN_2`, etc.

The `get_bot_env_key()` function determines which environment variable the current process is using.

### User Preferences

Each user has a `last_bot_key` field that tracks which bot they last interacted with. When queueing messages or notifications, the system uses this to route messages to the correct bot process.

### Message Targeting

Queued messages and notifications include a `target_bot_key` field that specifies which bot should deliver them. Bot processes filter their queues to only process messages targeted to their environment key.

## Database Schema Changes

Three new nullable columns were added (see `migrations/20260208_add_bot_keys.sql`):

```sql
-- User's preferred bot
ALTER TABLE bot_users ADD COLUMN last_bot_key TEXT;

-- Which bot should deliver this message
ALTER TABLE custom_message_queue ADD COLUMN target_bot_key TEXT;

-- Which bot should deliver this notification
ALTER TABLE notification_queue ADD COLUMN target_bot_key TEXT;
```

## Migration Steps

### For Existing Deployments

1. **Apply database migration**:
   ```bash
   sqlite3 catalog.db < migrations/20260208_add_bot_keys.sql
   ```

2. **No code changes needed** - the implementation is backward compatible:
   - NULL `target_bot_key` values fall back to bot_username filtering
   - NULL `last_bot_key` values default to the current process key
   - Single-BOT_TOKEN deployments work exactly as before

### For New Multi-Bot Deployments

1. **Configure environment variables** on each process:
   
   **Primary bot process**:
   ```env
   BOT_TOKEN=your_primary_bot_token_here
   ADMIN_IDS=123456789
   CHANNEL_ID=-1001234567890
   # ... other config
   ```
   
   **Secondary bot process 1**:
   ```env
   BOT_TOKEN_1=your_secondary_bot_token_here
   ADMIN_IDS=123456789  # Same admins
   CHANNEL_ID=-1001234567890  # Same channel
   # ... other config (must match primary)
   ```
   
   **Secondary bot process 2**:
   ```env
   BOT_TOKEN_2=your_third_bot_token_here
   ADMIN_IDS=123456789
   CHANNEL_ID=-1001234567890
   # ... other config
   ```

2. **Start all bot processes** - they will automatically coordinate:
   - All bots process their assigned users' messages
   - Only the primary bot (BOT_TOKEN) handles categorization
   - Broadcasts are distributed across bots based on user preferences

## Key Features

### 1. User Interaction Tracking

When a user interacts with any bot, their `last_bot_key` is updated:
```python
await db.set_last_bot_key(user.id, get_bot_env_key())
```

### 2. Smart Message Routing

When queuing broadcasts, each message is tagged with the user's preferred bot:
```python
users_with_targets = [(user_id, last_bot_key), ...]
await db.queue_custom_messages_batch_with_targets(users_with_targets, message_text)
```

### 3. Filtered Queue Processing

Each bot only processes messages targeted to its environment key:
```python
# In process_custom_message_queue:
if target_key == current_bot_key:
    # Process this message
elif target_key is None:
    # Backward compatibility: use bot_username filtering
```

### 4. Primary Bot Categorization

Only the primary bot prompts admins to categorize products:
```python
if is_primary_bot_process():
    # Send categorization prompt
```

## Backward Compatibility

The implementation maintains full backward compatibility:

1. **NULL target_bot_key**: Messages queued before the update have NULL `target_bot_key` and are processed using the old bot_username filtering logic

2. **NULL last_bot_key**: Users without a recorded preference default to the current process's bot key when new messages are queued

3. **Single-bot deployments**: Continue to work unchanged - the code path is identical to before, just with populated key fields

## Testing

Run the test suite to verify multi-bot functionality:

```bash
PYTHONPATH=. python3 -m pytest tests/test_multi_bot_broadcasts.py -v
```

Test coverage includes:
- Bot environment key detection
- User preference tracking
- Message queue routing with targets
- Backward compatibility with NULL values

## Deployment Considerations

### Performance

- Message queuing uses batch operations for efficiency
- Each bot process filters its queue independently (no blocking)
- Logging includes target distribution for monitoring

### Monitoring

Key log messages to monitor:
```
[BOT_TOKEN (@YourBot)] Queued 150 messages. Target distribution: {'BOT_TOKEN': 80, 'BOT_TOKEN_1': 70}
[BOT_TOKEN_1 (@SecondBot)] Filtered out 80 messages not targeted to this bot, 70 remaining
```

### Scaling

- Add more bot processes by defining `BOT_TOKEN_N` environment variables
- Each process handles its share of users automatically
- No coordination needed between processes beyond shared database

## Troubleshooting

**Issue**: Messages not being delivered
- **Check**: Verify `target_bot_key` matches an active bot process
- **Check**: Ensure all bots share the same database path

**Issue**: Duplicate categorization prompts
- **Check**: Only one process should have `BOT_TOKEN` (without number suffix)
- **Check**: Verify `is_primary_bot_process()` returns True on only one process

**Issue**: Users switching between bots unexpectedly
- **Check**: `last_bot_key` is being updated on every interaction
- **Check**: Bot processes have distinct token environment variable names

## API Reference

### Key Functions

**`get_bot_env_key() -> str`**
- Returns the environment variable name for this process's bot token
- Example: `'BOT_TOKEN'` or `'BOT_TOKEN_1'`

**`is_primary_bot_process() -> bool`**
- Returns True if this process is the primary bot (uses `BOT_TOKEN`)
- Used to control categorization workflow

**`set_last_bot_key(user_id: int, bot_key: str)`**
- Records which bot a user last interacted with
- Called automatically on every user interaction

**`queue_custom_messages_batch_with_targets(users_with_targets, ...)`**
- Queue messages for multiple users with individual routing
- `users_with_targets`: List of `(user_id, target_bot_key)` tuples

## Future Enhancements

Potential improvements for future versions:

1. **Load balancing**: Distribute new users across bot processes evenly
2. **Health checks**: Monitor which bot processes are active
3. **Dynamic routing**: Automatically reassign users if a bot process goes offline
4. **Analytics**: Track message delivery rates per bot process

## Support

For questions or issues:
1. Check the test suite for usage examples
2. Review log output for routing information
3. Verify environment variables are set correctly on each process
