# CHANGELOG

## [Unreleased] - 2026-02-08

### Fixed - Critical: Random User Blocking Bug
**BREAKING ISSUE**: Users were being randomly blocked even though they didn't block the bot and weren't blocked by admins.

**Root Causes:**
1. `mark_user_invalid()` was called without `bot_id` parameter in notification batch sender, causing global blocking instead of bot-specific invalidation
2. Error detection was too permissive, potentially matching non-permanent errors
3. No pre-send validation to skip users already marked invalid for current bot

**Fixes Applied:**
- Changed all `mark_user_invalid(user_id)` calls to `mark_user_invalid(user_id, bot_id=current_bot_key, reason="chat_not_found")`
- Enhanced `_is_chat_not_found()` to only match specific permanent error patterns
- Added bot-specific invalidation check before sending notifications
- Added explicit logging to distinguish bot-specific vs global blocking

**Migration Required:**
Run the fix script to unblock incorrectly blocked users:
```bash
# Preview users who should be unblocked
python scripts/fix_incorrectly_blocked_users.py --dry-run

# Unblock all candidates
python scripts/fix_incorrectly_blocked_users.py

# Unblock specific user
python scripts/fix_incorrectly_blocked_users.py --user-id 12345
```

### Added - Bot-Specific User Invalidation
- New `bot_invalidations` table to track per-bot user accessibility
- `mark_user_invalid(user_id, bot_id, reason)` now supports bot-specific invalidation
- `mark_user_valid(user_id, bot_id)` to revalidate users when they interact
- `is_user_invalid_for_bot(user_id, bot_id)` to check bot-specific invalidation
- Users can now be invalid for one bot while remaining valid for others

**Benefits:**
- No more false global blocking
- Users can recover automatically by interacting with the bot
- Better handling of multi-bot deployments

### Added - Webhook-Based User Revalidation
- Users are automatically revalidated when they interact with any bot via webhook
- Incoming messages and callback queries trigger `mark_user_valid()`
- Revalidation events are logged for monitoring

### Added - Enhanced Broadcast Telemetry
- Per-bot success/failure counts in broadcast processing
- Detailed failure reasons tracked (chat_not_found, forbidden, rate_limit, etc.)
- JSON telemetry logging at end of each broadcast
- Failed user IDs sample (first 10) for debugging
- Invalidated user count separate from general failures

### Fixed - Admin Broadcast UI Text
- Updated `/broadcast` command help text to clarify text-only support
- Removed mentions of photo/video/document/animation support (not implemented in broadcast flow)
- Users now have accurate expectations about broadcast capabilities

### Added - Improved Logging
- Bot identity logging (username, token fingerprint) on every send attempt
- Explicit "NOT blocking user" logs for non-permanent BadRequest errors
- Warning logs when global blocking occurs (backward compatibility path)
- Revalidation events logged in webhook handlers

### Added - Tools & Scripts
- **scripts/fix_incorrectly_blocked_users.py**: Tool to identify and fix incorrectly blocked users
  - Dry-run mode to preview changes
  - Batch or individual user unblocking
  - Shows bot-specific invalidations vs global blocks
  - Activity-based filtering (users active in last N days)

### Changed - Error Handling
- Stricter `_is_chat_not_found()` detection - only matches truly permanent errors:
  - "chat not found"
  - "user not found"
  - "user is deactivated"
- Other BadRequest errors no longer cause user blocking
- Transient errors (network, rate limits) never mark users invalid

### Testing
- All 26 existing tests still pass
- 5 new tests for bot-specific invalidation and revalidation
- Tests verify:
  - Global blocking (backward compatibility)
  - Bot-specific invalidation (doesn't cause global block)
  - User revalidation (removes bot-specific invalidation)
  - Custom messages use bot-specific invalidation
  - Notifications use bot-specific invalidation

### Database Schema Changes
New table `bot_invalidations`:
```sql
CREATE TABLE bot_invalidations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    bot_id TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1,
    UNIQUE(user_id, bot_id)
)
```

**Migration:** Table is created automatically on `init_db()` - no manual migration needed.

### Backward Compatibility
✅ All changes are backward compatible:
- `mark_user_invalid(user_id)` still works (global block, with warning log)
- NULL `target_bot_key` in message queues handled correctly
- Existing globally blocked users remain blocked until manually unblocked

### Security
✅ No security vulnerabilities introduced:
- More precise error handling reduces false positives
- Bot-specific invalidation improves multi-bot security boundaries
- Automatic revalidation allows legitimate users to recover

---

## Previous Entries
(See git history for older changes)
