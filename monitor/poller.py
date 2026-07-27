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
import threading
from typing import Optional

from config.settings import ROUTES
from config.dynamic_settings import get_setting
from network.api_client import ADYApiClient, CloudflareChallenge, RecaptchaError
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
    notify_tracked_date_update,
    TelegramListener,
)

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
    except RecaptchaError:
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


# ── Tracked date checking ─────────────────────────────────────────────────────

def _check_tracked_dates(client: ADYApiClient, state: dict, routes: list) -> None:
    """
    Check tracked dates for all users and notify on seat changes.
    Only fetches traintrip details for dates that are already in the state
    (i.e., confirmed available by the regular poll cycle).

    Raises RecaptchaError or CloudflareChallenge so the main loop can
    handle a browser restart instead of silently skipping everything.
    """
    from config.dynamic_settings import get_setting, set_setting
    from network.api_client import RecaptchaError, CloudflareChallenge

    tracked = get_setting("TRACKED_DATES", {})
    if not tracked:
        return

    updated = False
    api_calls = 0
    MAX_API_CALLS = 15  # Safety limit per cycle

    for chat_id, user_tracks in tracked.items():
        for track in user_tracks:
            date_from = track["date_from"]
            date_to = track["date_to"]
            last_seats = track.get("last_seats", {})

            for route in routes:
                if route.get("notify_only_on_empty"):
                    continue

                label = route["label"]
                from_st = route["from_station"]
                to_st = route["to_station"]
                url_slug = route.get("url_slug")

                route_state = state.get(label)
                if not route_state:
                    continue

                for date_val in sorted(route_state.dates.keys()):
                    if not (date_from <= date_val <= date_to):
                        continue

                    if api_calls >= MAX_API_CALLS:
                        log.info("Tracked dates: hit API call limit (%d), stopping.", MAX_API_CALLS)
                        break

                    try:
                        trip = client.get_traintrip(from_st, to_st, date_val)
                        api_calls += 1
                    except (RecaptchaError, CloudflareChallenge):
                        # Bubble up – the main loop will restart the browser
                        if updated:
                            set_setting("TRACKED_DATES", tracked)
                        raise
                    except Exception as exc:
                        log.warning("Tracked date fetch failed %s %s: %s", label, date_val, exc)
                        continue

                    if not trip:
                        # Date listed but no trip data
                        if label not in last_seats:
                            last_seats[label] = {}
                        if last_seats[label].get(date_val) is not None:
                            last_seats[label][date_val] = None
                            updated = True
                        continue

                    # Build current seat info
                    current_info = {
                        "total": trip.total_free_seats,
                        "classes": ", ".join(
                            f"{wc.wagon_type}: {wc.total_free_seats}"
                            for wc in trip.wagon_classes
                        )
                    }

                    if label not in last_seats:
                        last_seats[label] = {}

                    old_info = last_seats[label].get(date_val)

                    if old_info is None or old_info.get("total") != current_info["total"]:
                        # Seat count changed or first time seeing this date
                        notify_tracked_date_update(
                            chat_id=chat_id,
                            label=label,
                            trip=trip,
                            old_seats=old_info,
                            from_station=from_st,
                            to_station=to_st,
                            url_slug=url_slug,
                        )
                        last_seats[label][date_val] = current_info
                        updated = True

                    time.sleep(2)  # Rate limiting between API calls

                if api_calls >= MAX_API_CALLS:
                    break
            if api_calls >= MAX_API_CALLS:
                break

            track["last_seats"] = last_seats

    if updated:
        set_setting("TRACKED_DATES", tracked)
        log.info("Tracked dates checked and updated (%d API calls).", api_calls)
    else:
        log.debug("Tracked dates checked, no changes (%d API calls).", api_calls)


# ── Cloudflare recovery ───────────────────────────────────────────────────────

