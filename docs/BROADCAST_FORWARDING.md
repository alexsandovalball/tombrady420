# Global Broadcast Forwarding Implementation

This document describes the Redis-based global broadcast forwarding feature that distributes broadcast messages across multiple bot instances.

## Overview

The global broadcast forwarding system uses Redis pub/sub to enable broadcast messages initiated from any bot instance to be delivered by all bot instances. Each bot instance only sends messages to its own users and excludes configured admin users.

## Architecture

```
┌─────────────────┐
│ Admin initiates │
│   broadcast     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ publish_broadcast()     │
│ Publishes to Redis      │
│ Channel: "bot:broadcast"│
└────────┬────────────────┘
         │
         ▼
    ┌────────────────────────┐
    │   Redis Pub/Sub        │
    │   (Distributed Queue)  │
    └─┬──────────────────┬───┘
      │                  │
      ▼                  ▼
┌──────────────┐   ┌──────────────┐
│  Bot 1       │   │  Bot 2       │
│  Listener    │   │  Listener    │
└──────┬───────┘   └──────┬───────┘
       │                  │
       ▼                  ▼
┌──────────────┐   ┌──────────────┐
│ enqueue_     │   │ enqueue_     │
│ local_       │   │ local_       │
│ broadcast()  │   │ broadcast()  │
└──────┬───────┘   └──────┬───────┘
       │                  │
       ▼                  ▼
  Filter users         Filter users
  for Bot 1            for Bot 2
  Exclude admins       Exclude admins
       │                  │
       ▼                  ▼
  Send to users        Send to users
```

## Components

### 1. utils/redis_client.py
- Singleton Redis client
- Connection pooling and health checks
- Configurable via `REDIS_URL` environment variable

### 2. utils/broadcast_forwarder.py
- `publish_broadcast(message_text)` - Publishes broadcast to Redis
- `start_listener_in_background(context, callback)` - Starts Redis listener
- `MinimalContext` - Helper class for listener context

### 3. utils/notifications.py
- `enqueue_local_broadcast(context, message_text, exclude_blocked)` - New method
- Filters users to current bot instance only
- Excludes admin users
- Excludes blocked users
- Includes users with NULL last_bot_key (legacy users)

### 4. main.py & webhook_server.py
- Updated broadcast confirmation handler to publish to Redis
- Starts broadcast listeners at bot startup
- Falls back to local broadcast if Redis not configured

## Configuration

Add to your `.env` file:

```env
REDIS_URL=redis://localhost:6379/0
```

### Redis URL Format
```
redis://[[username:]password@]host[:port][/database]
```

Examples:
- Local Redis: `redis://localhost:6379/0`
- Remote Redis: `redis://user:password@redis.example.com:6379/0`
- Redis Cloud: `redis://default:password@redis-12345.cloud.redislabs.com:12345/0`

## Usage

### With Redis Configured
When `REDIS_URL` is set:
1. Admin uses `/broadcast` command
2. Broadcast is published to Redis
3. All bot instances receive and process the broadcast
4. Each bot sends messages only to its own users

### Without Redis (Fallback)
When `REDIS_URL` is not set:
- Broadcasts work locally on the bot instance that received the command
- No cross-instance distribution
- Maintains backward compatibility

## User Filtering

Each bot instance filters users based on:

1. **Bot Ownership**: Only sends to users with `last_bot_key` matching current bot
2. **Admin Exclusion**: Excludes all users in `ADMIN_IDS` configuration
3. **Blocked Users**: Excludes users marked as blocked (`is_blocked=1`)
4. **Legacy Users**: Includes users with `NULL last_bot_key` (for backward compatibility)

## Testing

Run the test suite:
```bash
pytest tests/test_broadcast_forwarder.py -v
```

Test coverage includes:
- Publish success and error handling
- Listener receives and processes messages
- Redis configuration detection
- Admin filtering
- Bot-specific user filtering

## Monitoring

### Logs
Look for these log messages:
- `[BroadcastForwarder] Published broadcast to N subscribers`
- `[BroadcastForwarder] Listener started for @bot_username`
- `[BroadcastForwarder] Received broadcast on @bot_username`
- `[Notifications] enqueue_local_broadcast: Queued N messages`

### Health Checks
- Redis client pings server every 30 seconds (health check)
- Connection errors are logged and retried automatically
- Listener loop has 1-second timeout for graceful shutdown

## Performance Considerations

### Synchronous Operations
- Redis client uses synchronous operations (not async)
- This is acceptable because:
  - Initial ping only happens once during startup
  - Listener runs in dedicated background task
  - Blocking is minimal (1-second timeout)
  - For production with full async requirements, consider `redis.asyncio`

### Scalability
- Each bot instance runs its own listener
- Messages are filtered locally, reducing Redis traffic
- Broadcasts are fire-and-forget (no waiting for completion)
- Background processing prevents blocking the main bot

## Troubleshooting

### Broadcasts not working across instances
1. Check `REDIS_URL` is set in all bot instances
2. Verify Redis server is running and accessible
3. Check logs for connection errors
4. Ensure all bots are subscribed: look for "Listener started" log

### Users not receiving broadcasts
1. Check user is not an admin (admins are excluded)
2. Verify user is not blocked
3. Check user's `last_bot_key` matches a running bot instance
4. Look for "chat not found" errors in logs

### Redis connection issues
1. Verify `REDIS_URL` format is correct
2. Check Redis server is running: `redis-cli ping`
3. Verify network connectivity to Redis server
4. Check Redis authentication credentials

## Security Considerations

✅ **Passed CodeQL Security Scan** - No vulnerabilities found

- Redis credentials should be kept secure (use environment variables)
- Admin users are excluded from broadcasts to prevent spam
- Message content is not validated (trusted admin input)
- Redis pub/sub does not persist messages (no data retention)

## Future Enhancements

Potential improvements:
1. Use `redis.asyncio` for fully async Redis operations
2. Add broadcast analytics/telemetry
3. Add delivery confirmation tracking
4. Support for media broadcasts (currently text-only)
5. Add rate limiting per bot instance
6. Add broadcast scheduling
