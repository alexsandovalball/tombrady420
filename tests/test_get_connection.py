"""
Tests for Database.get_connection() and get_file_id_cache_size().
"""
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database


@pytest_asyncio.fixture
async def test_db():
    """Create a temporary test database."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    db = Database(db_path)
    await db.init_db()

    yield db

    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_get_connection_is_async_context_manager(test_db):
    """Database.get_connection() should work as an async context manager."""
    async with test_db.get_connection() as conn:
        assert conn is not None
        # The connection should be usable for queries.
        async with conn.execute("SELECT 1") as cursor:
            row = await cursor.fetchone()
            assert row == (1,)


@pytest.mark.asyncio
async def test_get_connection_closes_after_exit(test_db):
    """Connection obtained via get_connection() should be closed after the block."""
    async with test_db.get_connection() as conn:
        pass  # just enter and exit

    # After the block, trying to use the connection should raise because it's closed.
    with pytest.raises(Exception):
        await conn.execute("SELECT 1")


@pytest.mark.asyncio
async def test_get_file_id_cache_size_returns_int(test_db, monkeypatch):
    """get_file_id_cache_size() should return an integer without raising."""
    import utils.helpers as helpers_module
    import database as db_module

    original_cls = db_module.Database

    class _PatchedDatabase(original_cls):
        def __init__(self, *args, **kwargs):
            # Reuse the test_db's path
            super().__init__(test_db.db_path)

    monkeypatch.setattr(db_module, "Database", _PatchedDatabase)

    result = await helpers_module.get_file_id_cache_size()
    assert isinstance(result, int)
    assert result >= 0
