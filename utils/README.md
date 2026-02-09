# Utils Module Documentation

## Rate Limit Handling

The bot implements robust rate limit handling to ensure reliable broadcast delivery even when Telegram imposes rate limits.

### Overview

When Telegram returns a `429 Too Many Requests` error (RetryAfter exception), the bot:
1. Pauses ALL workers for that bot using a shared database state
2. Waits for the server-specified retry_after duration (plus small jitter)
3. Retries the message automatically
4. Logs detailed telemetry about rate limiting

### Key Features

#### 1. Shared Pause State (SQLite-based)

The `rate_limiter.py` module provides cross-worker coordination:
- When one worker receives a RetryAfter, it writes to the `bot_rate_pause_state` table
- All other workers check this table before sending and wait if paused
- Uses SQLite for consistency with the rest of the codebase (no Redis required)

```python
from utils import rate_limiter

# Check if bot is currently paused
remaining = await rate_limiter.get_remaining_pause_seconds(bot_key)
if remaining > 0:
    await asyncio.sleep(remaining)

# Set pause state when receiving RetryAfter
pause_until = time.time() + retry_after_seconds
await rate_limiter.set_paused_until(bot_key, pause_until)
```

#### 2. Rate-Aware Message Sending

The `send_message_with_rate_handling()` function in `notifications.py` handles:
- **RetryAfter**: Pauses all workers and retries after the specified delay
- **TimedOut/NetworkError**: Exponential backoff with configurable retries
- **Permanent errors** (Forbidden, BadRequest): No retry, marks user as blocked/invalid

```python
result = await send_message_with_rate_handling(
    bot=bot,
    chat_id=user_id,
    text="Hello!",
    bot_key=current_bot_key
)

if result["success"]:
    # Message sent successfully
    pass
else:
    # Check result["error"] and result["retry_after"]
    pass
```

#### 3. Retry-on-Recent-Start Heuristic

Handles the race condition where a user clicks `/start` just as a broadcast is being sent:
- Tracks when each user started the bot (`started_at` in `bot_users` table)
- If user started within `BROADCAST_RETRY_ON_RECENT_START_SECONDS` after broadcast enqueue
- Waits `BROADCAST_RECENT_START_RETRY_DELAY_SECONDS` and retries once
- Prevents marking users as "invalid_for_bot" due to timing issues

#### 4. Enhanced Telemetry

Broadcast telemetry now includes:
```json
{
  "bot": "BOT_TOKEN (@bot_username)",
  "total_queued": 1000,
  "sent": 950,
  "failed": 50,
  "invalidated": 5,
  "failure_reasons": {
    "forbidden": 3,
    "chat_not_found": 2,
    "transient_error": 45
  },
  "rate_limit_info": {
    "count": 10,
    "retry_after_samples": [30, 45, 30, 60],
    "max_retry_after": 60
  },
  "failure_reason_samples": [
    "transient_error: RetryAfter(30s)",
    "forbidden: Forbidden: bot was blocked by the user"
  ]
}
```

### Configuration

Environment variables (with defaults):

```bash
# Maximum retry attempts for failed sends
BROADCAST_MAX_SEND_RETRIES=5

# Base delay for exponential backoff (seconds)
BROADCAST_BASE_BACKOFF_SECONDS=0.5

# Window for recent-start retry heuristic (seconds)
BROADCAST_RETRY_ON_RECENT_START_SECONDS=60

# Delay before retrying recent-start cases (seconds)
BROADCAST_RECENT_START_RETRY_DELAY_SECONDS=5
```

### Database Schema Changes

New columns added:

**bot_users table:**
- `started_at` (TIMESTAMP): When user first started the bot

**custom_message_queue table:**
- `retry_count` (INTEGER): Number of retry attempts for this message
- `enqueued_at` (TIMESTAMP): When broadcast was originally enqueued
- `scheduled_retry_at` (TIMESTAMP): When to retry this message (future use)

**New table:** `bot_rate_pause_state`
- `bot_key` (TEXT, PRIMARY KEY): Bot identifier
- `paused_until` (REAL): UTC epoch timestamp until which sending is paused
- `updated_at` (REAL): When this pause state was last updated

### Token Masking

Bot tokens are masked in logs for security:
- Shows first 8 and last 8 characters
- Example: `12345678...ABCDEFGH`

### How It Works Across Bot Instances

In a multi-bot setup:
1. Each bot instance processes its own users' messages (via `target_bot_key`)
2. When any worker receives a RetryAfter for a bot, it writes to the shared database
3. All workers for that bot check the pause state before sending
4. Messages that fail due to transient errors are NOT marked as sent
5. The next processing cycle will retry them automatically
6. Rate limit telemetry is aggregated per bot

### Error Handling

**Permanent errors (mark as sent, don't retry):**
- Forbidden (user blocked bot)
- BadRequest with chat_not_found
- BadRequest with invalid message format

**Transient errors (don't mark as sent, retry in next cycle):**
- RetryAfter (rate limited)
- TimedOut (network timeout)
- NetworkError (network issues)

**Special case:**
- `invalid_for_bot` with recent start → retry once after delay

## Related Modules

- `broadcast_forwarder.py`: Database-backed broadcast distribution
- `bot_key.py`: Bot identification helpers
- `notifications.py`: Main notification service
