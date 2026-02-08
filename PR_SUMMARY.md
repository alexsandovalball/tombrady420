# Multi-Bot Synchronized Broadcasts - Pull Request Summary

## Overview

This PR implements comprehensive support for multi-bot synchronized broadcasts and product notifications. The solution enables multiple bot processes to share a single database, coordinate message delivery, and route communications to users based on their preferred bot - all without hardcoding any bot usernames.

## Problem Statement

The original request was to support multiple Telegram bot instances running simultaneously, where:
- Any bot can process queue broadcasts and notifications targeted to users' preferred bot
- Only the primary bot is responsible for categorizing new products
- The solution must maintain backward compatibility with single-bot deployments
- No bot usernames should be hardcoded (portability requirement)

## Solution Architecture

### Bot Identification Strategy

Instead of hardcoding bot usernames, the solution uses **environment variable names** as bot identifiers:
- **Primary bot**: Uses `BOT_TOKEN` environment variable
- **Secondary bots**: Use `BOT_TOKEN_1`, `BOT_TOKEN_2`, etc.

This approach makes the code portable across different deployments without modification.

### Key Components

1. **Bot Key Detection** (`utils/bot_key.py`)
   - `get_bot_env_key()`: Determines which env var this process uses
   - `is_primary_bot_process()`: Returns true for processes using BOT_TOKEN

2. **User Preference Tracking**
   - New `last_bot_key` column in `bot_users` table
   - Automatically updated on every user interaction
   - Used to route messages to user's preferred bot

3. **Message Routing**
   - New `target_bot_key` columns in queue tables
   - Messages tagged with recipient's preferred bot
   - Each bot process filters queue by its environment key

4. **Backward Compatibility**
   - All new columns are nullable
   - NULL values fall back to original bot_username filtering
   - Single-bot deployments continue working unchanged

## Changes Summary

### Database Schema (`migrations/20260208_add_bot_keys.sql`)

```sql
-- Track which bot a user prefers
ALTER TABLE bot_users ADD COLUMN last_bot_key TEXT;

-- Route custom messages to specific bot
ALTER TABLE custom_message_queue ADD COLUMN target_bot_key TEXT;

-- Route notifications to specific bot
ALTER TABLE notification_queue ADD COLUMN target_bot_key TEXT;
```

### Code Changes

**Files Modified:**
- `database.py`: +331 lines (queue methods, user tracking)
- `utils/bot_key.py`: +130 lines (new file - bot detection)
- `utils/notifications.py`: +70 lines (routing logic)
- `utils/helpers.py`: -28 lines (simplified with bot_key module)
- `main.py`: +8 lines (user tracking)
- `handlers/start.py`: +6 lines (user tracking)

**Files Created:**
- `migrations/20260208_add_bot_keys.sql`: Database migration
- `tests/test_multi_bot_broadcasts.py`: 16 comprehensive tests
- `docs/MULTI_BOT_BROADCASTS.md`: Implementation guide

### Key Features Implemented

1. **Smart User Routing**
   ```python
   # When queuing broadcasts
   users_with_targets = [
       (user_id, user.get("last_bot_key") or current_bot_key)
       for user in eligible_users
   ]
   await db.queue_custom_messages_batch_with_targets(users_with_targets, message)
   ```

2. **Filtered Queue Processing**
   ```python
   # Each bot only processes its targeted messages
   if target_key == current_bot_key:
       # Process this message
   elif target_key is None:
       # Backward compatibility: use bot_username filtering
   ```

3. **Primary Bot Categorization**
   ```python
   # Only primary bot prompts for categorization
   if is_primary_bot_process():
       await notify_admins_for_categorization(context, product_id)
   ```

## Testing

### Test Coverage

16 comprehensive tests covering:
- ✅ Bot environment key detection (6 tests)
- ✅ User preference tracking (3 tests)
- ✅ Message queueing with routing (5 tests)
- ✅ Backward compatibility (2 tests)

**All tests passing** (100% success rate)

### Test Execution

```bash
PYTHONPATH=. python3 -m pytest tests/test_multi_bot_broadcasts.py -v
# Result: 16 passed, 20 warnings in 0.32s
```

