"""
browser/session.py – Playwright Browser Management

Provides a BrowserManager class that launches Chromium (headful but hidden on Linux via Xvfb)
to bypass Cloudflare naturally and execute API fetches natively in the page context.

THREADING MODEL:
  Playwright sync API can only be used from the thread that created it.
  To allow calls from multiple threads (TelegramListener, keepalive, poller),
  we use a dedicated "playwright thread" that owns the browser and processes
  all page.evaluate() requests via a thread-safe Queue.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from typing import Any, Optional

from config.settings import KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS

log = logging.getLogger(__name__)

TARGET_DOMAIN = "ticket.ady.az"

# Sentinel to signal the playwright thread to stop
_STOP = object()


class BrowserManager:
    """Manages the Playwright browser lifecycle and virtual display.

    All page.evaluate() calls are routed through a thread-safe queue and
    executed on the dedicated playwright thread, avoiding the greenlet
    "Cannot switch to a different thread" error.
    """

    def __init__(self):
        self._display = None
        self._pw_thread: Optional[threading.Thread] = None
        self._eval_queue: queue.Queue = queue.Queue()
        self._ready_event = threading.Event()
        self._start_error: Optional[Exception] = None
        self._stop_event = threading.Event()
        self._keepalive_thread: Optional[threading.Thread] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the browser on its own dedicated thread and wait until ready."""
        log.info("Starting BrowserManager...")

        # Start Xvfb virtual display on Linux
        if sys.platform.startswith("linux"):
            try:
                from pyvirtualdisplay import Display
                log.info("Linux detected. Starting Xvfb virtual display...")
                self._display = Display(visible=0, size=(1920, 1080))
                self._display.start()
            except ImportError:
                log.warning("PyVirtualDisplay not installed.")
            except Exception as exc:
                log.error("Failed to start virtual display: %s", exc)

        # Launch the playwright thread
        self._pw_thread = threading.Thread(
            target=self._playwright_thread_main,
            name="playwright",
            daemon=True,
        )
        self._pw_thread.start()

        # Wait until the browser is ready (or failed)
        self._ready_event.wait(timeout=120)
        if self._start_error:
            raise self._start_error

    def evaluate(self, js_code: str, timeout: float = 20.0) -> Any:
        """
        Execute js_code in the browser page from ANY thread.
        Blocks until the result is ready and returns the value.
        """
        result_holder: dict = {}
        done = threading.Event()
        self._eval_queue.put(("eval", js_code, result_holder, done))
        done.wait(timeout=timeout + 2)
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")

    def reload_page(self):
        """Reload the browser page from any thread (used for CF recovery)."""
        result_holder: dict = {}
        done = threading.Event()
        self._eval_queue.put(("reload", None, result_holder, done))
        done.wait(timeout=30)

    def stop(self):
        """Shut down the browser and virtual display."""
        log.info("Stopping BrowserManager...")
        self._stop_event.set()
        self._eval_queue.put(_STOP)
        if self._pw_thread:
            self._pw_thread.join(timeout=10)
        if self._display:
            self._display.stop()

    # ── Playwright Thread ──────────────────────────────────────────────────────

    def _playwright_thread_main(self):
        """Runs entirely in the dedicated playwright thread."""
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()

            user_data_dir = os.path.join(
                os.path.abspath(os.path.dirname(__file__)), "..", "playwright_profile"
            )
            os.makedirs(user_data_dir, exist_ok=True)

            # Build proxy config
            from config.settings import PROXY_SERVER, PROXY_USERNAME, PROXY_PASSWORD
            proxy_config = None
            if PROXY_SERVER:
                proxy_config = {"server": PROXY_SERVER}
                if PROXY_USERNAME:
                    proxy_config["username"] = PROXY_USERNAME
                    proxy_config["password"] = PROXY_PASSWORD
                log.info("Using proxy: %s", PROXY_SERVER)

            log.info("Launching Playwright Chromium...")
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                proxy=proxy_config,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--window-size=1920,1080",
                ],
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
            )

            pages = ctx.pages
            page = pages[0] if pages else ctx.new_page()

            # Apply stealth
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
                log.info("Playwright-stealth applied")
            except ImportError:
                ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )

            # Navigate and wait for Cloudflare
            log.info("Navigating to https://%s ...", TARGET_DOMAIN)
            page.goto(f"https://{TARGET_DOMAIN}", wait_until="domcontentloaded")
            self._wait_for_cloudflare(page, ctx)
            log.info("Cloudflare cleared successfully!")

            # Start keepalive thread (also routes through the queue)
            self._stop_event.clear()
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, daemon=True, name="keepalive"
            )
            self._keepalive_thread.start()

            # Signal that we're ready
            self._ready_event.set()

            # ── Event loop: process evaluate/reload requests ─────────────────
            while True:
                item = self._eval_queue.get()
                if item is _STOP:
                    break

                kind, payload, result_holder, done_event = item
                try:
                    if kind == "eval":
                        result_holder["result"] = page.evaluate(payload)
                    elif kind == "reload":
                        page.reload(wait_until="domcontentloaded")
                        self._wait_for_cloudflare(page, ctx, timeout=120)
                    elif kind == "keepalive":
                        from browser.keepalive import perform_keepalive_action
                        if payload == "reload":
                            page.reload()
                        else:
                            perform_keepalive_action(page)
                except Exception as exc:
                    result_holder["error"] = exc
                finally:
                    done_event.set()

            ctx.close()
            pw.stop()

        except Exception as exc:
            self._start_error = exc
            self._ready_event.set()

    def _wait_for_cloudflare(self, page, ctx, timeout=60):
        """Called only from the playwright thread."""
        deadline = time.monotonic() + timeout
        cf_titles = ["Just a moment", "Attention Required", "Access denied"]

        while time.monotonic() < deadline:
            title = page.title()
            if not any(cf in title for cf in cf_titles):
                if "ADY" in title:
                    return
                cookies = ctx.cookies()
                if any(c["name"] == "cf_clearance" for c in cookies):
                    return

            safe_title = title.encode("ascii", "ignore").decode()
            log.info("Waiting for Cloudflare... (Title: %s)", safe_title)
            time.sleep(3)

        raise RuntimeError("Cloudflare challenge not solved within timeout.")

    # ── Keepalive Loop ─────────────────────────────────────────────────────────

    def _keepalive_loop(self):
        """Runs in a separate thread; submits keepalive tasks to the queue."""
        import random

        while not self._stop_event.is_set():
            interval = random.uniform(KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS)
            log.debug("Next keepalive in %.0f seconds", interval)

            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(min(5, deadline - time.monotonic()))

            action = "reload" if random.random() < 0.2 else "mouse"
            result_holder: dict = {}
            done = threading.Event()
            self._eval_queue.put(("keepalive", action, result_holder, done))
            done.wait(timeout=15)
            if "error" in result_holder:
                log.warning("Keepalive action failed: %s", result_holder["error"])
