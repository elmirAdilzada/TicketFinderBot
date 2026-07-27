import os
import sys
import threading
import time
from typing import Any, Optional
import queue
import logging
from config.settings import KEEPALIVE_MIN_SECONDS, KEEPALIVE_MAX_SECONDS

log = logging.getLogger(__name__)
_STOP = object()

class FirefoxBrowserManager:
    def __init__(self):
        self._pw_thread = None
        self._eval_queue = queue.Queue()
        self._ready_event = threading.Event()
        self._start_error = None
        self._stop_event = threading.Event()

    def start(self):
        self._pw_thread = threading.Thread(target=self._playwright_thread_main)
        self._pw_thread.start()
        self._ready_event.wait(timeout=120)
        if self._start_error:
            raise self._start_error

    def evaluate(self, js_code: str, timeout: float = 20.0) -> Any:
        result_holder = {}
        done = threading.Event()
        self._eval_queue.put(("eval", js_code, result_holder, done))
        done.wait(timeout=timeout + 2)
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")

    def stop(self):
        self._stop_event.set()
        self._eval_queue.put(_STOP)
        if self._pw_thread:
            self._pw_thread.join(timeout=30)

    def _playwright_thread_main(self):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()

            user_data_dir = os.path.join(
                os.path.abspath(os.path.dirname(__file__)), "..", "playwright_profile_firefox"
            )
            os.makedirs(user_data_dir, exist_ok=True)
            
            ctx = pw.firefox.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=[],
                viewport={"width": 1920, "height": 1080},
                locale="az",
            )

            pages = ctx.pages
            page = pages[0] if pages else ctx.new_page()

            page.goto("https://ticket.ady.az", wait_until="domcontentloaded")
            time.sleep(5) # wait for cloudflare
            
            self._ready_event.set()

            while True:
                item = self._eval_queue.get()
                if item is _STOP:
                    break

                kind, payload, result_holder, done_event = item
                try:
                    if kind == "eval":
                        result_holder["result"] = page.evaluate(payload)
                except Exception as exc:
                    result_holder["error"] = exc
                finally:
                    done_event.set()

            ctx.close()
            pw.stop()

        except Exception as exc:
            self._start_error = exc
            self._ready_event.set()

from network.api_client import ADYApiClient
print("Starting Firefox...")
try:
    bm = FirefoxBrowserManager()
    bm.start()
    client = ADYApiClient(bm)

    print("Fetching trip dates...")
    dates = client.get_trip_dates(232, 170)
    print(f"Got {len(dates) if dates else 0} dates.")

    if dates:
        test_date_val = dates[0].trip_date_val
        print(f"Fetching traintrip for {test_date_val}...")
        trip = client.get_traintrip(232, 170, test_date_val)
        print("Success! Free seats:", trip.total_free_seats if trip else "No trip")
except Exception as e:
    print("Failed:", e)
finally:
    if 'bm' in locals():
        bm.stop()
print("Done.")
