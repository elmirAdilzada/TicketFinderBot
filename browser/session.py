"""
browser/session.py – Extract Cloudflare cookies from the live browser window
and schedule periodic keepalive actions.

Strategy:
  1. Open (or focus) the browser on ticket.ady.az.
  2. Wait for Cloudflare to clear.
  3. Extract cf_clearance and __ddg1_ cookies from the browser's cookie store
     via the DevTools Protocol (CDP) over localhost:9222 (Chrome launched with
     --remote-debugging-port=9222) – no DOM interaction needed.
  4. Schedule keepalive actions on a background thread.

If CDP is unavailable we fall back to reading cookies from the Chrome
SQLite profile database (offline extraction).

IMPORTANT: The browser must be launched manually by the user with:
  chrome.exe --remote-debugging-port=9222
This is a standard developer feature, not automation.
"""
from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from config.settings import KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS
from browser.keepalive import perform_keepalive_action, focus_browser_window

log = logging.getLogger(__name__)

CDP_URL = "http://localhost:9222"
TARGET_DOMAIN = "ticket.ady.az"

# Cookies we must have for Cloudflare to accept our requests
CF_COOKIE_NAMES = {"cf_clearance", "__ddg1_", "__ddg2_", "__cfwaitingroom"}

# ── CDP-based cookie extraction ───────────────────────────────────────────────

def _cdp_get_cookies() -> Optional[dict[str, str]]:
    """
    Use Chrome DevTools Protocol to extract cookies for TARGET_DOMAIN.
    Returns dict of cookie name → value, or None if CDP is unreachable.
    """
    try:
        # Get list of open targets
        resp = requests.get(f"{CDP_URL}/json/list", timeout=5)
        targets = resp.json()
    except Exception as exc:
        log.debug("CDP unreachable: %s", exc)
        return None

    # Find a target that has our site loaded
    ws_url = None
    for t in targets:
        url = t.get("url", "")
        if TARGET_DOMAIN in url:
            ws_url = t.get("webSocketDebuggerUrl")
            break

    if ws_url is None and targets:
        # Fall back to first available page target
        for t in targets:
            if t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl")
                break

    if ws_url is None:
        log.debug("No suitable CDP target found")
        return None

    # Use websocket to send Network.getCookies
    try:
        import websocket  # websocket-client

        cookies: dict[str, str] = {}
        done = threading.Event()

        def on_message(ws, message):
            try:
                data = json.loads(message)
                result = data.get("result", {})
                for c in result.get("cookies", []):
                    if TARGET_DOMAIN in c.get("domain", ""):
                        cookies[c["name"]] = c["value"]
            except Exception:
                pass
            finally:
                done.set()

        def on_open(ws):
            ws.send(json.dumps({"id": 1, "method": "Network.getCookies",
                                 "params": {"urls": [f"https://{TARGET_DOMAIN}"]}}))

        ws_client = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open)
        t = threading.Thread(target=ws_client.run_forever, daemon=True)
        t.start()
        done.wait(timeout=10)
        ws_client.close()

        if cookies:
            log.info("CDP extracted %d cookies for %s", len(cookies), TARGET_DOMAIN)
            log.info("Cookies found: %s", list(cookies.keys()))
            return cookies

    except ImportError:
        log.debug("websocket-client not installed – cannot use CDP websocket")
    except Exception as exc:
        log.debug("CDP websocket error: %s", exc)

    return None


# ── SQLite fallback (Chrome cookie database) ─────────────────────────────────

def _find_chrome_cookie_db() -> Optional[Path]:
    """Locate Chrome's Cookies SQLite file on Windows."""
    appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(appdata) / "Google" / "Chrome" / "User Data" / "Default" / "Cookies",
        Path(appdata) / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies",
        Path(os.path.expanduser("~")) / ".config" / "google-chrome" / "Default" / "Cookies",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _sqlite_get_cookies() -> Optional[dict[str, str]]:
    """
    Read cookies directly from Chrome's Cookies SQLite file.
    Note: values may be encrypted on modern Chrome; returns raw bytes as hex
    for the caller to handle.  This is a best-effort fallback only.
    """
    db_path = _find_chrome_cookie_db()
    if db_path is None:
        log.debug("Chrome Cookies DB not found")
        return None

    # Copy to temp file so Chrome's lock doesn't block us
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    try:
        tmp.write(db_path.read_bytes())
        tmp.flush()
        tmp.close()

        conn = sqlite3.connect(tmp.name)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, value FROM cookies WHERE host_key LIKE ?",
            (f"%{TARGET_DOMAIN}%",),
        )
        rows = cur.fetchall()
        conn.close()

        cookies = {name: value for name, value in rows if value}
        if cookies:
            log.info("SQLite extracted %d cookies (may be encrypted)", len(cookies))
            return cookies
    except Exception as exc:
        log.debug("SQLite cookie extraction failed: %s", exc)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return None


# ── Public interface ──────────────────────────────────────────────────────────

def get_cf_cookies(timeout: int = 120) -> dict[str, str]:
    """
    Wait up to *timeout* seconds for Cloudflare cookies to be present.
    First tries CDP, then SQLite.
    Raises RuntimeError if cookies cannot be obtained.
    """
    deadline = time.monotonic() + timeout
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        log.debug("Cookie extraction attempt %d", attempt)

        # Try CDP first (most reliable)
        cookies = _cdp_get_cookies()
        if cookies and "__cfwaitingroom" in cookies:
            log.info("Got cf_clearance via CDP")
            return cookies

        # Fall back to SQLite
        cookies = _sqlite_get_cookies()
        if cookies and "cf_clearance" in cookies:
            log.info("Got cf_clearance via SQLite")
            return cookies

        log.info(
            "Waiting for Cloudflare clearance cookie… (attempt %d, %.0fs remaining)",
            attempt,
            deadline - time.monotonic(),
        )
        time.sleep(5)

    raise RuntimeError(
        "Could not obtain cf_clearance cookie within timeout. "
        "Please open Chrome with --remote-debugging-port=9222 and "
        f"navigate to https://{TARGET_DOMAIN} manually."
    )


def has_cf_challenge(cookies: dict[str, str]) -> bool:
    """Return True if we appear to be missing a valid Cloudflare token."""
    return "__cfwaitingroom" not in cookies


# ── Keepalive scheduler ───────────────────────────────────────────────────────

class KeepaliveScheduler:
    """
    Background thread that periodically performs human-like browser actions
    to prevent Cloudflare from invalidating the session.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="keepalive", daemon=True
        )
        self._thread.start()
        log.info("Keepalive scheduler started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Keepalive scheduler stopped")

    def _loop(self):
        while not self._stop_event.is_set():
            interval = random.uniform(KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS)
            log.debug("Next keepalive in %.0f seconds", interval)

            # Wait in small increments so we can respond to stop quickly
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(min(10, deadline - time.monotonic()))

            # Focus browser window first
            focus_browser_window(TARGET_DOMAIN)
            time.sleep(random.uniform(0.5, 1.5))

            # Perform a random human-like action
            if not perform_keepalive_action():
                log.debug("Keepalive action skipped (pyautogui unavailable)")
