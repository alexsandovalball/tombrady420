# handlers/init.py
"""
Handlers package initializer.
Ensures DB migrations run at startup and imports handler modules so they register.
"""

import asyncio
import logging

from database import init_db  # module-level convenience that runs migrations and creates tables

logger = logging.getLogger(__name__)

# Run DB init/migrations during startup.
# If this file is executed inside an async startup function, replace the run_until_complete call
# with: await init_db()
try:
    asyncio.get_event_loop().run_until_complete(init_db())
    logger.info("Database init_db() completed at startup")
except Exception:
    logger.exception("Failed to run init_db() at startup")

# Import handler modules so their registration code runs.
# Adjust these imports to match the actual module names in your handlers package.
# If your handlers expose explicit register(dispatcher) functions, call them after importing.
try:
    from handlers import admin
    from handlers import menu
    from handlers import product_view
    from handlers import search
    from handlers import start
    from handlers import debug
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