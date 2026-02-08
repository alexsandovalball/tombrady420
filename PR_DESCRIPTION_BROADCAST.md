# Replace Redis pub/sub broadcast forwarding with DB-backed forwarding

## Summary

This PR implements a **database-backed broadcast forwarding system** to replace the draft Redis-based solution from PR #86. The new system uses SQLite polling to distribute broadcast messages across multiple bot instances without requiring any external dependencies.

## Problem Statement

The original draft PR #86 introduced Redis pub/sub for cross-instance broadcast forwarding, which:
- Requires Redis as an external dependency
- Adds deployment complexity
- Increases infrastructure costs
- Is not compatible with the current Render.com SQLite-based deployment

## Solution

Replace Redis with a **SQLite-backed polling system** that:
- Uses the existing database (no new dependencies)
- Works seamlessly with current deployment
- Maintains backward compatibility
- Provides the same functionality as Redis pub/sub

## How It Works

### Architecture

1. **Broadcast Publishing** (Admin → Database)
   - Admin sends `/broadcast` command
   - Event is inserted into `broadcast_events` table with message text and optional payload
   - Returns immediately with confirmation

2. **Event Distribution** (Database → Bot Instances)
   - Each bot instance runs a background polling task (default: 3-second interval)
   - Polls `broadcast_events` table for unprocessed events (LEFT JOIN with `broadcast_processed`)
   - Processes events for users assigned to this bot instance only

3. **Deduplication** (Processed Tracking)
   - After processing, event is marked in `broadcast_processed` table with `(broadcast_id, bot_key)` composite primary key
   - `INSERT OR IGNORE` prevents duplicate processing errors
   - Each bot processes each broadcast exactly once

### Database Schema

```sql
CREATE TABLE broadcast_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_text TEXT NOT NULL,
    payload_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE broadcast_processed (
    broadcast_id INTEGER NOT NULL,
    bot_key TEXT NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (broadcast_id, bot_key)
);
```

## Changes Made

### New Files

- **`utils/broadcast_forwarder.py`** (150 lines)
  - `publish_broadcast(db, message_text, payload)` - Insert broadcast event
  - `start_listener_in_background(db, callback)` - Start polling loop
  - `_listener_loop(db, callback, bot_key)` - Internal polling implementation
  - Configurable `POLL_INTERVAL_SECONDS = 3`

- **`tests/test_broadcast_forwarder.py`** (450 lines)
  - 18 comprehensive tests covering all scenarios
  - Tests for publish, retrieve, mark processed
  - Multi-bot scenarios validated
  - Error handling tested
  - **All tests passing** ✅

### Modified Files

- **`database.py`**
  - Added `broadcast_events` and `broadcast_processed` table creation
  - Added `insert_broadcast_event()` method
  - Added `get_unprocessed_broadcasts_for_bot()` method
  - Added `mark_broadcast_processed()` method
  - All methods follow existing async/aiosqlite patterns

- **`main.py`**
  - Updated `broadcast_confirm_all` callback to use `publish_broadcast()`
  - Added fallback to local broadcast if DB publish fails
  - Added listener startup in `post_init()` function
  - Graceful error handling with warnings if listener fails to start

- **`webhook_server.py`**
  - Added listener startup for secondary bot instances
  - Each bot gets its own listener polling for its assigned broadcasts
  - Consistent with primary bot setup

- **`utils/notifications.py`**
  - Added `enqueue_local_broadcast(context, message_text)` function
  - Acts as callback for broadcast forwarder
  - Filters users by `last_bot_key` to only queue for this bot's users
  - Reuses existing batch queuing infrastructure

- **`README.md`**
  - Added "Cross-Instance Broadcast Forwarding" section
  - Documented how the system works
  - Added deployment requirements (shared database)
  - Noted WAL mode recommendation for multi-server setups
  - Updated Technical Features list

## Benefits Over Redis

