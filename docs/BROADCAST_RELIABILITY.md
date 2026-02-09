# Broadcast Reliability and Shutdown Best Practices

This document describes the broadcast reliability improvements and shutdown patterns implemented in this project to prevent message delivery failures and pickling errors.

## Broadcast Reliability

### Overview

The bot implements a multi-tier message delivery system with automatic retry and fallback routing across multiple bot instances:

1. **Database-backed broadcast forwarding**: Messages are published to a database table and processed by all bot instances
2. **Automatic fallback routing**: If a user hasn't started the primary bot, messages are queued to alternative bots
3. **Retry with exponential backoff**: Transient errors (timeouts, rate limits) trigger automatic retries
4. **Permanent error handling**: Chat not found, user blocked, etc. trigger immediate fallback

### Architecture

```
┌──────────────┐
│ Admin sends  │
│ /broadcast   │
└──────┬───────┘
       │
       v
┌─────────────────────────────┐
│ broadcast_events table      │
│ (SQLite database)           │
└────────┬────────────────────┘
         │
         │ (polled by all bot instances every 3s)
         │
    ┌────┴────┬────────┬────────┐
    │         │        │        │
    v         v        v        v
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│Bot 0  │ │Bot 1  │ │Bot 2  │ │Bot N  │
│(pri.) │ │(sec.) │ │(sec.) │ │(sec.) │
└───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
    │         │        │        │
    │         │        │        │
    └─────────┴────────┴────────┘
              │
              v
    ┌─────────────────────┐
    │ User receives msg   │
    │ from bot they       │
    │ have started        │
    └─────────────────────┘
```

### Retry and Fallback Logic

The `utils/send_utils.py` module provides `send_with_retry_and_fallback()` function that implements:

#### Permanent Errors (Immediate Fallback)
- `Forbidden`: Bot was blocked by user → Try next bot immediately
- `BadRequest` with "Chat not found" → Try next bot immediately
- `BadRequest` with "User deactivated" → Try next bot immediately

#### Transient Errors (Retry with Backoff)
- `TimedOut`: Network timeout → Retry with exponential backoff
- `NetworkError`: Connection failed → Retry with exponential backoff
- `RetryAfter`: Rate limit → Retry with exponential backoff
- `TelegramError` with "Too Many Requests" → Retry with exponential backoff

#### Exponential Backoff Configuration
```python
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_RETRY_DELAY = 30.0  # seconds

# Retry delays: 1s, 2s, 4s, 8s, ...up to 30s max
delay = min(RETRY_BASE_DELAY * (2 ** retry_count), MAX_RETRY_DELAY)
```

### Current Implementation in notifications.py

The existing `_send_single_custom_message()` method in `utils/notifications.py` already implements:
- Retry logic for rate limit errors with exponential backoff
- Permanent error detection (Forbidden, chat not found)
- User invalidation for specific bots
- Detailed logging

### Integration with send_utils.py (Future Enhancement)

The `send_utils.py` module can be integrated in a future update to replace the retry logic in `_send_single_custom_message()`. This would provide:
- Unified retry/fallback logic across all send operations
- Better separation of concerns
- Easier testing and maintenance

**Note**: The current implementation already provides adequate reliability. The `send_utils.py` module is provided as a reference implementation and for future enhancements.

## Shutdown Best Practices

### The Pickling Problem

Python's `pickle` module cannot serialize:
- `asyncio.Task` objects
- `telegram.ext.Application` objects
- Coroutine objects
- Active event loops

Attempting to use `ProcessPoolExecutor` or `multiprocessing` with these objects will raise:
```
TypeError: cannot pickle '_asyncio.Task' object
```

### Current Shutdown Implementation

The `webhook_server.py` lifespan shutdown already implements the correct pattern:

```python
async def lifespan(app: FastAPI):
    # ... startup code ...
    
    yield
    
    # Shutdown
    logger.info("Shutting down bot applications...")
    for idx, bot_app in enumerate(app.state.bot_apps):
        cancelled_tasks = []
        try:
            # 1. Cancel and clear asyncio tasks to prevent pickling errors
            if hasattr(bot_app, 'bot_data'):
                for key, value in list(bot_app.bot_data.items()):
                    if isinstance(value, asyncio.Task):
                        if not value.done():
                            value.cancel()
                            cancelled_tasks.append(value)
                        del bot_app.bot_data[key]
                    elif key == NOTIFICATION_TASKS_KEY and isinstance(value, list):
                        for task in value:
                            if isinstance(task, asyncio.Task) and not task.done():
                                task.cancel()
                                cancelled_tasks.append(task)
                        value.clear()
            
            # 2. Wait for task cancellation
            if cancelled_tasks:
                await asyncio.gather(*cancelled_tasks, return_exceptions=True)
            
            # 3. Stop and shutdown application (sequential, in-process)
            await bot_app.stop()
            await bot_app.shutdown()
            
            logger.info(f"Bot {idx} shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down bot {idx}: {e}")
    
    logger.info("All bot applications shut down successfully")
```

