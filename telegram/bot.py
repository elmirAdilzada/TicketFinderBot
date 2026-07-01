"""
telegram/bot.py – Send notifications & listen for date queries via Telegram Bot API.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from typing import Optional

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ROUTES
from models.trip import Trip

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None) -> Optional[int]:
    """
    Low-level Telegram message sender.
    Retries once on transient failure.
    Returns the message_id if successful, else None.
    """
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.warning("Telegram not configured – message suppressed:\n%s", text)
        return None

    url = _TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                log.debug("Telegram message sent (len=%d)", len(text))
                data = resp.json()
                return data.get("result", {}).get("message_id")
            log.warning("Telegram HTTP %s: %s", resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            log.warning("Telegram request error (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(3)

    return None


# ── Formatters ────────────────────────────────────────────────────────────────

def _detected_now() -> str:
    return datetime.now().strftime("%d-%m-%Y %H:%M:%S")


def notify_dates_changed(label: str, dates: list[dict], new_date_vals: set[str], force_all: bool = False) -> Optional[int]:
    """
    Send a message listing all available dates for a route.
    Only NEW dates are shown (unless force_all is True). If there are too many, a summary range is sent instead.
    """
    from config.dynamic_settings import get_setting
    max_listed = get_setting("MAX_NEW_DATES_LISTED", 5)

    # Filter to only new dates
    if force_all:
        new_dates = dates
    else:
        new_dates = [d for d in dates if d["date_val"] in new_date_vals]

    if not new_dates:
        return None

    lines = [f"🚆 <b>{label}</b>\n"]

    if len(new_dates) <= max_listed:
        # Few new dates → list them individually
        if force_all:
            lines.append(f"<b>{len(new_dates)} available date(s):</b>")
        else:
            lines.append(f"<b>{len(new_dates)} new date(s):</b>")
            
        for d in new_dates:
            lines.append(f"  📅 {d['trip_date_txt']} — from {d['min_amount']:.2f} AZN")
    else:
        # Many new dates → send a compact summary
        first = new_dates[0]["trip_date_txt"]
        last = new_dates[-1]["trip_date_txt"]
        
        if force_all:
            lines.append(f"📅 <b>{len(new_dates)} available dates!</b>")
        else:
            lines.append(f"📅 <b>{len(new_dates)} new dates available!</b>")
            
        lines.append(f"  {first}  →  {last}")
        # Show price range
        prices = [d["min_amount"] for d in new_dates if d["min_amount"] > 0]
        if prices:
            lines.append(f"  💰 from {min(prices):.2f} AZN")

    lines.append(f"\n💬 Reply with a date (DD-MM-YYYY) to check seats.")
    lines.append(f"<b>Updated:</b> {_detected_now()}")

    # Build inline keyboard markup
    reply_markup = None
    if len(new_dates) <= max_listed and new_dates:
        inline_keyboard = []
        for d in new_dates:
            inline_keyboard.append([
                {"text": f"🔍 {d['trip_date_txt']}", "callback_data": f"check_seat:{d['trip_date_txt']}"}
            ])
        reply_markup = {"inline_keyboard": inline_keyboard}

    return _send("\n".join(lines), reply_markup=reply_markup)


def notify_dates_disappeared(label: str, disappeared_dates: list[str]) -> Optional[int]:
    """Notify when dates are no longer available."""
    lines = [f"❌ <b>{label} — Dates Removed</b>\n"]
    for dt in disappeared_dates:
        lines.append(f"  📅 {dt}")
    lines.append(f"\n<b>Detected:</b> {_detected_now()}")
    return _send("\n".join(lines))


def _send_traintrip_details(label: str, trip: Trip) -> Optional[int]:
    """Format and send detailed seat info for a trip."""
    lines = [
        f"🚆 <b>{label} — {trip.depart_date}</b>\n",
        f"<b>Train:</b> #{trip.train_number}",
        f"<b>Departure:</b> {trip.depart_time}",
        f"<b>Arrival:</b> {trip.arrival_time}",
        f"<b>Total Free Seats:</b> {trip.total_free_seats}",
    ]

    if trip.wagon_classes:
        lines.append("\n<b>Classes:</b>")
        for wc in trip.wagon_classes:
            lines.append(
                f"  • {wc.wagon_type} ({wc.seat_class}): "
                f"{wc.total_free_seats} seats @ {wc.display_price}"
            )

    lines.append(f'\n<a href="https://ticket.ady.az">🔗 Book now</a>')
    return _send("\n".join(lines))


def notify_cloudflare_challenge() -> Optional[int]:
    text = (
        "⚠️ <b>Cloudflare challenge detected</b>\n\n"
        "Please open the browser and solve the challenge manually.\n"
        "Polling has been paused.\n\n"
        f"<b>Time:</b> {_detected_now()}"
    )
    return _send(text)


def notify_cloudflare_resolved() -> Optional[int]:
    text = (
        f"✅ <b>Cloudflare session restored</b>\n"
        f"Polling resumed at {_detected_now()}"
    )
    return _send(text)


def notify_startup(routes: list[dict]) -> Optional[int]:
    route_lines = "\n".join(f"  • {r['label']}" for r in routes)
    text = (
        f"🟢 <b>ADY Monitor started</b>\n\n"
        f"<b>Monitoring routes:</b>\n{route_lines}\n\n"
        f"💬 Send a date (DD-MM-YYYY) anytime to check seat details.\n\n"
        f"<b>Time:</b> {_detected_now()}"
    )
    return _send(text)


def notify_error(message: str) -> Optional[int]:
    text = (
        f"❌ <b>ADY Monitor Error</b>\n\n"
        f"{message}\n\n"
        f"<b>Time:</b> {_detected_now()}"
    )
    return _send(text)


# ── Telegram Listener (background thread) ────────────────────────────────────

class TelegramListener:
    """
    Background thread that listens for incoming Telegram messages.
    When the user sends a date in DD-MM-YYYY format, it fetches
    traintrip details via CDP and replies with seat info.
    """

    def __init__(self, api_client, routes: list[dict], force_poll_event: threading.Event = None,
                 bot_status: dict = None):
        self.api_client = api_client
        self.routes = routes
        self.force_poll_event = force_poll_event
        self.bot_status = bot_status or {}
        self._thread: Optional[threading.Thread] = None
        self._offset = 0
        self._date_pattern = re.compile(r"^\d{2}-\d{2}-\d{4}$")
        self._waiting_for_setting = None

    def start(self):
        # Flush old updates so we don't process stale messages
        self._flush_updates()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        log.info("Telegram listener started – send a date (DD-MM-YYYY) to check seats")

    def _flush_updates(self):
        """Consume all pending updates so we only react to new messages."""
        if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        try:
            resp = requests.get(url, params={"offset": -1, "timeout": 0}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", [])
                if results:
                    self._offset = results[-1]["update_id"] + 1
        except Exception:
            pass

    def _listen_loop(self):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

        while True:
            try:
                resp = requests.get(
                    url, params={"offset": self._offset, "timeout": 30}, timeout=40
                )
                if resp.status_code != 200:
                    time.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1

                    if "message" in update:
                        msg = update["message"]
                        text = msg.get("text", "").strip()
                        chat_id = str(msg.get("chat", {}).get("id", ""))

                        # Only respond to our configured chat
                        if chat_id != str(TELEGRAM_CHAT_ID):
                            continue

                        if self._date_pattern.match(text):
                            self._handle_date_query(text)
                        elif text.lower() in ("/dates", "/status"):
                            self._handle_status_query()
                        elif text.lower() == "/yenilə":
                            _send("🔄 Yoxlanılır... Gözləyin.")
                            if self.force_poll_event:
                                self.force_poll_event.set()
                        elif text.lower() == "/settings":
                            self._handle_settings_menu()
                        elif self._waiting_for_setting:
                            # User is typing a value for a setting
                            self._handle_setting_input(text)
                            
                    elif "callback_query" in update:
                        cb = update["callback_query"]
                        data = cb.get("data", "")
                        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                        
                        # Only respond to our configured chat
                        if chat_id != str(TELEGRAM_CHAT_ID):
                            continue
                            
                        if data.startswith("check_seat:"):
                            date_txt = data.split("check_seat:")[1]
                            self._handle_date_query(date_txt)
                        elif data.startswith("edit_setting:"):
                            setting_key = data.split("edit_setting:")[1]
                            self._waiting_for_setting = setting_key
                            _send(f"✏️ Send the new value for <b>{setting_key}</b>:")
                            
                        # Answer the callback query so the button stops spinning
                        cb_id = cb.get("id")
                        if cb_id:
                            cb_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                            requests.post(cb_url, json={"callback_query_id": cb_id}, timeout=5)

            except Exception as exc:
                log.warning("Telegram listener error: %s", exc)
                time.sleep(5)

    def _handle_setting_input(self, text: str):
        key = self._waiting_for_setting
        self._waiting_for_setting = None
        try:
            val = int(text)
            from config.dynamic_settings import set_setting
            set_setting(key, val)
            _send(f"✅ <b>{key}</b> updated to {val}!")
            self._handle_settings_menu()
        except ValueError:
            _send("❌ Invalid number. Edit cancelled.")
            
    def _handle_settings_menu(self):
        from config.dynamic_settings import get_setting

        # Last poll time
        last_poll = self.bot_status.get("last_poll_time")
        if last_poll:
            last_poll_str = last_poll.strftime("%d.%m.%Y %H:%M:%S")
        else:
            last_poll_str = "Hələ yoxlanılmayıb"

        # Proxy status
        proxy_ok = self.bot_status.get("proxy_ok", True)
        proxy_icon = "🟢" if proxy_ok else "🔴"
        proxy_str = "OK" if proxy_ok else "XƏTA"

        lines = [
            "⚙️ <b>Bot Settings</b>\n",
            f"• POLL_MIN_SECONDS: {get_setting('POLL_MIN_SECONDS', 60)}",
            f"• POLL_MAX_SECONDS: {get_setting('POLL_MAX_SECONDS', 120)}",
            f"• MAX_NEW_DATES_LISTED: {get_setting('MAX_NEW_DATES_LISTED', 5)}\n",
            f"🕐 <b>Son yoxlanma:</b> {last_poll_str}",
            f"{proxy_icon} <b>Proxy/Session:</b> {proxy_str}\n",
            "Select a setting below to change it:"
        ]
        
        inline_keyboard = [
            [{"text": "⏱️ Edit Poll Min", "callback_data": "edit_setting:POLL_MIN_SECONDS"}],
            [{"text": "⏱️ Edit Poll Max", "callback_data": "edit_setting:POLL_MAX_SECONDS"}],
            [{"text": "📋 Edit Max Dates", "callback_data": "edit_setting:MAX_NEW_DATES_LISTED"}]
        ]
        
        _send("\n".join(lines), reply_markup={"inline_keyboard": inline_keyboard})

    def _handle_date_query(self, date_txt: str):
        """User sent DD-MM-YYYY → fetch traintrip for all routes and reply."""
        # Convert DD-MM-YYYY → YYYY-MM-DD
        parts = date_txt.split("-")
        if len(parts) != 3:
            return
        date_val = f"{parts[2]}-{parts[1]}-{parts[0]}"

        log.info("Telegram date query: %s", date_txt)
        _send(f"🔍 Checking seats for <b>{date_txt}</b>...")

        found_any = False
        for route in self.routes:
            label = route["label"]
            try:
                trip = self.api_client.get_traintrip(
                    route["from_station"], route["to_station"], date_val
                )
                if trip:
                    found_any = True
                    _send_traintrip_details(label, trip)
            except Exception as exc:
                log.warning("Failed to fetch traintrip for %s on %s: %s", label, date_val, exc)
                _send(f"⚠️ Failed to check <b>{label}</b> for {date_txt}: {exc}")

            # Delay between routes to avoid ReCaptcha throttling
            time.sleep(3)

        if not found_any:
            _send(f"❌ No trains found for <b>{date_txt}</b> on any route.")

    def _handle_status_query(self):
        """User sent /dates or /status → remind them how to use the bot."""
        _send(
            "ℹ️ <b>ADY Monitor</b>\n\n"
            "Send a date in <b>DD-MM-YYYY</b> format to check available seats.\n"
            f"Example: <code>{datetime.now().strftime('%d-%m-%Y')}</code>\n\n"
            "The monitor automatically notifies you when new travel dates appear."
        )
