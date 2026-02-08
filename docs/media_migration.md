# Cross-Bot Media Delivery Migration Guide

## Overview

This document describes the cross-bot media delivery fallback mechanism implemented to handle Telegram's bot-scoped file_id limitation in multi-bot deployments.

## Problem

In a multi-bot setup (primary bot + secondary bots), Telegram file_ids are scoped to the bot that uploaded them. When:
- Bot A (primary) uploads media to a channel and stores the file_id in the database
- Bot B (secondary) tries to send that media to a user using the stored file_id
- Telegram returns error: "Wrong file identifier/HTTP URL specified"

This breaks media delivery in multi-bot configurations.

## Solution

The hotfix implements a transparent fallback mechanism:

1. **Track uploader**: Each product now records which bot uploaded it (`uploader_bot_id` column)
2. **Detect cross-bot errors**: When sending media fails with "Wrong file identifier" error
3. **Download from uploader**: Use the uploader bot's token to download the media bytes from Telegram
4. **Re-upload with current bot**: Send the downloaded bytes to the user, which assigns a new file_id valid for the current bot
5. **Optional caching**: Future enhancement to cache media in cloud storage (`storage_url` column)

## Database Changes

### New Columns in `products` Table

```sql
-- Stores which bot uploaded this media (e.g., 'BOT_TOKEN', 'BOT_TOKEN_1', 'BOT_TOKEN_2')
uploader_bot_id TEXT NULL

-- Optional: URL to cached media in cloud storage (S3, etc.)
-- Currently unused, reserved for future optimization
storage_url TEXT NULL
```

### Migration

The migration file `migrations/20260208_add_uploader_bot_id.sql` adds these columns with NULL default for backward compatibility.

**For existing databases:**
```bash
# Apply migration manually if needed
sqlite3 catalog.db < migrations/20260208_add_uploader_bot_id.sql
```

**For new deployments:**
These columns are included in the schema automatically when `Database.init_db()` is called.

## Code Changes

### 1. Product Creation (`main.py`)

Products now record which bot uploaded them:

```python
from utils.bot_key import get_bot_env_key

product_id = await db.add_product(
    file_id=file_id,
    file_type=file_type,
    caption=caption,
    message_id=message.message_id,
    chat_id=message.chat.id,
    uploader_bot_id=get_bot_env_key()  # NEW: Record uploader
)
```

### 2. Media Sending (`utils/helpers.py`)

The `send_media_message()` function now accepts optional parameters:

```python
await send_media_message(
    context=context,
    chat_id=chat_id,
    file_id=product["file_id"],
    file_type=product["file_type"],
    caption=caption,
    reply_markup=keyboard,
    product_id=product_id,                          # NEW: For logging
    uploader_bot_id=product.get("uploader_bot_id")  # NEW: Enable fallback
)
```

**Fallback Logic:**
1. Try to send with original file_id
2. If BadRequest with "Wrong file identifier":
   - Check if `uploader_bot_id` is provided
   - Resolve uploader_bot_id → bot_token via `Config.BOT_TOKENS`
   - Call `download_media_from_bot(bot_token, file_id, file_type)`
   - Re-upload bytes using `InputFile` with current bot
   - Log success/failure
3. If fallback fails or uploader_bot_id missing, send text fallback message

### 3. Helper Function (`utils/helpers.py`)

New function to download media from a specific bot:

```python
async def download_media_from_bot(
    bot_token: str,
    file_id: str,
    file_type: str
) -> Optional[bytes]:
    """
    Download media file from Telegram using a specific bot's token.
    Returns media bytes if successful, None otherwise.
    """
```

## Bot Token Resolution

The uploader_bot_id is resolved to an actual bot token using the existing `Config.BOT_TOKENS` list:

