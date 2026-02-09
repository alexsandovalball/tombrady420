# Testing the Shared Rate-Limit Pause Mechanism

This document explains how to test the new shared rate-limit pause mechanism locally.

## Overview

The system now implements a Redis-backed shared pause state that:
- Prevents all workers from repeatedly hitting rate limits
- Uses atomic operations to ensure only the longest pause is kept
- Adds configurable buffer time to prevent immediately re-hitting limits
- Implements exponential backoff with jitter for retries
- Provides telemetry and metrics for monitoring pause events

## Prerequisites

### For Unit Tests (Recommended)
The unit tests use `fakeredis`, so you **don't need a real Redis server** to run them:

```bash
# Install dependencies
pip install -r requirements.txt

# Run all pause manager tests
python -m pytest tests/test_redis_pause_manager.py -v

# Run notification pause handling tests
python -m pytest tests/test_pause_handling.py -v

# Run all tests
python -m pytest tests/ -v
```

### For Integration Testing (Optional)
If you want to test with a real Redis server:

1. **Install Redis**:
   ```bash
   # On Ubuntu/Debian
   sudo apt-get install redis-server
   
   # On macOS
   brew install redis
   
   # Using Docker
   docker run -d -p 6379:6379 redis:latest
   ```

2. **Configure Redis** (optional, defaults work for local testing):
   ```bash
   # In your .env file
   REDIS_HOST=localhost
   REDIS_PORT=6379
   REDIS_DB=0
   # REDIS_PASSWORD=  # Leave empty for local testing
   ```

3. **Start the bot**:
   ```bash
   python main.py
   ```

## Testing Scenarios

### 1. Basic Pause Functionality

Test that pause state is correctly set and retrieved:

```python
import asyncio
from utils import redis_pause_manager

async def test_basic_pause():
    # Set pause for 30 seconds
    await redis_pause_manager.set_pause_state(
        bot_username="test_bot",
        retry_after_seconds=30.0,
        reason="manual_test",
        set_by="tester"
    )
    
    # Check if paused
    is_paused = await redis_pause_manager.is_paused("test_bot")
    print(f"Is paused: {is_paused}")
    
    # Get remaining time
    remaining = await redis_pause_manager.get_remaining_pause_seconds("test_bot")
    print(f"Remaining pause: {remaining:.1f}s")
    
    # Get pause state
    state = await redis_pause_manager.get_pause_state("test_bot")
    print(f"Pause state: {state}")

asyncio.run(test_basic_pause())
```

### 2. Atomic Pause Extension

Test that longer pauses extend existing pauses, but shorter pauses don't:

```python
import asyncio
from utils import redis_pause_manager

async def test_atomic_extension():
    # Set initial pause for 10 seconds
    await redis_pause_manager.set_pause_state(
        bot_username="test_bot",
        retry_after_seconds=10.0,
        reason="initial",
        set_by="tester",
        buffer_seconds=0.0
    )
    
    initial_state = await redis_pause_manager.get_pause_state("test_bot")
    print(f"Initial pause until: {initial_state['paused_until']}")
    
    # Try to set shorter pause (should fail)
    await asyncio.sleep(0.1)
    success = await redis_pause_manager.set_pause_state(
        bot_username="test_bot",
        retry_after_seconds=5.0,
        reason="shorter",
        set_by="tester",
        buffer_seconds=0.0
    )
    print(f"Shorter pause set: {success}")  # Should be False
    
    # Try to set longer pause (should succeed)
    success = await redis_pause_manager.set_pause_state(
        bot_username="test_bot",
        retry_after_seconds=30.0,
        reason="extended",
        set_by="tester",
        buffer_seconds=0.0
    )
    print(f"Longer pause set: {success}")  # Should be True
    
    final_state = await redis_pause_manager.get_pause_state("test_bot")
    print(f"Final pause until: {final_state['paused_until']}")

asyncio.run(test_atomic_extension())
```

### 3. Processing Skip When Paused

Test that notification processing is skipped when bot is paused:

