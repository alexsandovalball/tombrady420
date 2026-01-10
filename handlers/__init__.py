# handlers/__init__.py
"""
Handlers package initializer.
Ensures DB migrations run at startup and imports handler modules so they register.
"""

import asyncio
import logging

from database import init_db  # module-level convenience that runs migrations and creates tables

logger = logging.getLogger(__name__)

# Guarded DB init: avoid running init_db at import time when an event loop is already running.
# If this file is executed inside an async startup function, call: await init_db()
try:
    # Prefer get_running_loop to detect an active loop reliably
    try:
        _ = asyncio.get_running_loop()
        loop_running = True
    except RuntimeError:
        loop_running = False

    if not loop_running:
        # Use asyncio.run for a clean import-time initialization
        try:
            asyncio.run(init_db())
            logger.info("Database init_db() completed at import-time startup")
        except Exception:
            logger.exception("init_db() failed when run at import time")
    else:
        # Event loop already running (e.g., under Uvicorn). Skip init here; main.post_init will handle it.
        logger.debug("Event loop already running; skipping import-time init_db()")
except Exception:
    # Any unexpected error during the guarded init check should be logged but not crash import
    logger.exception("Unexpected error while checking/running init_db() at import time")

# Import handler modules so their registration code runs.
# Use relative imports because this is the package initializer.
try:
    from . import admin
    from . import menu
    from . import product_view
    from . import search
    from . import start
    from . import debug
except Exception:
    logger.exception("Failed to import one or more handler modules; check handler filenames and package layout")

# Example: if your handlers expose register(dispatcher) functions, call them here:
# try:
#     admin.register(dispatcher)
#     menu.register(dispatcher)
#     product_view.register(dispatcher)
# except NameError:
#     # dispatcher not available in this module; registration may happen elsewhere
#     pass