"""Logging setup for PrintSVC."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def _build_formatter():
    return logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def _log_backup_paths(log_file):
    yield log_file
    for index in range(1, LOG_BACKUP_COUNT + 1):
        yield f"{log_file}.{index}"


def setup_logging(log_file=None, level=logging.INFO):
    formatter = _build_formatter()

    root = logging.getLogger("PrintSVC")
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        fh = RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    return root


def clear_log_file(log_file):
    if not log_file:
        return False

    root = logging.getLogger("PrintSVC")
    formatter = None
    file_handlers = []

    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            file_handlers.append(handler)
            if formatter is None and handler.formatter is not None:
                formatter = handler.formatter

    for handler in file_handlers:
        handler.acquire()
        try:
            handler.flush()
            handler.close()
        finally:
            handler.release()
        if handler in root.handlers:
            root.removeHandler(handler)

    with open(log_file, "w", encoding="utf-8"):
        pass

    fh = RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
    fh.setFormatter(formatter or _build_formatter())
    root.addHandler(fh)
    return True
