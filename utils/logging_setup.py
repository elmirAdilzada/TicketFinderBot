"""
utils/logging_setup.py – Configure logging for the application.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from config.settings import LOG_FILE, LOG_LEVEL


def setup_logging() -> None:
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # Rotating file handler (5 MB × 3 backups)
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # Silence noisy third-party loggers
    for name in ("urllib3", "requests", "websocket"):
        logging.getLogger(name).setLevel(logging.WARNING)
