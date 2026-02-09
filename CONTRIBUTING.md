# Contributing to Telegram Media Catalog Bot

Thank you for considering contributing to this project! This document provides guidelines and best practices for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Guidelines](#development-guidelines)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Important Technical Constraints](#important-technical-constraints)

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help maintain a welcoming environment

## Getting Started

1. **Fork the repository** and clone your fork
2. **Install dependencies**: `pip install -r requirements.txt`
3. **Set up environment**: Copy `configs/.env.example` to `.env` and configure
4. **Run tests**: `pytest tests/ -v`
5. **Make your changes** following the guidelines below

## Development Guidelines

### Code Style

- Follow PEP 8 style guidelines
- Use meaningful variable and function names
- Add docstrings to all public functions and classes
- Keep functions focused and single-purpose
- Use type hints where appropriate

### Async/Await Patterns

This project uses asyncio extensively. Follow these patterns:

```python
# ✅ GOOD - Proper async function
async def fetch_data():
    result = await db.query()
    return result

# ❌ BAD - Blocking call in async function
async def fetch_data():
    result = db.query_sync()  # Blocks event loop
    return result
```

### Logging

- Use structured logging with appropriate levels
- Include context in log messages
- Use logger names to identify modules

```python
import logging

logger = logging.getLogger(__name__)

# ✅ GOOD - Structured with context
logger.info(f"Processing user {user_id} - action: {action}")

# ❌ BAD - Vague message
logger.info("Processing")
```

## Testing

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_broadcast_reliability.py -v

# Run with coverage
pytest tests/ --cov=. --cov-report=html
```

### Writing Tests

- Use `pytest` and `pytest-asyncio` for async tests
- Mock external dependencies (Telegram API, etc.)
- Test both success and failure paths
- Use descriptive test names

```python
import pytest

class TestMessageSending:
    @pytest.mark.asyncio
    async def test_success_on_primary_no_fallback(self):
        """Test successful send on primary bot doesn't use fallback."""
        # ... test implementation
```

## Pull Request Process

1. **Create a feature branch** from `main`
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** with clear, focused commits
   ```bash
   git commit -m "Add: feature description"
   ```

3. **Run tests** and ensure they pass
   ```bash
   pytest tests/ -v
   ```

4. **Update documentation** if needed
   - Update README.md for user-facing changes
   - Update docstrings for API changes
   - Add to docs/ for architectural changes

5. **Push to your fork** and create a pull request
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Describe your changes** in the PR description
   - What problem does it solve?
   - What changes were made?
   - Are there any breaking changes?
   - Have tests been added/updated?

## Important Technical Constraints

### ⚠️ DO NOT Pickle AsyncIO Objects

**Critical**: Python's `pickle` module cannot serialize asyncio objects. This includes:

- `asyncio.Task` objects
- `telegram.ext.Application` objects  
- Coroutine objects
- Active event loops

#### ❌ WRONG - Will cause errors:
```python
from concurrent.futures import ProcessPoolExecutor

async def shutdown_app(app):
    await app.stop()

# This will fail with "TypeError: cannot pickle '_asyncio.Task' object"
with ProcessPoolExecutor() as executor:
    executor.submit(shutdown_app, app)
```

#### ✅ CORRECT - Use asyncio patterns:
```python
# Sequential shutdown
async def shutdown_apps(apps):
    for app in apps:
        try:
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.exception(f"Error: {e}")

# Concurrent shutdown with asyncio.gather()
async def shutdown_apps_concurrent(apps):
    async def shutdown_one(app):
        await app.stop()
        await app.shutdown()
    
    await asyncio.gather(
        *[shutdown_one(app) for app in apps],
        return_exceptions=True
    )
```

#### Task Cleanup Before Shutdown

Always clean up asyncio tasks before shutdown:

```python
# Clean up tasks from bot_data
cancelled_tasks = []
for key, value in list(app.bot_data.items()):
    if isinstance(value, asyncio.Task):
        if not value.done():
            value.cancel()
            cancelled_tasks.append(value)
        del app.bot_data[key]

# Wait for cancellation
if cancelled_tasks:
    await asyncio.gather(*cancelled_tasks, return_exceptions=True)
```

See [docs/BROADCAST_RELIABILITY.md](docs/BROADCAST_RELIABILITY.md) for more details.

### Multi-Bot Architecture

This project supports multiple bot instances for load distribution:

- **Primary bot** (BOT_TOKEN): Monitors channel for new products
- **Secondary bots** (BOT_TOKEN_1, BOT_TOKEN_2, etc.): Deliver notifications

When adding features:
- Use `get_bot_env_key()` to identify which bot instance is running
- Use database-backed broadcast forwarding for cross-instance communication
- Don't assume bot context is always the primary bot

### Database Patterns

- Use async aiosqlite operations
- Always use context managers for database connections
- Implement proper error handling
- Use parameterized queries to prevent SQL injection

```python
# ✅ GOOD - Async with context manager
async def get_user(user_id: int):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()
```

### Error Handling

Distinguish between permanent and transient errors:

**Permanent errors** (immediate fallback):
- `Forbidden`: User blocked the bot
- `BadRequest` with "chat not found"
- `BadRequest` with "user deactivated"

**Transient errors** (retry with backoff):
- `TimedOut`: Network timeout
- `NetworkError`: Connection issues
- `RetryAfter`: Rate limiting

See `utils/send_utils.py` for reference implementation.

## Project Structure

```
tombrady420/
├── configs/          # Configuration management
├── database.py       # Database layer (aiosqlite)
├── docs/            # Documentation
├── handlers/        # Command and callback handlers
├── main.py          # Bot entry point (polling mode)
├── tests/           # Test suite
├── translations/    # Multi-language support
├── utils/           # Utility modules
│   ├── broadcast_forwarder.py  # Cross-instance messaging
│   ├── notifications.py        # Message delivery
│   └── send_utils.py           # Retry/fallback logic
└── webhook_server.py  # Webhook mode entry point
```

## Key Modules

- **database.py**: All database operations
- **handlers/**: User command handlers (start, menu, search, etc.)
- **utils/notifications.py**: Message delivery and rate limiting
- **utils/broadcast_forwarder.py**: Cross-instance broadcast distribution
- **webhook_server.py**: FastAPI server for webhook mode

## Questions?

- Open an issue for bugs or feature requests
- Check existing issues and PRs before creating new ones
- Review [docs/BROADCAST_RELIABILITY.md](docs/BROADCAST_RELIABILITY.md) for architecture details

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.
