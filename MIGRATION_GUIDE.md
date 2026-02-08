# Migration Guide: Broadcast Fixes & Bot-Specific Invalidation

## Overview
This update fixes a critical bug where users were being randomly blocked, and adds bot-specific user invalidation for better multi-bot support.

## What Changed?

### 1. Bot-Specific Invalidation (Breaking Change for Multi-Bot Setups)
**Before:** Users were globally blocked when any bot couldn't reach them  
**After:** Users are marked invalid only for the specific bot that encountered the error

**Impact:** 
- Users can now use Bot A even if Bot B can't reach them
- Better handling of multi-bot deployments
- Automatic recovery via webhook revalidation

### 2. Stricter Error Detection
**Before:** Any BadRequest error could cause blocking  
**After:** Only permanent errors ("chat not found", "user deactivated") cause invalidation

**Impact:**
- Fewer false positives
- Temporary errors no longer block users
- Better error logging

### 3. Webhook-Based Revalidation
**New Feature:** Users are automatically revalidated when they interact with the bot

**Impact:**
- Users recover automatically from temporary issues
- No manual admin intervention needed for most cases

## Migration Steps

### Step 1: Update Your Deployment

1. **Pull the latest changes:**
   ```bash
   git pull origin main
   ```

2. **Install dependencies (if needed):**
   ```bash
   pip install -r requirements.txt
   ```

3. **Restart your bot:**
   - The new `bot_invalidations` table is created automatically on startup
   - No manual database migration required

### Step 2: Fix Incorrectly Blocked Users

**IMPORTANT:** Some users may have been incorrectly blocked before this fix.

1. **Preview affected users (dry run):**
   ```bash
   python scripts/fix_incorrectly_blocked_users.py --dry-run
   ```

2. **Review the output:**
   - Look for users who were recently active but are blocked
   - Check if they have bot-specific invalidations (new feature)

3. **Unblock affected users:**
   ```bash
   # Unblock all candidates
   python scripts/fix_incorrectly_blocked_users.py
   
   # OR unblock specific user
   python scripts/fix_incorrectly_blocked_users.py --user-id 12345
   ```

4. **Monitor the logs:**
   - Watch for revalidation events as users interact again
   - Check telemetry in broadcast logs

### Step 3: Verify the Fix

1. **Send a test broadcast:**
   ```
   /broadcast
   ```

2. **Check the logs for:**
   - Bot identity on each send attempt
   - Per-bot telemetry summary
   - "NOT blocking user" messages for non-permanent errors

3. **Verify webhook revalidation:**
   - Have a test user interact with the bot
   - Check logs for: `Revalidated user X for bot BOT_TOKEN`

## New Behavior Examples

### Example 1: Chat Not Found Error
**Before:**
```
User 12345 chat not found (BadRequest), marking as invalid
→ User globally blocked for ALL bots
```

**After:**
```
User 12345 chat not found for bot BOT_TOKEN_1 (BadRequest: Chat not found), marking invalid for this bot only
→ User invalid ONLY for BOT_TOKEN_1
→ User can still use BOT_TOKEN, BOT_TOKEN_2, etc.
```

### Example 2: Temporary BadRequest Error
**Before:**
```
BadRequest error sending to user 12345: Message is too long
→ User globally blocked
```

**After:**
```
BadRequest error for user 12345 via bot @mybot: Message is too long - NOT blocking user
→ User NOT blocked
→ Message marked as sent to avoid retry
```

### Example 3: User Interacts After Being Marked Invalid
**Before:**
```
→ User stays blocked until admin manually unblocks
```

**After:**
```
Received webhook request for primary bot
Revalidated user 12345 for bot BOT_TOKEN
→ User automatically revalidated
→ Can receive messages again from this bot
```

## Configuration

### Optional Environment Variables

No new environment variables are required. The fix works automatically.

If you want to track which bot is primary for legacy user migration (optional):
```bash
PRIMARY_BOT_USERNAME=your_primary_bot_username
```

## Monitoring

### Key Log Messages to Watch

**Good Signs:**
```
Revalidated user X for bot BOT_TOKEN
[Send] ✓ Sent to user X via bot @mybot
Broadcast telemetry: {"sent": 45, "failed": 2, "invalidated": 0}
```

**Warning Signs:**
```
GLOBAL BLOCK: Marking user X as globally invalid (no bot_id specified)
→ Indicates code is still calling mark_user_invalid without bot_id (report this!)
```

**Expected in Logs:**
```
BadRequest error for user X: <any non-permanent error> - NOT blocking user
→ This is normal - user not blocked for temporary errors
```

### Broadcast Telemetry

New JSON telemetry at the end of each broadcast:
```json
{
  "bot": "BOT_TOKEN (@mybot)",
  "total_queued": 50,
  "sent": 45,
  "failed": 5,
  "invalidated": 2,
  "failure_reasons": {
    "chat_not_found": 2,
    "rate_limited": 3
  },
  "failed_user_ids_sample": [123, 456, 789]
}
```

## Troubleshooting

### Q: Users still getting blocked randomly
**A:** Check logs for "GLOBAL BLOCK" warnings. If you see these, there may be code still calling `mark_user_invalid()` without `bot_id`. Report this as a bug.

### Q: How do I manually unblock a user?
**A:** Use the admin commands or the fix script:
```bash
# Via admin command (in bot)
/unblock

# Via script
python scripts/fix_incorrectly_blocked_users.py --user-id 12345
```

### Q: How can I see which users are invalid for which bots?
**A:** Query the `bot_invalidations` table:
```sql
SELECT user_id, bot_id, reason, created_at 
FROM bot_invalidations 
WHERE is_active = 1 
ORDER BY created_at DESC;
```

### Q: Will this affect existing broadcasts?
**A:** No. The fix improves reliability and reduces false blocking. Broadcasts will work better than before.

### Q: Do I need to run the fix script on every deployment?
**A:** No. Run it once after updating to fix users who were incorrectly blocked by the old code. New users won't have this problem.

## Support

If you encounter issues:
1. Check the logs for error details
2. Run the fix script in dry-run mode to see affected users
3. Report issues with log excerpts showing the problem

## Rollback (Emergency Only)

If you must rollback:
1. The `bot_invalidations` table will remain but be ignored
2. Old code will use global `is_blocked` flag
3. You may need to manually review and unblock users

**Note:** Rollback is not recommended. The fix improves reliability significantly.
