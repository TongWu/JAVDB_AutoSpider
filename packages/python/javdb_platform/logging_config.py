import logging
import os
import sys

from packages.python.javdb_platform.config_helper import cfg


def setup_logging(log_file=None, log_level=None):
    """Setup logging configuration for all modules.

    When *log_file* is provided this is an authoritative call from an
    entry-point script — existing handlers are replaced so the new file
    handler takes effect.

    When *log_file* is ``None`` (level-only update) and the root logger
    already has handlers, only the level is adjusted.  This prevents
    transitive imports from accidentally stripping a file handler that
    was set up earlier.

    Args:
        log_file: Log file path (optional).
        log_level: Log level string, e.g. ``"INFO"`` (optional).
    """
    if log_level is None:
        log_level = cfg('LOG_LEVEL', 'INFO')

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Level-only call and handlers already exist — just update levels.
    if log_file is None and root_logger.handlers:
        for h in root_logger.handlers:
            h.setLevel(numeric_level)
        return root_logger

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)

    return root_logger

def get_logger(name):
    """
    Get a logger with the specified name
    
    Args:
        name: Logger name (usually __name__)
    
    Returns:
        Logger instance
    """
    return logging.getLogger(name) 