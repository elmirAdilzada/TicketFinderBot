"""
monitor/poller.py – Main polling loop.

Flow:
  1. Extract CF cookies from browser session.
  2. Start keepalive scheduler + Telegram listener.
  3. Every random interval: poll all routes for available dates.
  4. If dates changed → send Telegram notification listing available dates.
  5. User can reply with a date (DD-MM-YYYY) → Telegram listener fetches seat details.
  6. On Cloudflare challenge: pause, notify Telegram, wait for user to solve.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

from config.settings import ROUTES
from config.dynamic_settings import get_setting
from network.api_client import ADYApiClient, CloudflareChallenge
from monitor.state import (
    DateSnapshot,
    load_state,
    save_state,
    diff_dates,
)
from telegram.bot import (
    notify_dates_changed,
    notify_dates_disappeared,
    notify_cloudflare_challenge,
    notify_cloudflare_resolved,
    notify_startup,
    notify_error,
    TelegramListener,
)
from browser.session import get_cf_cookies, KeepaliveScheduler

log = logging.getLogger(__name__)


# ── Route polling (dates only) ────────────────────────────────────────────────

def _poll_route_dates(client: ADYApiClient, route: dict) -> Optional[DateSnapshot]:
    """
    Poll one route: fetch available dates only (no seat details).
    Returns a DateSnapshot or None on failure.
    """
    label = route["label"]
    from_st = route["from_station"]
    to_st = route["to_station"]
    way = route.get("way", 1)

    try:
        trip_dates = client.get_trip_dates(from_st, to_st, way)
    except CloudflareChallenge:
        raise
    except Exception as exc:
        log.warning("get_trip_dates failed for %s: %s", label, exc)
        return None

    snapshot = DateSnapshot(label=label, from_station=from_st, to_station=to_st)

    for td in trip_dates:
        snapshot.dates[td.trip_date_val] = {
            "trip_date_txt": td.trip_date_txt,
            "min_amount": td.min_amount,
        }

    log.info("Polled %s → %d available dates", label, len(snapshot.dates))
    return snapshot


# ── Cloudflare recovery ───────────────────────────────────────────────────────

def _handle_cloudflare_challenge(scheduler: KeepaliveScheduler) -> dict[str, str]:
    """
    Pause polling, notify Telegram, wait for user to solve CF challenge.
    Returns fresh cookies once resolved.
    """
    log.warning("Cloudflare challenge detected – pausing poll loop")
    notify_cloudflare_challenge()
    scheduler.stop()

    # Wait for fresh cookies with a long timeout
    log.info("Waiting for user to solve Cloudflare challenge in browser…")
    while True:
        try:
            cookies = get_cf_cookies(timeout=300)  # 5 min window each attempt
            log.info("Cloudflare resolved – resuming")
            notify_cloudflare_resolved()
            scheduler.start()
            return cookies
        except RuntimeError:
            log.info("Still waiting for CF clearance…")
            time.sleep(30)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_monitor() -> None:
    """
    Entry point for the polling monitor.
    Runs indefinitely until interrupted.
    """
    log.info("ADY Monitor starting…")
    notify_startup(ROUTES)

    # Load persisted state
    state = load_state()
    log.info("Loaded state with %d saved routes", len(state))

    # Get initial CF cookies
    log.info("Extracting Cloudflare cookies from browser…")
    try:
        cf_cookies = get_cf_cookies(timeout=120)
    except RuntimeError as exc:
        log.error("Cannot start without CF cookies: %s", exc)
        notify_error(str(exc))
        return

    # Start keepalive scheduler
    scheduler = KeepaliveScheduler()
    scheduler.start()

    # Build API client
    client = ADYApiClient(cf_cookies)

    import threading
    force_poll_event = threading.Event()

    # Start Telegram listener for interactive date queries
    listener = TelegramListener(
        api_client=client,
        routes=ROUTES,
        force_poll_event=force_poll_event
    )
    listener.start()

    poll_min = get_setting("POLL_MIN_SECONDS", 60)
    poll_max = get_setting("POLL_MAX_SECONDS", 120)
    log.info("Monitor running. Polling every %d–%d minutes.",
             poll_min // 60, poll_max // 60)

    forced_poll_cycle = False
    while True:
        # ── Poll all routes (dates only) ──────────────────────────────────
        log.info("Starting poll cycle…")

        for route in ROUTES:
            label = route["label"]
            try:
                new_snapshot = _poll_route_dates(client, route)
            except CloudflareChallenge:
                cf_cookies = _handle_cloudflare_challenge(scheduler)
                client.refresh_cookies(cf_cookies)
                # Retry this route once
                try:
                    new_snapshot = _poll_route_dates(client, route)
                except Exception as exc:
                    log.error("Route %s failed after CF recovery: %s", label, exc)
                    continue
            except Exception as exc:
                log.error("Unexpected error polling %s: %s", label, exc)
                continue

            if new_snapshot is None:
                log.warning("Poll returned None for %s – skipping", label)
                continue

            # Diff against saved state
            old_snapshot = state.get(label)
            diff = diff_dates(old_snapshot, new_snapshot)

            if diff.has_changes or forced_poll_cycle:
                log.info("Date changes for %s: +%d / -%d (forced=%s)",
                         label, len(diff.new_dates), len(diff.disappeared_dates), forced_poll_cycle)

                # Notify new/updated date list
                if diff.new_dates or (old_snapshot is None) or forced_poll_cycle:
                    notify_dates_changed(label, diff.all_dates, diff.new_dates, force_all=forced_poll_cycle)

                # Notify disappeared dates (only if they actually disappeared)
                if diff.disappeared_dates:
                    disappeared_txts = []
                    for dv in sorted(diff.disappeared_dates):
                        old_info = old_snapshot.dates.get(dv, {}) if old_snapshot else {}
                        txt = old_info.get("trip_date_txt", dv)
                        disappeared_txts.append(txt)
                    notify_dates_disappeared(label, disappeared_txts)
            else:
                log.info("No date changes for %s", label)

            # Update state
            state[label] = new_snapshot

        # Save state after every full cycle
        save_state(state)

        # ── Wait for next poll ─────────────────────────────────────────────
        poll_min = get_setting("POLL_MIN_SECONDS", 60)
        poll_max = get_setting("POLL_MAX_SECONDS", 120)
        
        interval = random.uniform(poll_min, poll_max)
        next_poll = time.strftime(
            "%H:%M:%S", time.localtime(time.time() + interval)
        )
        # Sleep using the event so it can be interrupted
        log.info(
            "Poll cycle complete. Next poll in %.0f min (around %s).",
            interval / 60,
            next_poll,
        )
        if force_poll_event.wait(timeout=interval):
            log.info("Force poll requested via Telegram!")
            force_poll_event.clear()
            forced_poll_cycle = True
        else:
            forced_poll_cycle = False
