# Tests

Test suite for the Telegram bot functionality.

## Running Tests

### Bot Pruning Tests

Tests for inactive bot detection and pruning functionality:

```bash
python tests/test_bot_pruning.py
```

**Test Coverage:**

1. **Inactive Bot Detection** - Validates detection of users from inactive bots
2. **Dry Run Pruning** - Ensures dry run mode doesn't delete data
3. **Actual Pruning** - Verifies actual deletion works correctly
4. **Case-Insensitive Matching** - Tests bot username matching ignores case
5. **Preserve Untracked Users** - Ensures users without bot_username aren't affected

## Test Requirements

Tests use temporary in-memory databases and don't require external dependencies beyond those in `requirements.txt`.

## Future Tests

Additional test files can be added here for:
- Notification handling
- Product categorization
- Search functionality
- Multi-bot delivery
