"""
Structured logging configuration.
Logs include mode (DRY_RUN/LIVE) but never expose secrets.
"""

import logging
import sys
from datetime import datetime
from typing import Optional

from scalperbot.config import settings


class ModeFilter(logging.Filter):
    """Add trading mode to all log records."""

    def filter(self, record):
        record.mode = "DRY_RUN" if settings.dry_run else "LIVE"
        return True


class SafeFormatter(logging.Formatter):
    """Formatter that includes mode and timestamp."""

    def format(self, record):
        # Add mode prefix
        mode_prefix = f"[{record.mode}]" if hasattr(record, 'mode') else ""
        record.mode_prefix = mode_prefix
        return super().format(record)


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Path to log file (optional)

    Returns:
        Configured root logger
    """
    log_level = getattr(logging, (level or settings.log_level).upper(), logging.INFO)

    # Create formatter
    fmt = "%(asctime)s %(mode_prefix)s %(name)s - %(levelname)s - %(message)s"
    formatter = SafeFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # Create mode filter
    mode_filter = ModeFilter()

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(mode_filter)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    file_path = log_file or settings.log_file
    if file_path:
        file_handler = logging.FileHandler(file_path, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(mode_filter)
        root_logger.addHandler(file_handler)

    # Reduce noise from external libraries
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a named logger."""
    return logging.getLogger(name)
