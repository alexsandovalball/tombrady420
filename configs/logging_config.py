"""
Centralized logging configuration for the application.
Provides consistent structured logging across all modules.
"""
import logging


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure logging with structured format for better visibility.

    Args:
        level: Logging level (default: INFO)
    """
    # Configure basic logging with structured format
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
        level=level,
        force=True  # Ensure this configuration takes precedence
    )

    # Configure specific loggers
    logging.getLogger('telegram').setLevel(logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)  # Reduce noise from HTTP library

    # For uvicorn (if running webhook server)
    try:
        logging.getLogger('uvicorn.access').setLevel(logging.INFO)  # Keep access logs visible
    except Exception:
        pass  # Uvicorn may not be imported in polling mode