| uploader_bot_id | Resolves to |
|----------------|-------------|
| `BOT_TOKEN` | `Config.BOT_TOKENS[0]` (primary bot) |
| `BOT_TOKEN_1` | `Config.BOT_TOKENS[1]` (first secondary) |
| `BOT_TOKEN_2` | `Config.BOT_TOKENS[2]` (second secondary) |

**Security**: Bot tokens are NEVER stored in the database. Only the environment variable name is stored, which is resolved at runtime.

## Migration Strategy for Existing Data

### Option 1: Graceful Degradation (Recommended)
**No action required.** Existing products have `uploader_bot_id = NULL`, which means:
- Same-bot sends continue to work normally
- Cross-bot sends will fall back to text message with error notice
- Users can report problematic products to admins for manual recategorization

### Option 2: Backfill Uploader Bot ID
If all existing products were uploaded by the primary bot:

```sql
-- Update all products to mark primary bot as uploader
UPDATE products 
SET uploader_bot_id = 'BOT_TOKEN' 
WHERE uploader_bot_id IS NULL;
```

### Option 3: Delete and Re-import
For a clean slate (destructive):
```sql
-- Clear all products
DELETE FROM products;

-- Re-import from channel using scan_channel.py or manual admin workflow
```

## Testing

Run the test suite to verify functionality:

```bash
# Test cross-bot media delivery
pytest tests/test_cross_bot_media.py -v

# Test database migrations
pytest tests/test_multi_bot_broadcasts.py::TestProductUploaderBotId -v
```

## Monitoring

Key log messages to monitor:

**Success:**
```
Successfully downloaded media from uploader bot: file_id=ABC..., size=12345 bytes, type=photo
Successfully delivered media via cross-bot fallback: product_id=123, uploader=BOT_TOKEN, current_bot=@secondary_bot
```

**Failures:**
```
Cannot resolve uploader_bot_id 'BOT_TOKEN_99' to bot token. Available tokens: 2
Failed to download media from uploader bot - cannot recover
```

## Performance Considerations

**Current Implementation:**
- Download + re-upload happens synchronously during user request
- Adds ~1-3 seconds latency for cross-bot media delivery
- Uses additional Telegram API quota (2 calls instead of 1)

**Future Optimization (storage_url):**
1. On first cross-bot delivery, download media and upload to S3/cloud storage
2. Store URL in `storage_url` column
3. On subsequent requests, serve from cloud storage instead of re-downloading from Telegram
4. Reduces latency and Telegram API usage

## Troubleshooting

### Error: "Cannot resolve uploader_bot_id to bot token"

**Cause**: Product's `uploader_bot_id` references a bot token that's not in `Config.BOT_TOKENS`.

**Fix**: 
1. Verify all bot tokens are loaded in environment: `BOT_TOKEN`, `BOT_TOKEN_1`, etc.
2. Check logs for `Config.BOT_TOKENS` list during startup
3. If bot was removed from deployment, update product's `uploader_bot_id` or delete the product

### Error: "Failed to download media from uploader bot"

**Cause**: 
- Uploader bot token is invalid/revoked
- Media expired on Telegram (rare, usually >1 week old)
- Network/Telegram API issue

**Fix**:
1. Verify uploader bot token is still valid
2. Check Telegram API status
3. Consider deleting old products or re-importing from source channel

### Media still shows as text fallback

**Cause**: Product doesn't have `uploader_bot_id` set (legacy product).

**Fix**: Backfill uploader_bot_id (see Migration Strategy Option 2).

## Related Files

- `migrations/20260208_add_uploader_bot_id.sql` - Database migration
- `database.py` - Schema and add_product() updates
- `main.py` - Product creation with uploader tracking
- `utils/helpers.py` - Fallback logic and download helper
- `handlers/product_view.py` - Pass uploader_bot_id to send function
- `tests/test_cross_bot_media.py` - Comprehensive test suite

## Changelog

**2026-02-08**: Initial implementation
- Added `uploader_bot_id` and `storage_url` columns
- Implemented cross-bot download+reupload fallback
- Added comprehensive tests and documentation
