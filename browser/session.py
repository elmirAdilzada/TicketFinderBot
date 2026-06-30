"""
browser/session.py – Playwright Browser Management

Provides a BrowserManager class that launches Chromium (headful but hidden on Linux via Xvfb)
to bypass Cloudflare naturally and execute API fetches natively in the page context.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Optional

from config.settings import KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS

log = logging.getLogger(__name__)

TARGET_DOMAIN = "ticket.ady.az"

class BrowserManager:
    """Manages the Playwright browser lifecycle and virtual display."""

    def __init__(self):
        self._display = None
        self.playwright = None
        self.browser_context = None
        self.page = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        """Starts the browser and initializes the keep-alive loop."""
        log.info("Starting BrowserManager...")
        
        # Start virtual display on Linux
        if sys.platform.startswith("linux"):
            try:
                from pyvirtualdisplay import Display
                log.info("Linux detected. Starting Xvfb virtual display...")
                self._display = Display(visible=0, size=(1920, 1080))
                self._display.start()
            except ImportError:
                log.warning("PyVirtualDisplay not installed. Playwright will run in normal mode.")
            except Exception as exc:
                log.error("Failed to start virtual display: %s", exc)

        # Import playwright
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright is not installed. Run: pip install playwright")

        self.playwright = sync_playwright().start()
        
        # We use a persistent context so cookies (cf_clearance) are saved across restarts
        user_data_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "playwright_profile")
        os.makedirs(user_data_dir, exist_ok=True)
        
        log.info("Launching Playwright Chromium (headless=False) to bypass Cloudflare...")
        
        # headless=False is critical to bypass Cloudflare on both desktop and xvfb
        self.browser_context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
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

        # Use the default page if it exists, or create one
        pages = self.browser_context.pages
        self.page = pages[0] if pages else self.browser_context.new_page()

        # Apply playwright-stealth to hide all automation signals
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(self.page)
            log.info("Playwright-stealth applied successfully")
        except ImportError:
            log.warning("playwright-stealth not installed, skipping stealth mode")
            # Fallback: manually hide webdriver property
            self.browser_context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

        # Navigate to target domain to clear Cloudflare
        target_url = f"https://{TARGET_DOMAIN}"
        log.info("Navigating to %s to solve Cloudflare challenge...", target_url)
        
        self.page.goto(target_url, wait_until="domcontentloaded")
        
        # Wait for Cloudflare to clear
        self._wait_for_cloudflare()
        
        log.info("Cloudflare cleared successfully!")

        # Start keepalive loop
        self._stop_event.clear()
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, name="keepalive", daemon=True)
        self._keepalive_thread.start()

    def _wait_for_cloudflare(self, timeout=60):
        """Wait until the page title is not a Cloudflare challenge title."""
        deadline = time.monotonic() + timeout
        cf_titles = ["Just a moment", "Attention Required", "Access denied"]
        
        while time.monotonic() < deadline:
            title = self.page.title()
            
            # If the title is something real like "ADY", we are probably clear
            if not any(cf in title for cf in cf_titles):
                if "ADY |" in title or "ADY" in title:
                    return # Page has loaded successfully
                
                # Double check that we have the cf_clearance cookie
                cookies = self.browser_context.cookies()
                has_cf = any(c['name'] == 'cf_clearance' for c in cookies)
                if has_cf:
                    return
            
            safe_title = title.encode("ascii", "ignore").decode()
            log.info("Waiting for Cloudflare... (Title: %s)", safe_title)
            time.sleep(3)
            
        raise RuntimeError("Cloudflare challenge not solved within timeout.")

    def stop(self):
        """Stop keepalive, browser, and display."""
        log.info("Stopping BrowserManager...")
        self._stop_event.set()
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=5)
            
        if self.browser_context:
            self.browser_context.close()
        if self.playwright:
            self.playwright.stop()
        if self._display:
            self._display.stop()

    # ── Keepalive Logic ────────────────────────────────────────────────────────
    def _keepalive_loop(self):
        import random
        from browser.keepalive import perform_keepalive_action
        
        while not self._stop_event.is_set():
            interval = random.uniform(KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS)
            log.debug("Next keepalive in %.0f seconds", interval)

            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(min(5, deadline - time.monotonic()))

            try:
                # Reload page occasionally to keep session completely fresh
                if random.random() < 0.2:
                    log.debug("Keepalive: Reloading page")
                    self.page.reload()
                else:
                    perform_keepalive_action(self.page)
            except Exception as exc:
                log.warning("Keepalive action failed: %s", exc)
