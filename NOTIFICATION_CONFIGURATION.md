# Notification System Configuration Guide

This document describes the configuration options for the notification system, including the button callback fixes and staggered delivery feature.

## Overview

The notification system has been enhanced to:
1. **Fix button callbacks** - Ensure "View Product" and "Unsubscribe" buttons work correctly
2. **Implement staggered delivery** - Send notifications to user groups at intervals to avoid spam flags

## Configuration Options

All configuration constants are located in `utils/notifications.py`.

### Rate Limiting

Controls how many notifications individual users can receive per hour:

```python
MAX_NOTIFICATIONS_PER_HOUR = 5  # Maximum notifications per user per hour
NOTIFICATION_BATCH_SIZE = 10    # Send notifications in batches
BATCH_DELAY_SECONDS = 3         # Delay between batches
```

**Recommendations:**
- Keep `MAX_NOTIFICATIONS_PER_HOUR` at 5 or lower to respect user preferences
- `NOTIFICATION_BATCH_SIZE` of 10 works well for most use cases
- `BATCH_DELAY_SECONDS` of 3 provides good throughput without triggering rate limits

### Staggered Delivery

Controls how notifications are distributed across user groups to avoid spam flags:

```python
ENABLE_STAGGERED_DELIVERY = True     # Enable/disable staggered delivery
STAGGER_GROUP_SIZE = 10              # Number of users per group
STAGGER_INTERVAL_SECONDS = 30        # Delay between groups (30s or 60s recommended)
```

**How it works:**
1. When there are more users than `STAGGER_GROUP_SIZE`, users are split into groups
2. Each group receives notifications with a delay of `STAGGER_INTERVAL_SECONDS` between groups
3. Within each group, normal batch processing applies with `BATCH_DELAY_SECONDS` delays
4. Rate limiting is still enforced per user

**Example:**
- With 25 users, `STAGGER_GROUP_SIZE=10`, and `STAGGER_INTERVAL_SECONDS=30`:
  - Group 1 (users 1-10): Sent immediately
  - Group 2 (users 11-20): Sent after 30 seconds
  - Group 3 (users 21-25): Sent after 60 seconds
  - Total delivery time: ~60+ seconds

**Recommendations:**
- Set `ENABLE_STAGGERED_DELIVERY = True` for production use
- Use `STAGGER_GROUP_SIZE = 10` for balanced delivery
- Choose `STAGGER_INTERVAL_SECONDS`:
  - `30` seconds for faster delivery with moderate spam protection
  - `60` seconds for maximum spam protection with slower delivery

### Custom Message Rate Limiting

Controls broadcast messages sent by admins:

```python
MAX_CUSTOM_MESSAGES_PER_HOUR = 3     # Maximum custom messages per user per hour
CUSTOM_MESSAGE_BATCH_SIZE = 5        # Send custom messages in smaller batches
CUSTOM_MESSAGE_DELAY_SECONDS = 5     # Longer delay for custom messages
```

**Recommendations:**
- Keep `MAX_CUSTOM_MESSAGES_PER_HOUR` lower than notification limit
- Use smaller `CUSTOM_MESSAGE_BATCH_SIZE` for important announcements

## Button Callback Fixes

### View Product Button

**Issue:** The "View Product" button was sending callback data in the wrong format.

**Fix:** Changed from `product|{product_id}|1` (3 parts) to `product|{product_id}` (2 parts)

**Location:** `utils/notifications.py`, `_send_notification_batch()` method

```python
keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("👁️ View Product", callback_data=f"product|{product_id}")],
    [InlineKeyboardButton("🔕 Unsubscribe", callback_data="unsubscribe_notifications")]
])
```

**Handler:** `handlers/product_view.py`, `handle_product_callback()` function

### Unsubscribe Button

**Status:** Already working correctly, no changes needed.

**Callback data:** `unsubscribe_notifications`

**Handler:** `main.py`, `callback_query_handler()` function, handles `callback_data == "unsubscribe_notifications"`

## Usage Examples

### Enable Staggered Delivery with 60-Second Intervals

Edit `utils/notifications.py`:

```python
ENABLE_STAGGERED_DELIVERY = True
STAGGER_GROUP_SIZE = 10
STAGGER_INTERVAL_SECONDS = 60  # Change from 30 to 60
```

### Disable Staggered Delivery (Use Standard Batching)