## Deployment Guide

### Migration Steps

1. **Apply Database Migration**
   ```bash
   sqlite3 catalog.db < migrations/20260208_add_bot_keys.sql
   ```

2. **Single-Bot Deployments** (no changes needed)
   - Existing configuration continues working
   - New columns populate automatically

3. **Multi-Bot Deployments**
   
   **Primary bot process:**
   ```env
   BOT_TOKEN=your_primary_bot_token
   ADMIN_IDS=123456789
   CHANNEL_ID=-1001234567890
   ```
   
   **Secondary bot processes:**
   ```env
   BOT_TOKEN_1=your_secondary_bot_token
   ADMIN_IDS=123456789
   CHANNEL_ID=-1001234567890
   ```

### Verification

Monitor logs for successful routing:
```
[BOT_TOKEN (@PrimaryBot)] Queued 150 messages. Target distribution: {'BOT_TOKEN': 80, 'BOT_TOKEN_1': 70}
[BOT_TOKEN_1 (@SecondBot)] Filtered out 80 messages, processing 70 targeted to this bot
```

## Backward Compatibility

### Guaranteed Compatibility

1. **Existing Data**: NULL values in new columns
   - Handled gracefully with fallback to bot_username filtering
   - No data migration required

2. **Single-Bot Deployments**: 
   - Code path identical to before
   - Only difference: new fields populated for future

3. **No Breaking Changes**:
   - All new parameters are optional with sensible defaults
   - Existing API calls continue working

### Migration Path

For existing deployments:
1. Deploy code (backward compatible)
2. Apply migration (nullable columns)
3. Restart bot(s) - starts populating new fields
4. (Optional) Add secondary bots by setting BOT_TOKEN_N

## Code Quality

### Code Review Results

- ✅ All feedback addressed
- ✅ Database queries optimized (moved outside loops)
- ✅ Clarity comments added for SQLite specifics
- ✅ Consistent null-checking patterns
- ✅ No security vulnerabilities (CodeQL passed)

### Performance Optimizations

1. **Batch Operations**: All queue methods use batch inserts
2. **Query Efficiency**: Bot user filtering pre-fetched once
3. **Logging**: Target distribution logged for monitoring

## Documentation

Comprehensive guide created at `docs/MULTI_BOT_BROADCASTS.md` includes:
- Architecture overview
- Migration instructions
- Multi-bot deployment configuration
- Troubleshooting guide
- API reference
- Future enhancement ideas

## Security

- ✅ CodeQL security scan passed with 0 alerts
- ✅ No SQL injection vulnerabilities
- ✅ No hardcoded credentials
- ✅ Proper parameterized queries throughout

## Future Enhancements

Potential improvements identified:
1. Load balancing: Distribute new users evenly across bots
2. Health checks: Monitor active bot processes
3. Dynamic routing: Reassign users if bot offline
4. Analytics: Track delivery rates per bot

## Conclusion

This PR successfully implements all requirements:
- ✅ Multi-bot synchronized broadcasts
- ✅ User-preferred bot routing
- ✅ Primary bot categorization control
- ✅ Environment variable-based bot detection
- ✅ Full backward compatibility
- ✅ No hardcoded bot usernames
- ✅ Comprehensive tests (100% passing)
- ✅ Complete documentation

The solution is production-ready and tested. Single-bot deployments continue working unchanged, while multi-bot deployments can be configured by simply adding environment variables.

## Files Changed

```
database.py                        | +331 lines
utils/bot_key.py                   | +130 lines (new)
utils/notifications.py             | +70 lines
utils/helpers.py                   | -28 lines
main.py                            | +8 lines
handlers/start.py                  | +6 lines
migrations/20260208_add_bot_keys.sql | +27 lines (new)
tests/test_multi_bot_broadcasts.py | +277 lines (new)
docs/MULTI_BOT_BROADCASTS.md       | +223 lines (new)
tests/__init__.py                  | new file
```

**Total**: 10 files changed, 1044 insertions(+), 28 deletions(-)
