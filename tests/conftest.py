"""
Shared fixtures for the test suite.

Provides:
- An in-memory SQLite Database instance (initialised with full schema).
- Minimal async/sync mock helpers for Telegram bot interactions.
"""
import asyncio
import os
import tempfile
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from database import Database


# ---------------------------------------------------------------------------
# Pytest-asyncio configuration
# ---------------------------------------------------------------------------

# Tell pytest-asyncio to use the event loop for all async tests in this
# package automatically (avoids having to decorate every test).
pytest_plugins = ("pytest_asyncio",)


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def in_memory_db(tmp_path):
    """Return a fully-initialised Database instance backed by a temp file.

    SQLite :memory: databases are connection-scoped, so all methods that open
    new connections would see an empty database.  Using a real (temp) file
    avoids this while remaining isolated between tests via pytest's tmp_path.
    """
    db_path = str(tmp_path / "test_catalog.db")
    db = Database(db_path=db_path)
    await db.init_db()
    return db


# ---------------------------------------------------------------------------
# Telegram mock helpers
# ---------------------------------------------------------------------------

def make_mock_user(user_id: int = 1, username: str = "testuser"):
    """Return a minimal mock Telegram User object."""
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.first_name = "Test"
    user.last_name = "User"
    user.is_bot = False
    return user


def make_mock_bot(username: str = "testbot"):
    """Return a minimal mock Telegram Bot object."""
    bot = AsyncMock()
    bot.username = username
    bot.token = "fake:TOKEN"
    return bot


def make_mock_message(chat_id: int = 100, message_id: int = 1, user=None):
    """Return a minimal mock Telegram Message object."""
    msg = MagicMock()
    msg.chat_id = chat_id
    msg.message_id = message_id
    msg.from_user = user or make_mock_user()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    # Simulate a plain text message (no media)
    msg.photo = None
    msg.video = None
    msg.document = None
    msg.animation = None
    msg.audio = None
    return msg


def make_mock_callback_query(data: str, user=None, message=None):
    """Return a minimal mock Telegram CallbackQuery."""
    query = AsyncMock()
    query.data = data
    query.from_user = user or make_mock_user()
    query.message = message or make_mock_message()
    query.answer = AsyncMock()
    return query


def make_mock_update(callback_query=None, message=None, user=None):
    """Return a minimal mock Telegram Update."""
    update = MagicMock()
    update.callback_query = callback_query
    update.message = message
    effective_user = user or make_mock_user()
    update.effective_user = effective_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = 100
    return update


def make_mock_context(bot=None):
    """Return a minimal mock PTB ContextTypes.DEFAULT_TYPE."""
    ctx = MagicMock()
    ctx.bot = bot or make_mock_bot()
    ctx.user_data = {}
    return ctx



# ---------------------------------------------------------------------------
# Telegram mock helpers
# ---------------------------------------------------------------------------

def make_mock_user(user_id: int = 1, username: str = "testuser"):
    """Return a minimal mock Telegram User object."""
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.first_name = "Test"
    user.last_name = "User"
    user.is_bot = False
    return user


def make_mock_bot(username: str = "testbot"):
    """Return a minimal mock Telegram Bot object."""
    bot = AsyncMock()
    bot.username = username
    bot.token = "fake:TOKEN"
    return bot


def make_mock_message(chat_id: int = 100, message_id: int = 1, user=None):
    """Return a minimal mock Telegram Message object."""
    msg = MagicMock()
    msg.chat_id = chat_id
    msg.message_id = message_id
    msg.from_user = user or make_mock_user()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    # Simulate a plain text message (no media)
    msg.photo = None
    msg.video = None
    msg.document = None
    msg.animation = None
    msg.audio = None
    return msg


def make_mock_callback_query(data: str, user=None, message=None):
    """Return a minimal mock Telegram CallbackQuery."""
    query = AsyncMock()
    query.data = data
    query.from_user = user or make_mock_user()
    query.message = message or make_mock_message()
    query.answer = AsyncMock()
    return query


def make_mock_update(callback_query=None, message=None, user=None):
    """Return a minimal mock Telegram Update."""
    update = MagicMock()
    update.callback_query = callback_query
    update.message = message
    effective_user = user or make_mock_user()
    update.effective_user = effective_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = 100
    return update


def make_mock_context(bot=None):
    """Return a minimal mock PTB ContextTypes.DEFAULT_TYPE."""
    ctx = MagicMock()
    ctx.bot = bot or make_mock_bot()
    ctx.user_data = {}
    return ctx