### Key Principles

1. **Sequential, in-process shutdown**: Use `await` in the main event loop, NOT ProcessPoolExecutor
2. **Clean up tasks first**: Cancel and remove all asyncio.Task objects before shutdown
3. **Use asyncio.gather() for concurrency**: If you need concurrent shutdown, use `asyncio.gather()`, NOT multiprocessing
4. **Handle exceptions gracefully**: Use `return_exceptions=True` to prevent one failure from stopping others

### ❌ WRONG - Will cause pickling errors:
```python
from concurrent.futures import ProcessPoolExecutor

# DON'T DO THIS
async def shutdown_in_process(app):
    await app.stop()
    await app.shutdown()

with ProcessPoolExecutor() as executor:
    # This will fail with pickling error
    futures = [executor.submit(shutdown_in_process, app) for app in apps]
```

### ✅ CORRECT - Sequential shutdown:
```python
async def shutdown_apps(apps):
    for app in apps:
        try:
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.exception(f"Error shutting down app: {e}")
```

### ✅ CORRECT - Concurrent shutdown with asyncio:
```python
async def shutdown_one_app(app):
    await app.stop()
    await app.shutdown()

async def shutdown_apps_concurrent(apps):
    results = await asyncio.gather(
        *[shutdown_one_app(app) for app in apps],
        return_exceptions=True
    )
    # Handle any exceptions in results
```

## Logging

### Broadcast Send Attempts

All send attempts are logged with the following information:
- Timestamp
- User ID
- Bot username and key
- Message preview (first 50 characters)
- Success/failure status
- Error details (if failed)
- Retry count

Example log output:
```
[SendUtils] Send attempt 1/4 - user_id=12345, bot=@my_bot (BOT_TOKEN), message_preview=Hello, this is a test message...
[SendUtils] ✓ SUCCESS - user_id=12345, bot=@my_bot, retries=0, message_id=123
```

### Configuring Log Levels

To reduce verbosity, adjust log levels in your code:

```python
import logging

# Show only warnings and errors for send operations
logging.getLogger('utils.send_utils').setLevel(logging.WARNING)

# Show only info for notifications
logging.getLogger('utils.notifications').setLevel(logging.INFO)
```

## Testing

### Broadcast Reliability Tests

See `tests/test_broadcast_reliability.py` for comprehensive tests covering:
- Permanent error → immediate fallback
- Transient error → retry → fallback
- Success on primary → no fallback
- Exponential backoff behavior
- Rate limit handling

Run tests:
```bash
pytest tests/test_broadcast_reliability.py -v
```

### Shutdown Tests

See `tests/test_shutdown.py` for tests covering:
- Task cleanup before shutdown
- Sequential shutdown
- Concurrent shutdown with asyncio.gather()
- Pickling error detection

Run tests:
```bash
pytest tests/test_shutdown.py -v
```

## Summary

✅ **DO**:
- Use database-backed broadcast forwarding for multi-bot deployments
- Implement retry with exponential backoff for transient errors
- Use immediate fallback for permanent errors
- Cancel asyncio tasks before shutdown
- Use sequential or asyncio.gather() for concurrent shutdown
- Log all send attempts with detailed metadata

❌ **DON'T**:
- Use ProcessPoolExecutor with Application or Task objects
- Use multiprocessing with asyncio objects
- Forget to clean up tasks before shutdown
- Ignore transient vs permanent error distinction
- Remove retry logic (could cause silent failures)

## References

- Issue #100: https://github.com/alexsandovalball/tombrady420/issues/100
- PR #99 (fallback routing): https://github.com/alexsandovalball/tombrady420/pull/99
- `utils/send_utils.py`: Reference implementation of retry/fallback
- `utils/notifications.py`: Current production implementation
- `webhook_server.py`: Correct shutdown pattern
