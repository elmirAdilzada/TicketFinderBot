"""
main.py – ADY Monitor entry point.

Usage:
    python main.py

Prerequisites:
  1. Edit config/settings.py with your Telegram bot token and chat ID.
  2. Launch Chrome with remote debugging enabled:
       chrome.exe --remote-debugging-port=9222
  3. Navigate to https://ticket.ady.az in that browser window.
  4. Wait for Cloudflare to clear (the page loads normally).
  5. Run this script.

The monitor will:
  - Extract Cloudflare session cookies from the browser via CDP.
  - Poll the ADY backend API on a randomized interval (10–60 min).
  - Send Telegram notifications whenever ticket availability changes.
  - Perform natural human-like browser actions every 3–8 minutes to keep
    the Cloudflare session alive.
"""
import signal
import sys

from utils.logging_setup import setup_logging
from monitor.poller import run_monitor

setup_logging()

import logging
log = logging.getLogger(__name__)


def _handle_exit(signum, frame):
    log.info("Received signal %d – shutting down.", signum)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_exit)
    signal.signal(signal.SIGTERM, _handle_exit)

    try:
        run_monitor()
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as exc:
        log.critical("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)