def _handle_cloudflare_challenge(browser) -> None:
    """
    Pause polling, notify Telegram, wait for Playwright to solve CF challenge.
    """
    from telegram.bot import notify_cloudflare_challenge, notify_cloudflare_resolved
    log.warning("Cloudflare challenge detected – pausing poll loop")
    notify_cloudflare_challenge()

    # Wait for fresh cookies with a long timeout
    log.info("Waiting for Playwright to solve Cloudflare challenge in browser…")
    try:
        browser.reload_page()
        log.info("Cloudflare resolved – resuming")
        notify_cloudflare_resolved()
    except RuntimeError:
        log.error("Failed to solve Cloudflare challenge automatically.")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_monitor() -> None:
    """
    Entry point for the polling monitor.
    Runs indefinitely until interrupted.
    """
    log.info("ADY Monitor starting…")
    
    # ── Initialization ─────────────────────────────────────────────────────────
    state = load_state()
    log.info("Loaded state with %d saved routes", len(state))

    from browser.session import BrowserManager
    
    browser = BrowserManager()
    client = ADYApiClient(browser)

    # Shared status dict accessible from TelegramListener
    bot_status = {
        "last_poll_time": None,   # datetime of last successful poll cycle
        "proxy_ok": True,
    }

    force_poll_event = threading.Event()
    is_finding_event = threading.Event()
    listener = TelegramListener(
        api_client=client,
        routes=ROUTES,
        force_poll_event=force_poll_event,
        is_finding_event=is_finding_event,
        bot_status=bot_status,
    )
    listener.start()

    notify_startup(ROUTES)

    try:
        browser.start()
    except Exception as exc:
        log.critical("Failed to start browser initially: %s", exc)
        bot_status["proxy_ok"] = False

    poll_min = get_setting("POLL_MIN_SECONDS", 60)
    poll_max = get_setting("POLL_MAX_SECONDS", 120)
    log.info("Monitor running. Polling every %d–%d minutes.",
             poll_min // 60, poll_max // 60)

    forced_poll_cycle = False
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 5

    bot_status["browser_start_time"] = time.time()
    bot_status["last_browser_restart_interval"] = get_setting("BROWSER_RESTART_INTERVAL", 3600)

    while True:
        if force_poll_event.is_set():
            log.info("Force poll requested via Telegram!")
            force_poll_event.clear()
            forced_poll_cycle = True

        # Wait until /startfinding is received or a forced poll is pending
        if not is_finding_event.is_set() and not forced_poll_cycle:
            # Wait with a short timeout to catch force_poll_event quickly
            is_finding_event.wait(timeout=2)
            continue

        # Check if browser needs an hourly restart or if proxy was changed
        browser_restart_interval = get_setting("BROWSER_RESTART_INTERVAL", 3600)
        proxy_changed = bot_status.pop("proxy_changed", False)

        # If the restart interval was changed via settings, reset the timer so
        # the new interval takes effect from now (not from the old start time).
        last_interval = bot_status.get("last_browser_restart_interval", browser_restart_interval)
        if last_interval != browser_restart_interval:
            log.info(
                "Browser restart interval changed (%ds → %ds). Resetting timer.",
                last_interval, browser_restart_interval,
            )
            bot_status["browser_start_time"] = time.time()
            bot_status["last_browser_restart_interval"] = browser_restart_interval

        if proxy_changed or (time.time() - bot_status.get("browser_start_time", time.time()) > browser_restart_interval):
            if proxy_changed:
                log.info("Proxy change detected! Restarting browser immediately...")
            else:
                log.info("Scheduled browser restart triggered to prevent stale sessions.")
                
            try:
                browser.stop()
                import time as _t; _t.sleep(2)
                browser = BrowserManager()
                browser.start()
                client = ADYApiClient(browser)
                listener.api_client = client
                bot_status["browser_start_time"] = time.time()
                bot_status["last_browser_restart_interval"] = browser_restart_interval
                bot_status["proxy_ok"] = True
                log.info("Browser restart completed successfully.")
                
                if not proxy_changed:
                    from telegram.bot import notify_browser_restarted
                    notify_browser_restarted()
            except Exception as exc:
                log.error("Failed to restart browser automatically: %s", exc)
                bot_status["proxy_ok"] = False

        # ── Poll all routes (dates only) ──────────────────────────────────
        log.info("Starting poll cycle…")

        for route in ROUTES:
            label = route["label"]
            try:
                new_snapshot = _poll_route_dates(client, route)
            except CloudflareChallenge:
                _handle_cloudflare_challenge(browser)
                # Retry this route once
                try:
                    new_snapshot = _poll_route_dates(client, route)
                except Exception as exc:
                    log.error("Route %s failed after CF recovery: %s", label, exc)
                    continue
            except RecaptchaError:
                from telegram.bot import notify_recaptcha_error
                log.warning("Recaptcha error detected on %s! Forcing browser restart.", label)
                notify_recaptcha_error()
                try:
                    browser.stop()
                    import time as _t; _t.sleep(2)
                    browser = BrowserManager()
                    browser.start()
                    client = ADYApiClient(browser)
                    listener.api_client = client
                    bot_status["browser_start_time"] = time.time()
                    bot_status["last_browser_restart_interval"] = get_setting("BROWSER_RESTART_INTERVAL", 3600)
                    bot_status["proxy_ok"] = True
                    log.info("Browser restart completed successfully after Recaptcha error.")
                    # Allow the new browser a moment to warm up, then poll immediately
                    time.sleep(5)
                    forced_poll_cycle = True
                except Exception as exc:
                    log.error("Failed to restart browser after Recaptcha error: %s", exc)
                    bot_status["proxy_ok"] = False
                break
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

                if route.get("notify_only_on_empty"):
                    if len(new_snapshot.dates) == 0 and old_snapshot and len(old_snapshot.dates) > 0:
                        from telegram.bot import notify_all_dates_deleted
                        notify_all_dates_deleted(label)
                else:
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

        # ── Check tracked dates ──────────────────────────────────────────────
        try:
            _check_tracked_dates(client, state, ROUTES)
        except RecaptchaError:
            from telegram.bot import notify_recaptcha_error
            log.warning("Recaptcha error during tracked date check! Forcing browser restart.")
            notify_recaptcha_error()
            try:
                browser.stop()
                import time as _t; _t.sleep(2)
                browser = BrowserManager()
                browser.start()
                client = ADYApiClient(browser)
                listener.api_client = client
                bot_status["browser_start_time"] = time.time()
                bot_status["last_browser_restart_interval"] = get_setting("BROWSER_RESTART_INTERVAL", 3600)
                bot_status["proxy_ok"] = True
                log.info("Browser restarted after tracked-date Recaptcha error.")
                time.sleep(5)
                forced_poll_cycle = True
            except Exception as exc:
                log.error("Failed to restart browser after tracked-date Recaptcha error: %s", exc)
                bot_status["proxy_ok"] = False
        except CloudflareChallenge:
            log.warning("Cloudflare challenge during tracked date check! Handling...")
            _handle_cloudflare_challenge(browser)
        except Exception as exc:
            log.warning("Tracked date check failed: %s", exc)

        # ── Update last poll time ────────────────────────────────────────
        import datetime
        bot_status["last_poll_time"] = datetime.datetime.now()

        # ── Proxy / session health check (anomaly detection) ──────────────
        # If ALL *primary* routes return None for N consecutive cycles the
        # session is dead. Routes with notify_only_on_empty are auxiliary
        # (e.g. Anomaly Check) and are excluded – their failure must not
        # trigger a false-positive session restart.
        primary_routes = [r for r in ROUTES if not r.get("notify_only_on_empty")]
        if primary_routes and all(state.get(r["label"]) is None for r in primary_routes):
            consecutive_failures += 1
            log.warning(
                "All primary routes returned None (%d/%d). Possible session/proxy failure.",
                consecutive_failures, MAX_CONSECUTIVE_FAILURES,
            )
            bot_status["proxy_ok"] = False
        else:
            consecutive_failures = 0
            bot_status["proxy_ok"] = True

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            log.error("Session/proxy appears dead. Notifying and restarting browser...")
            notify_error(
                "⚠️ <b>Bot session/proxy failure detected!</b>\n"
                f"All routes failed {consecutive_failures} times in a row.\n"
                "Restarting browser..."
            )
            try:
                browser.stop()
                import time as _t; _t.sleep(5)
                browser = BrowserManager()
                browser.start()
                client = ADYApiClient(browser)
                listener.api_client = client
                bot_status["browser_start_time"] = time.time()
                bot_status["last_browser_restart_interval"] = get_setting("BROWSER_RESTART_INTERVAL", 3600)
                consecutive_failures = 0
                bot_status["proxy_ok"] = True
                notify_error("✅ Browser restarted successfully.")
            except Exception as exc:
                log.error("Failed to restart browser: %s", exc)
                notify_error(f"❌ Browser restart failed: {exc}")

        if not is_finding_event.is_set():
            # If search is paused, reset forced flag and loop back to the pause check
            forced_poll_cycle = False
            continue

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