Edit `utils/notifications.py`:

```python
ENABLE_STAGGERED_DELIVERY = False
```

### Increase Group Size for Fewer Delays

Edit `utils/notifications.py`:

```python
STAGGER_GROUP_SIZE = 20  # Send to 20 users before staggering
```

## Monitoring and Logging

The notification system logs detailed information:

```
INFO: Processing 25 pending notifications
INFO: Using staggered delivery: 25 users in groups of 10
INFO: Processing group 1/3 with 10 users
INFO: Waiting 30s before next group...
INFO: Processing group 2/3 with 10 users
INFO: Waiting 30s before next group...
INFO: Processing group 3/3 with 5 users
INFO: Sent 25 notifications successfully
```

Monitor these logs to:
- Verify staggered delivery is working
- Track delivery times
- Identify rate limiting issues
- Detect failed deliveries

## Testing

Run the comprehensive test suite:

```bash
python test_notification_fixes.py
```

This tests:
- ✅ Button callback format correctness
- ✅ Unsubscribe button configuration
- ✅ Staggered delivery configuration
- ✅ Staggered delivery timing and grouping
- ✅ Rate limiting integration

## Performance Impact

### With Staggered Delivery Enabled

**Pros:**
- Reduced risk of spam flags
- More controlled delivery rate
- Better Telegram API compliance

**Cons:**
- Longer total delivery time for large user bases
- Example: 100 users with 10/group and 30s interval = ~4.5 minutes total

### Without Staggered Delivery

**Pros:**
- Faster notification delivery
- Example: 100 users = ~30 seconds total

**Cons:**
- Higher risk of spam flags with large user bases
- May trigger Telegram rate limits

## Troubleshooting

### Notifications Not Sending

1. Check if rate limiting is too restrictive:
   - View logs for "reached rate limit" messages
   - Consider increasing `MAX_NOTIFICATIONS_PER_HOUR`

2. Check if users are blocked:
   - Users who block the bot are automatically marked and skipped

3. Verify database queue:
   - Check `pending_notifications` table
   - Ensure notifications are being queued properly

### Slow Notification Delivery

1. If staggered delivery is enabled:
   - Reduce `STAGGER_INTERVAL_SECONDS` from 60 to 30
   - Increase `STAGGER_GROUP_SIZE` to send more at once
   - Disable with `ENABLE_STAGGERED_DELIVERY = False` if needed

2. If batch processing is slow:
   - Reduce `BATCH_DELAY_SECONDS` from 3 to 1 or 2
   - Increase `NOTIFICATION_BATCH_SIZE` for larger batches

### Spam Flags from Telegram

1. Enable staggered delivery if not already:
   ```python
   ENABLE_STAGGERED_DELIVERY = True
   ```

2. Increase stagger interval:
   ```python
   STAGGER_INTERVAL_SECONDS = 60
   ```

3. Reduce group size:
   ```python
   STAGGER_GROUP_SIZE = 5
   ```

4. Reduce rate limits:
   ```python
   MAX_NOTIFICATIONS_PER_HOUR = 3
   ```

## Best Practices

1. **Production Settings:**
   - `ENABLE_STAGGERED_DELIVERY = True`
   - `STAGGER_INTERVAL_SECONDS = 30` or `60`
   - `MAX_NOTIFICATIONS_PER_HOUR = 5`

2. **Development/Testing:**
   - `ENABLE_STAGGERED_DELIVERY = False`
   - Monitor logs for issues

3. **Large User Base (>100 users):**
   - Use `STAGGER_INTERVAL_SECONDS = 60`
   - Consider smaller `STAGGER_GROUP_SIZE = 5`

4. **Small User Base (<20 users):**
   - Staggering may not be necessary
   - Can disable or use `STAGGER_GROUP_SIZE = 20`

## Migration Notes

No migration is required. The changes are backward compatible:
- Existing notification queues will be processed with the new staggered delivery
- Button callbacks will work immediately after deployment
- No database schema changes needed

## API Changes

No public API changes. All modifications are internal to `utils/notifications.py`.

## See Also

- `test_notification_fixes.py` - Comprehensive test suite
- `test_notifications.py` - Original notification tests
- `NOTIFICATION_SYSTEM.md` - Original notification documentation
- `utils/notifications.py` - Implementation
- `handlers/product_view.py` - Product view handler
- `main.py` - Callback query handler
