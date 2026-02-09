# All-Bots Notification Delivery - Implementation Summary

## Overview

This implementation ensures that all bot instances deliver notifications, broadcasts, and single-user sends to **all users who have started each bot**, rather than only to users based on their last interaction.

## Key Changes

### 1. Bot Username Global Storage

**File:** `utils/bot_key.py`

Added global bot_username storage to make the bot's username accessible to broadcast callbacks:

```python
_BOT_USERNAME: Optional[str] = None

def set_bot_username(username: str):
    """Set the bot username for this process."""
    global _BOT_USERNAME
    _BOT_USERNAME = username

def get_bot_username() -> Optional[str]:
    """Get the bot username for this process."""
    return _BOT_USERNAME
```

**File:** `main.py`

Bot username is set during post_init:

```python
async def post_init(application: Application):
    ...
    from utils.bot_key import set_bot_username
    bot_username = application.bot.username if hasattr(application.bot, 'username') else None
    if bot_username:
        set_bot_username(bot_username)
```

### 2. Modified Broadcast Delivery Logic

**File:** `utils/notifications.py`

Changed `enqueue_local_broadcast()` to:
- Use `bot_username` instead of `last_bot_key` to filter users
- Accept a `db` parameter via callback factory pattern
- Support different delivery modes via payload context:
  - `target_user_id`: Single-user send (deliver if user started this bot)
  - `product_id`: Product notification (deliver to subscribers who started this bot)
  - No special fields: Broadcast to all (deliver to all users who started this bot)

```python
async def enqueue_local_broadcast(db: Database, context: dict, message_text: str) -> None:
    bot_username = get_bot_username()
    
    # Get users who have started this bot (based on bot_username)
    if bot_username:
        bot_user_ids = set(await db.get_users_by_bot(bot_username))
    
    # Filter based on context
    if context.get("target_user_id"):
        # Single-user send: only if they started this bot
        ...
    elif context.get("product_id"):
        # Product notification: subscribed users who started this bot
        ...
    else:
        # Broadcast: all users who started this bot
        ...
```

### 3. Single-User Sends via Broadcast Forwarder

**File:** `utils/notifications.py`

Modified `send_custom_message_to_user()` to use broadcast forwarder:

```python
async def send_custom_message_to_user(self, context, user_id, message_text):
    # Use broadcast forwarder to ensure all bot instances deliver
    from utils import broadcast_forwarder
    
    payload = {"target_user_id": user_id}
    broadcast_id = await broadcast_forwarder.publish_broadcast(
        self.db, message_text, payload=payload
    )
```

### 4. Product Notifications via Broadcast Forwarder

**File:** `utils/notifications.py`

Modified `notify_subscribers_about_product()` to use broadcast forwarder:

```python
async def notify_subscribers_about_product(self, context, product_id):
    ...
    # Use broadcast forwarder
    from utils import broadcast_forwarder
    
    payload = {
        "product_id": product_id,
        "reply_markup_json": reply_markup_json
    }
    broadcast_id = await broadcast_forwarder.publish_broadcast(
        self.db, message_text, payload=payload
    )
```

### 5. Callback Factory Pattern

**File:** `utils/notifications.py` and `main.py`

Created a factory function to bind the database instance to the broadcast callback:

```python
def create_enqueue_local_broadcast_callback(db: Database):
    """Create a callback with the database instance bound."""
    async def callback(context: dict, message_text: str) -> None:
        await enqueue_local_broadcast(db, context, message_text)
    return callback
```

Used in main.py:

```python
async def post_init(application: Application):
    from utils.notifications import create_enqueue_local_broadcast_callback

    enqueue_callback = create_enqueue_local_broadcast_callback(db)
    listener_task = broadcast_forwarder.start_listener_in_background(db, enqueue_callback)
```

## Behavior Changes

### Before

- **Broadcasts (`/broadcast`):** Delivered by all bots, but only to users with matching `last_bot_key`
- **Single-user sends (`/send`):** Only delivered by one bot (based on `target_bot_key`)
- **Product notifications:** Only delivered by one bot per user (based on `last_bot_key`)

### After

- **Broadcasts (`/broadcast`):** Delivered by **all bots to ALL users who have started each bot**
- **Single-user sends (`/send`):** Delivered by **all bots the user has started**
- **Product notifications:** Delivered by **all bots to their own subscribed users**

## User Impact

Users who have started multiple bots will now receive:
- Duplicate broadcasts (once from each bot they've started)
- Duplicate single-user sends (once from each bot they've started)
- Duplicate product notifications (once from each bot they've started)

This ensures **no user will miss a notification** because it originated from a different bot instance.

## Technical Details

### User-Bot Association

The system now uses `bot_username` field in the `bot_users` table to determine which users have started which bot:

```sql
SELECT user_id FROM bot_users WHERE bot_username = ?
```

This is different from `last_bot_key` which only tracks the most recent bot interaction.

### Backward Compatibility

- NULL `bot_username` values are handled via `get_users_by_bot()` logic
- Primary bot includes NULL users for backward compatibility
- Existing queued messages with NULL `target_bot_key` still work

## Testing

Added comprehensive test suite in `tests/test_all_bots_delivery.py`:

- ✅ Broadcasts use `bot_username` filtering
- ✅ Single-user sends via broadcast forwarder
- ✅ Product notifications to subscribed users only
- ✅ Multiple bots deliver to their own users
- ✅ Backward compatibility with NULL values

All 69 existing tests continue to pass.

## Migration Notes

No database migration required. The implementation uses existing fields:
- `bot_username` (already exists, added in prior migration)
- `last_bot_key` (already exists, added in prior migration)

The change is purely in the delivery logic.

## Performance Considerations

- Each broadcast event triggers queue operations in all bot instances
- Users with multiple bots will receive multiple messages (intentional design)
- Database polling remains at 3-second intervals (configurable via `POLL_INTERVAL_SECONDS`)

## Monitoring

Key log messages to look for:

```
[BroadcastEnqueue] Processing broadcast for bot @botname (key=BOT_TOKEN): Message...
[BroadcastEnqueue] Found 150 users for bot @botname
[BroadcastEnqueue] Queued 150 messages for bot @botname
```

For single-user sends:

```
[BroadcastEnqueue] Single-user target: 123456
[BroadcastEnqueue] User 123456 has started bot @botname, will deliver
```

For product notifications:

```
[BroadcastEnqueue] Product notification: 789
[BroadcastEnqueue] 50 subscribed users from bot @botname
```

## Future Enhancements

Potential improvements:
1. Add user-level deduplication flags to prevent duplicate delivery
2. Implement message delivery receipts per bot instance
3. Add analytics for cross-bot delivery coverage
4. Support for bot-specific message customization