```python
import asyncio
from database import Database
from utils.notifications import NotificationService, BotContext

async def test_processing_skip():
    # Set up database and service
    db = Database("test.db")
    await db.init_db()
    service = NotificationService(db)
    
    # Create mock application
    from unittest.mock import MagicMock, AsyncMock
    app = MagicMock()
    app.bot = MagicMock()
    app.bot.username = "test_bot"
    app.bot.send_message = AsyncMock()
    context = BotContext(app)
    
    # Set pause
    await redis_pause_manager.set_pause_state(
        bot_username="test_bot",
        retry_after_seconds=30.0,
        reason="test_pause",
        set_by="tester"
    )
    
    # Try to process queue (should be skipped)
    result = await service.process_custom_message_queue(context)
    print(f"Result: {result}")
    # Should show {"sent_count": 0, "failed_count": 0, "failure_reasons": {"paused": 1}}

asyncio.run(test_processing_skip())
```

### 4. Metrics Tracking

Test that metrics are properly incremented:

```python
from utils import redis_pause_manager

# Get current metrics
metrics = redis_pause_manager.get_metrics()
print(f"Current metrics: {metrics}")

# Metrics include:
# - pause_set_count: Number of times pause was set
# - pause_skipped_count: Number of times processing was skipped due to pause
```

## Configuration Options

All configuration is in `.env` file:

```bash
# Redis connection
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=  # Optional

# Pause behavior
PAUSE_BUFFER_SECONDS=5  # Extra buffer added to rate-limit pause
DEFAULT_RETRY_AFTER_SECONDS=30  # Default when no retry-after header

# Retry behavior
BROADCAST_MAX_SEND_RETRIES=5
BROADCAST_BASE_BACKOFF_SECONDS=0.5
MAX_BACKOFF_SECONDS=300
BACKOFF_JITTER_FACTOR=0.1
```

## Monitoring

### Logs

The system logs pause events at INFO level:

```
[PauseManager] Set pause for bot @test_bot: paused_until=1234567890.12 (35.0s), reason=rate_limit_429, set_by=message_processor@BOT_TOKEN, retry_after_header=30
[test_bot] Bot is paused due to rate limiting, remaining: 33.5s - skipping processing
```

### Metrics

Access metrics via:

```python
from utils import redis_pause_manager

metrics = redis_pause_manager.get_metrics()
print(f"Pause set count: {metrics['pause_set_count']}")
print(f"Pause skipped count: {metrics['pause_skipped_count']}")
```

## Troubleshooting

### Tests Fail with "unknown command 'eval'"

This is expected with older versions of fakeredis. The code includes a fallback for this case. Ensure you have:
```bash
pip install fakeredis>=2.20.0
```

### Redis Connection Errors

If you see connection errors in unit tests, it's likely the tests are trying to connect to a real Redis server. Make sure the test fixtures are properly set up to use fakeredis.

### Pause Not Working

1. Check that Redis is running (if using real Redis)
2. Verify Redis connection settings in `.env`
3. Check logs for error messages from PauseManager
4. Ensure bot username is available (`context.bot.username`)

## Production Deployment

For production deployment with Redis:

1. **Use a managed Redis service** (e.g., Redis Cloud, AWS ElastiCache)
2. **Set connection parameters** in environment variables
3. **Enable persistence** in Redis to survive restarts
4. **Monitor metrics** to track pause behavior
5. **Set appropriate buffer** values based on your traffic patterns

### Example: Redis Cloud

```bash
REDIS_HOST=redis-12345.c123.us-east-1-1.ec2.cloud.redislabs.com
REDIS_PORT=12345
REDIS_PASSWORD=your_password_here
```

### Example: Docker Compose

```yaml
services:
  redis:
    image: redis:latest
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  bot:
    build: .
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    depends_on:
      - redis

volumes:
  redis_data:
```

## Further Reading

- [Telegram Bot API Rate Limits](https://core.telegram.org/bots/faq#broadcasting-to-users)
- [Redis Documentation](https://redis.io/documentation)
- [Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