| Feature | Redis Pub/Sub | DB-Backed Polling |
|---------|---------------|-------------------|
| **External Dependencies** | ❌ Requires Redis | ✅ Uses existing SQLite |
| **Deployment Complexity** | ❌ Must deploy/manage Redis | ✅ Zero additional setup |
| **Infrastructure Cost** | ❌ Additional service fees | ✅ No additional costs |
| **Database Compatibility** | ❌ Needs Redis + SQLite | ✅ Single SQLite database |
| **Render.com Compatible** | ❌ Requires paid Redis addon | ✅ Works with free tier |
| **Broadcast Latency** | ~100ms | ~3 seconds (configurable) |
| **Deduplication** | Manual tracking needed | ✅ Built-in via DB constraints |
| **Fault Tolerance** | ❌ Messages lost if Redis down | ✅ Events persist in database |
| **Multi-Server Support** | ✅ Native | ✅ With shared disk/DB |

## Testing

### Unit Tests
- **18 new tests** in `test_broadcast_forwarder.py`
- All existing tests pass (**34/34 total**)
- Test coverage:
  - ✅ Publish broadcast events
  - ✅ Retrieve unprocessed events per bot
  - ✅ Mark events as processed
  - ✅ Idempotent processing (INSERT OR IGNORE)
  - ✅ Multi-bot scenarios (2+ bots process same event)
  - ✅ Callback error handling
  - ✅ Payload serialization/deserialization

### Code Quality
- ✅ Code review passed (4 issues addressed)
- ✅ CodeQL security scan: **0 vulnerabilities**
- ✅ Follows existing code patterns and style
- ✅ Comprehensive error handling and logging

## Deployment Impact

### Backward Compatibility
- ✅ Local broadcasts work if listener fails to start
- ✅ Existing broadcast workflows unchanged
- ✅ No breaking changes to API or commands
- ✅ Database migration handled automatically

### Requirements
- All bot instances must share the same SQLite database file
- For multi-server deployments:
  - Use shared persistent disk (Render.com persistent disk)
  - Or network storage (NFS, EFS)
  - Or centralized database (PostgreSQL with code modifications)
- **Recommended:** Enable WAL mode for better concurrency:
  ```python
  await db.execute("PRAGMA journal_mode=WAL")
  ```

### Performance Considerations
- **Polling overhead:** One SELECT query per bot instance every 3 seconds
- **Database load:** Minimal (indexed queries, small result sets)
- **Latency:** 0-6 seconds average (0.5 × POLL_INTERVAL to 2 × POLL_INTERVAL)
- **Scalability:** Tested with 2 bots, designed for 5-10 concurrent bots

## Migration from Draft PR #86

If you were testing PR #86 (Redis version):
1. Merge this PR instead
2. Remove Redis configuration from environment
3. Ensure all bots share the same database path
4. Restart all bot instances
5. Test broadcast with `/broadcast` command

No data migration needed - the systems are independent.

## Future Enhancements

Potential improvements (not in scope for this PR):
- [ ] Add `POLL_INTERVAL_SECONDS` as environment variable
- [ ] Implement broadcast priority (high/normal/low) via payload
- [ ] Add broadcast scheduling (future delivery time)
- [ ] Cleanup old broadcast_events (older than N days)
- [ ] Metrics: track broadcast delivery times and success rates
- [ ] Admin dashboard: view broadcast history and status

## Conclusion

This PR provides a **production-ready, zero-dependency solution** for cross-instance broadcast forwarding that:
- Works with existing infrastructure
- Maintains simplicity and ease of deployment
- Provides the same functionality as Redis pub/sub
- Passes all tests and security scans
- Is fully documented

Ready for review and merge! 🚀

---

**Related Issues:** Addresses requirement to support cross-instance broadcasts without Redis

**Testing:** 34/34 tests passing, 0 security vulnerabilities

**Documentation:** README updated with deployment guide
