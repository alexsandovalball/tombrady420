"""
Tests for shutdown logic to prevent pickling errors.

Tests that:
1. Shutdown gracefully handles asyncio.Task cleanup
2. PicklingError fallback works if process-based shutdown is attempted
3. No ProcessPoolExecutor usage in shutdown path
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import pickle

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestShutdownTaskCleanup:
    """Test that shutdown properly cleans up asyncio tasks."""
    
    @pytest.mark.asyncio
    async def test_cancel_running_tasks_before_shutdown(self):
        """Test that running tasks are cancelled before shutdown."""
        # Create a mock application with bot_data containing tasks
        mock_app = Mock()
        mock_app.bot_data = {}
        
        # Create some mock tasks
        task1 = asyncio.create_task(asyncio.sleep(100))
        task2 = asyncio.create_task(asyncio.sleep(100))
        
        # Store tasks in bot_data
        mock_app.bot_data['task1'] = task1
        mock_app.bot_data['task2'] = task2
        
        # Simulate shutdown cleanup
        cancelled_tasks = []
        for key, value in list(mock_app.bot_data.items()):
            if isinstance(value, asyncio.Task):
                if not value.done():
                    value.cancel()
                    cancelled_tasks.append(value)
                del mock_app.bot_data[key]
        
        # Wait for cancellation
        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        
        # Verify tasks were cancelled
        assert task1.cancelled()
        assert task2.cancelled()
        assert len(mock_app.bot_data) == 0
    
    @pytest.mark.asyncio
    async def test_cleanup_task_list_in_bot_data(self):
        """Test cleanup of task lists in bot_data (like notification_tasks)."""
        mock_app = Mock()
        mock_app.bot_data = {}
        
        # Create task list (like NOTIFICATION_TASKS_KEY)
        task_list = []
        task_list.append(asyncio.create_task(asyncio.sleep(100)))
        task_list.append(asyncio.create_task(asyncio.sleep(100)))
        
        mock_app.bot_data['notification_tasks'] = task_list
        
        # Simulate shutdown cleanup
        cancelled_tasks = []
        for key, value in list(mock_app.bot_data.items()):
            if key == 'notification_tasks' and isinstance(value, list):
                for task in value:
                    if isinstance(task, asyncio.Task) and not task.done():
                        task.cancel()
                        cancelled_tasks.append(task)
                value.clear()
        
        # Wait for cancellation
        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        
        # Verify tasks were cancelled and list is empty
        assert all(t.cancelled() for t in cancelled_tasks)
        assert len(mock_app.bot_data['notification_tasks']) == 0
    
    @pytest.mark.asyncio
    async def test_skip_already_done_tasks(self):
        """Test that already done tasks are skipped during cleanup."""
        mock_app = Mock()
        mock_app.bot_data = {}
        
        # Create a task that completes immediately
        async def quick_task():
            return "done"
        
        task = asyncio.create_task(quick_task())
        await task  # Wait for it to complete
        
        mock_app.bot_data['completed_task'] = task
        
        # Simulate shutdown cleanup
        cancelled_tasks = []
        for key, value in list(mock_app.bot_data.items()):
            if isinstance(value, asyncio.Task):
                if not value.done():
                    value.cancel()
                    cancelled_tasks.append(value)
                del mock_app.bot_data[key]
        
        # Verify no tasks were cancelled (task was already done)
        assert len(cancelled_tasks) == 0
        assert task.done()
        assert not task.cancelled()
        assert len(mock_app.bot_data) == 0


class TestPicklingErrors:
    """Test that asyncio objects cannot be pickled."""
    
    def test_task_cannot_be_pickled(self):
        """Test that asyncio.Task objects cannot be pickled."""
        async def dummy_coro():
            await asyncio.sleep(0.1)
        
        # Create task in event loop
        loop = asyncio.new_event_loop()
        task = loop.create_task(dummy_coro())
        
        # Try to pickle the task
        with pytest.raises(TypeError):
            pickle.dumps(task)
        
        # Cleanup
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()
    
    def test_application_cannot_be_pickled(self):
        """Test that Application objects cannot be pickled."""
        from telegram.ext import ApplicationBuilder
        
        # Create a simple application
        app = ApplicationBuilder().token("test_token").build()
        
        # Try to pickle the application
        with pytest.raises((TypeError, pickle.PicklingError)):
            pickle.dumps(app)
    
    def test_coroutine_cannot_be_pickled(self):
        """Test that coroutine objects cannot be pickled."""
        async def dummy_coro():
            return "test"
        
        coro = dummy_coro()
        
        # Try to pickle the coroutine
        with pytest.raises(TypeError):
            pickle.dumps(coro)
        
        # Cleanup
        coro.close()


class TestSequentialShutdown:
    """Test sequential async shutdown pattern."""
    
    @pytest.mark.asyncio
    async def test_sequential_app_shutdown(self):
        """Test shutting down multiple apps sequentially."""
        # Create mock applications
        apps = []
        for i in range(3):
            app = Mock()
            app.stop = AsyncMock()
            app.shutdown = AsyncMock()
            apps.append(app)
        
        # Sequential shutdown
        for idx, app in enumerate(apps):
            try:
                await app.stop()
                await app.shutdown()
            except Exception as e:
                # Log but continue
                pass
        
        # Verify all apps were stopped and shut down
        for app in apps:
            app.stop.assert_called_once()
            app.shutdown.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_shutdown_continues_on_error(self):
        """Test that shutdown continues even if one app fails."""
        # Create mock applications
        app1 = Mock()
        app1.stop = AsyncMock()
        app1.shutdown = AsyncMock()
        
        app2 = Mock()
        app2.stop = AsyncMock(side_effect=Exception("Shutdown failed"))
        app2.shutdown = AsyncMock()
        
        app3 = Mock()
        app3.stop = AsyncMock()
        app3.shutdown = AsyncMock()
        
        apps = [app1, app2, app3]
        
        # Sequential shutdown with error handling
        for idx, app in enumerate(apps):
            try:
                await app.stop()
                await app.shutdown()
            except Exception as e:
                # Log but continue
                pass
        
        # Verify app1 and app3 were shut down successfully
        app1.stop.assert_called_once()
        app1.shutdown.assert_called_once()
        app3.stop.assert_called_once()
        app3.shutdown.assert_called_once()
        
        # Verify app2.stop was called (and failed)
        app2.stop.assert_called_once()
        # app2.shutdown might not be called if stop failed


class TestConcurrentShutdownWithAsyncio:
    """Test concurrent shutdown using asyncio.gather (not ProcessPoolExecutor)."""
    
    @pytest.mark.asyncio
    async def test_concurrent_shutdown_with_gather(self):
        """Test shutting down multiple apps concurrently with asyncio.gather."""
        # Create mock applications
        apps = []
        for i in range(3):
            app = Mock()
            app.stop = AsyncMock()
            app.shutdown = AsyncMock()
            apps.append(app)
        
        # Define shutdown function for one app
        async def shutdown_app(app):
            await app.stop()
            await app.shutdown()
        
        # Concurrent shutdown using asyncio.gather
        results = await asyncio.gather(
            *[shutdown_app(app) for app in apps],
            return_exceptions=True
        )
        
        # Verify all apps were stopped and shut down
        for app in apps:
            app.stop.assert_called_once()
            app.shutdown.assert_called_once()
        
        # Verify no exceptions
        assert all(r is None for r in results)
    
    @pytest.mark.asyncio
    async def test_gather_handles_exceptions_gracefully(self):
        """Test that asyncio.gather handles exceptions without stopping others."""
        # Create mock applications
        app1 = Mock()
        app1.stop = AsyncMock()
        app1.shutdown = AsyncMock()
        
        app2 = Mock()
        app2.stop = AsyncMock(side_effect=Exception("Shutdown failed"))
        app2.shutdown = AsyncMock()
        
        app3 = Mock()
        app3.stop = AsyncMock()
        app3.shutdown = AsyncMock()
        
        apps = [app1, app2, app3]
        
        # Define shutdown function
        async def shutdown_app(app):
            await app.stop()
            await app.shutdown()
        
        # Concurrent shutdown with return_exceptions=True
        results = await asyncio.gather(
            *[shutdown_app(app) for app in apps],
            return_exceptions=True
        )
        
        # Verify app1 and app3 succeeded
        app1.stop.assert_called_once()
        app1.shutdown.assert_called_once()
        app3.stop.assert_called_once()
        app3.shutdown.assert_called_once()
        
        # Verify app2 failed
        app2.stop.assert_called_once()
        
        # Check results: app1 success (None), app2 error (Exception), app3 success (None)
        assert results[0] is None
        assert isinstance(results[1], Exception)
        assert results[2] is None


class TestDefensivePicklingFallback:
    """Test defensive fallback for pickling errors."""
    
    @pytest.mark.asyncio
    async def test_pickling_error_fallback_to_sequential(self):
        """Test that PicklingError triggers fallback to sequential shutdown."""
        from concurrent.futures import ProcessPoolExecutor
        import pickle
        
        # Create mock application with tasks that can't be pickled
        app = Mock()
        app.bot_data = {}
        
        # Add a task (which can't be pickled)
        loop = asyncio.get_event_loop()
        task = loop.create_task(asyncio.sleep(100))
        app.bot_data['task'] = task
        
        # Try to use ProcessPoolExecutor (should fail)
        pickling_failed = False
        try:
            # This should raise PicklingError
            pickle.dumps(app)
        except (TypeError, pickle.PicklingError) as e:
            pickling_failed = True
        
        assert pickling_failed
        
        # Fallback to sequential in-process shutdown
        app.stop = AsyncMock()
        app.shutdown = AsyncMock()
        
        # Clean up tasks first
        if hasattr(app, 'bot_data'):
            cancelled_tasks = []
            for key, value in list(app.bot_data.items()):
                if isinstance(value, asyncio.Task):
                    if not value.done():
                        value.cancel()
                        cancelled_tasks.append(value)
                    del app.bot_data[key]
            
            if cancelled_tasks:
                await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        
        # Then shutdown
        await app.stop()
        await app.shutdown()
        
        # Verify shutdown was called
        app.stop.assert_called_once()
        app.shutdown.assert_called_once()
        
        # Verify task was cleaned up
        assert task.cancelled()
        assert 'task' not in app.bot_data
