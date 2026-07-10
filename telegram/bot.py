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


def _get_users() -> dict:
    from config.dynamic_settings import get_setting, set_setting
    allowed = get_setting("ALLOWED_CHAT_IDS", {})
    if isinstance(allowed, list):
        new_allowed = {}
        for c in allowed:
            new_allowed[str(c)] = {
                "name": "Unknown User",
                "notifications": False,
                "perms": {
                    "startfinding": False,
                    "stopfinding": False,
                    "check": False,
                    "settings": False,
                    "dates": False
                }
            }
        set_setting("ALLOWED_CHAT_IDS", new_allowed)
        return new_allowed
    return allowed

def _get_broadcast_chat_ids() -> list[str]:
    chats = set()
    if TELEGRAM_CHAT_ID:
        chats.add(str(TELEGRAM_CHAT_ID))
    users = _get_users()
    for cid, data in users.items():
        if data.get("notifications", False):
            chats.add(str(cid))
    return list(chats)


def _send(text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None, chat_id: Optional[str] = None) -> Optional[int]:
    """
    Low-level Telegram message sender.
    Retries once on transient failure.
    Returns the message_id if successful, else None.
    If chat_id is provided, sends only to that chat. Otherwise broadcasts to all authorized chats.
    """
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.warning("Telegram not configured – message suppressed:\n%s", text)
        return None

    url = _TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    
    target_chats = [chat_id] if chat_id else _get_broadcast_chat_ids()
    last_msg_id = None

    for cid in target_chats:
        if not cid:
            continue
            
        payload = {
            "chat_id": cid,
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
                    log.debug("Telegram message sent (len=%d) to %s", len(text), cid)
                    data = resp.json()
                    last_msg_id = data.get("result", {}).get("message_id")
                    break
                log.warning("Telegram HTTP %s: %s", resp.status_code, resp.text[:200])
            except requests.RequestException as exc:
                log.warning("Telegram request error (attempt %d): %s", attempt + 1, exc)
                if attempt == 0:
                    time.sleep(3)

    return last_msg_id


def set_bot_commands() -> None:
    """Sets the default commands in the Telegram bot command palette."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
    commands = [
        {"command": "startfinding", "description": "Start automatic search"},
        {"command": "stopfinding", "description": "Pause automatic search"},
        {"command": "settings", "description": "Configure monitor settings"},
        {"command": "check", "description": "Force manual check now"},
        {"command": "dates", "description": "Show monitor status & instructions"}
    ]
    
    try:
        requests.post(url, json={"commands": commands}, timeout=10)
    except Exception as exc:
        log.warning("Failed to set bot commands: %s", exc)


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


def _send_traintrip_details(label: str, trip: Trip, chat_id: str) -> Optional[int]:
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
    return _send("\n".join(lines), chat_id=chat_id)


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
    # Set bot commands in the command palette
    set_bot_commands()
    
    route_lines = "\n".join(f"  • {r['label']}" for r in routes)
    text = (
        f"🟢 <b>ADY Monitor started</b>\n\n"
        f"<b>Monitoring routes:</b>\n{route_lines}\n\n"
        f"<b>Commands:</b>\n"
        f"▶️ /startfinding - Start searching for tickets\n"
        f"⏸️ /stopfinding - Pause searching for tickets\n"
        f"⚙️ /settings - Configure bot settings\n"
        f"🔄 /check - Force manual check\n"
        f"📅 /dates - Show available dates\n\n"
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


def notify_browser_restarted() -> Optional[int]:
    return _send("🔄 <b>Browser session restarted automatically to prevent stale tokens.</b>")


# ── Telegram Listener (background thread) ────────────────────────────────────

class TelegramListener:
    """
    Background thread that listens for incoming Telegram messages.
    When the user sends a date in DD-MM-YYYY format, it fetches
    traintrip details via CDP and replies with seat info.
    """

    def __init__(self, api_client, routes: list[dict], force_poll_event: threading.Event = None,
                 is_finding_event: threading.Event = None,
                 bot_status: dict = None):
        self.api_client = api_client
        self.routes = routes
        self.force_poll_event = force_poll_event
        self.is_finding_event = is_finding_event
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

                        admin_chat = str(TELEGRAM_CHAT_ID)
                        is_admin = (chat_id == admin_chat)
                        users = _get_users()
                        user_data = users.get(chat_id, {})

                        def has_perm(cmd_name: str) -> bool:
                            if is_admin: return True
                            if chat_id in users:
                                return user_data.get("perms", {}).get(cmd_name, False)
                            return False

                        if text.lower() == "/start":
                            _send(f"👋 <b>Welcome!</b>\nYour Chat ID is: <code>{chat_id}</code>\nPlease ask the admin to approve this ID.", chat_id=chat_id)
                            continue

                        if text.lower().startswith("/addchat ") and is_admin:
                            new_chat = text.split(" ")[1].strip()
                            from config.dynamic_settings import get_setting, set_setting
                            allowed = get_setting("ALLOWED_CHAT_IDS", {})
                            if new_chat not in allowed:
                                allowed[new_chat] = {
                                    "name": "Unknown User",
                                    "notifications": False,
                                    "perms": {
                                        "startfinding": False,
                                        "stopfinding": False,
                                        "check": False,
                                        "settings": False,
                                        "dates": False
                                    }
                                }
                                set_setting("ALLOWED_CHAT_IDS", allowed)
                                _send(f"✅ Chat ID {new_chat} has been added.", chat_id=chat_id)
                            else:
                                _send(f"ℹ️ Chat ID {new_chat} is already authorized.", chat_id=chat_id)
                            continue

                        if text.lower().startswith("/removechat ") and is_admin:
                            old_chat = text.split(" ")[1].strip()
                            from config.dynamic_settings import get_setting, set_setting
                            allowed = get_setting("ALLOWED_CHAT_IDS", {})
                            if old_chat in allowed:
                                del allowed[old_chat]
                                set_setting("ALLOWED_CHAT_IDS", allowed)
                                _send(f"✅ Chat ID {old_chat} has been removed.", chat_id=chat_id)
                            else:
                                _send(f"ℹ️ Chat ID {old_chat} was not in the authorized list.", chat_id=chat_id)
                            continue

                        if self._date_pattern.match(text):
                            if has_perm("dates"):
                                self._handle_date_query(text, chat_id)
                        elif text.lower() in ("/dates", "/status"):
                            if has_perm("dates"):
                                self._handle_status_query(chat_id)
                        elif text.lower() == "/check":
                            if has_perm("check"):
                                _send("🔄 Checking... Please wait.", chat_id=chat_id)
                                if self.force_poll_event:
                                    self.force_poll_event.set()
                        elif text.lower() == "/startfinding":
                            if has_perm("startfinding"):
                                if self.is_finding_event:
                                    self.is_finding_event.set()
                                    _send("▶️ <b>Search started!</b>\nThe bot will now automatically search for tickets.", chat_id=chat_id)
                                    if self.force_poll_event:
                                        self.force_poll_event.set()
                        elif text.lower() == "/stopfinding":
                            if has_perm("stopfinding"):
                                if self.is_finding_event:
                                    self.is_finding_event.clear()
                                    _send("⏸️ <b>Search paused!</b>\nYou can restart it by sending /startfinding.", chat_id=chat_id)
                        elif text.lower() == "/settings":
                            if has_perm("settings"):
                                self._handle_settings_menu(chat_id)
                        elif text.lower() == "/users" and is_admin:
                            self._handle_users_menu(chat_id)
                        elif getattr(self, '_waiting_for_setting', None):
                            self._handle_setting_input(text, chat_id)
                        elif getattr(self, '_waiting_for_user_name', None):
                            self._handle_user_name_input(text, chat_id)
                            
                    elif "callback_query" in update:
                        cb = update["callback_query"]
                        data = cb.get("data", "")
                        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                        
                        admin_chat = str(TELEGRAM_CHAT_ID)
                        is_admin = (chat_id == admin_chat)
                        users = _get_users()
                        user_data = users.get(chat_id, {})

                        def has_perm(cmd_name: str) -> bool:
                            if is_admin: return True
                            if chat_id in users:
                                return user_data.get("perms", {}).get(cmd_name, False)
                            return False
                            
                        if data.startswith("check_seat:"):
                            if has_perm("dates"):
                                date_txt = data.split("check_seat:")[1]
                                self._handle_date_query(date_txt, chat_id)
                        elif data.startswith("edit_setting:"):
                            if has_perm("settings"):
                                setting_key = data.split("edit_setting:")[1]
                                self._waiting_for_setting = setting_key
                                _send(f"✏️ Send the new value for <b>{setting_key}</b>:", chat_id=chat_id)
                        elif data == "manage_users" and is_admin:
                            self._handle_users_menu(chat_id)
                        elif data.startswith("user_details:") and is_admin:
                            target_id = data.split(":")[1]
                            self._handle_user_details(chat_id, target_id)
                        elif data.startswith("toggle_perm:") and is_admin:
                            _, target_id, perm = data.split(":")
                            self._toggle_user_perm(chat_id, target_id, perm)
                        elif data.startswith("edit_user_name:") and is_admin:
                            target_id = data.split(":")[1]
                            self._waiting_for_user_name = target_id
                            _send(f"✏️ Send the new name for User <b>{target_id}</b>:", chat_id=chat_id)
                        elif data.startswith("remove_user:") and is_admin:
                            target_id = data.split(":")[1]
                            self._remove_user(chat_id, target_id)
                            
                        # Answer the callback query so the button stops spinning
                        cb_id = cb.get("id")
                        if cb_id:
                            cb_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                            requests.post(cb_url, json={"callback_query_id": cb_id}, timeout=5)

            except Exception as exc:
                log.warning("Telegram listener error: %s", exc)
                time.sleep(5)

    def _handle_setting_input(self, text: str, chat_id: str):
        key = self._waiting_for_setting
        self._waiting_for_setting = None
        try:
            val = int(text)
            from config.dynamic_settings import set_setting
            set_setting(key, val)
            _send(f"✅ <b>{key}</b> updated to {val}!", chat_id=chat_id)
            self._handle_settings_menu(chat_id)
        except ValueError:
            _send("❌ Invalid number. Edit cancelled.", chat_id=chat_id)
            
    def _handle_settings_menu(self, chat_id: str):
        from config.dynamic_settings import get_setting

        # Last poll time
        last_poll = self.bot_status.get("last_poll_time")
        if last_poll:
            last_poll_str = last_poll.strftime("%d.%m.%Y %H:%M:%S")
        else:
            last_poll_str = "Not checked yet"

        # Proxy status
        proxy_ok = self.bot_status.get("proxy_ok", True)
        proxy_icon = "🟢" if proxy_ok else "🔴"
        proxy_str = "OK" if proxy_ok else "ERROR"
        
        # Browser Restart
        b_start = self.bot_status.get("browser_start_time", time.time())
        b_interval = get_setting("BROWSER_RESTART_INTERVAL", 3600)
        next_restart = b_start + b_interval
        next_restart_str = datetime.fromtimestamp(next_restart).strftime("%d.%m.%Y %H:%M:%S")

        lines = [
            "⚙️ <b>Bot Settings</b>\n",
            f"• POLL_MIN_SECONDS: {get_setting('POLL_MIN_SECONDS', 60)}",
            f"• POLL_MAX_SECONDS: {get_setting('POLL_MAX_SECONDS', 120)}",
            f"• MAX_NEW_DATES_LISTED: {get_setting('MAX_NEW_DATES_LISTED', 5)}",
            f"• BROWSER_RESTART_INTERVAL: {get_setting('BROWSER_RESTART_INTERVAL', 3600)}s\n",
            f"🕐 <b>Last check:</b> {last_poll_str}",
            f"{proxy_icon} <b>Proxy/Session:</b> {proxy_str}",
            f"🔄 <b>Next browser restart:</b> {next_restart_str}\n",
            "Select a setting below to change it:"
        ]
        
        inline_keyboard = [
            [{"text": "⏱️ Edit Poll Min", "callback_data": "edit_setting:POLL_MIN_SECONDS"}],
            [{"text": "⏱️ Edit Poll Max", "callback_data": "edit_setting:POLL_MAX_SECONDS"}],
            [{"text": "📋 Edit Max Dates", "callback_data": "edit_setting:MAX_NEW_DATES_LISTED"}],
            [{"text": "🔄 Edit Restart Interval", "callback_data": "edit_setting:BROWSER_RESTART_INTERVAL"}],
            [{"text": "👥 Manage Users", "callback_data": "manage_users"}]
        ]
        
        _send("\n".join(lines), reply_markup={"inline_keyboard": inline_keyboard}, chat_id=chat_id)

    def _handle_date_query(self, date_txt: str, chat_id: str):
        """User sent DD-MM-YYYY → fetch traintrip for all routes and reply."""
        # Convert DD-MM-YYYY → YYYY-MM-DD
        parts = date_txt.split("-")
        if len(parts) != 3:
            return
        date_val = f"{parts[2]}-{parts[1]}-{parts[0]}"

        log.info("Telegram date query: %s", date_txt)
        _send(f"🔍 Checking seats for <b>{date_txt}</b>...", chat_id=chat_id)

        found_any = False
        for route in self.routes:
            label = route["label"]
            try:
                trip = self.api_client.get_traintrip(
                    route["from_station"], route["to_station"], date_val
                )
                if trip:
                    found_any = True
                    _send_traintrip_details(label, trip, chat_id)
            except Exception as exc:
                log.warning("Failed to fetch traintrip for %s on %s: %s", label, date_val, exc)
                _send(f"⚠️ Failed to check <b>{label}</b> for {date_txt}: {exc}", chat_id=chat_id)

            # Delay between routes to avoid ReCaptcha throttling
            time.sleep(3)

        if not found_any:
            _send(f"❌ No trains found for <b>{date_txt}</b> on any route.", chat_id=chat_id)

    def _handle_status_query(self, chat_id: str):
        """User sent /dates or /status → remind them how to use the bot."""
        _send(
            "ℹ️ <b>ADY Monitor</b>\n\n"
            "Send a date in <b>DD-MM-YYYY</b> format to check available seats.\n"
            f"Example: <code>{datetime.now().strftime('%d-%m-%Y')}</code>\n\n"
            "The monitor automatically notifies you when new travel dates appear.", chat_id=chat_id
        )

    def _handle_users_menu(self, chat_id: str):
        users = _get_users()
        if not users:
            _send("👥 <b>No users have been added yet.</b>\nThey can be added via /addchat.", chat_id=chat_id)
            return
            
        lines = ["👥 <b>Manage Users</b>\n", "Select a user to manage their permissions:"]
        inline_keyboard = []
        for cid, udata in users.items():
            name = udata.get("name", "Unknown User")
            inline_keyboard.append([{"text": f"👤 {name} ({cid})", "callback_data": f"user_details:{cid}"}])
            
        _send("\n".join(lines), reply_markup={"inline_keyboard": inline_keyboard}, chat_id=chat_id)

    def _handle_user_details(self, chat_id: str, target_id: str):
        users = _get_users()
        if target_id not in users:
            _send(f"❌ User <b>{target_id}</b> not found.", chat_id=chat_id)
            return
            
        udata = users[target_id]
        name = udata.get("name", "Unknown User")
        perms = udata.get("perms", {})
        notif = udata.get("notifications", False)
        
        lines = [
            f"👤 <b>User Details</b>",
            f"<b>Name:</b> {name}",
            f"<b>Chat ID:</b> {target_id}\n",
            "Select an option to toggle permission:"
        ]
        
        def btn(label, key, is_notif=False):
            state = notif if is_notif else perms.get(key, False)
            icon = "🟢 ON" if state else "🔴 OFF"
            return {"text": f"{label}: {icon}", "callback_data": f"toggle_perm:{target_id}:{key}"}

        inline_keyboard = [
            [{"text": "✏️ Edit Name", "callback_data": f"edit_user_name:{target_id}"}],
            [btn("🔔 Notifications", "notifications", is_notif=True)],
            [btn("▶️ startfinding", "startfinding"), btn("⏸️ stopfinding", "stopfinding")],
            [btn("🔄 check", "check"), btn("⚙️ settings", "settings")],
            [btn("📅 dates", "dates")],
            [{"text": "❌ Remove User", "callback_data": f"remove_user:{target_id}"}],
            [{"text": "🔙 Back to Users", "callback_data": "manage_users"}]
        ]
        
        _send("\n".join(lines), reply_markup={"inline_keyboard": inline_keyboard}, chat_id=chat_id)

    def _toggle_user_perm(self, chat_id: str, target_id: str, perm: str):
        from config.dynamic_settings import get_setting, set_setting
        allowed = get_setting("ALLOWED_CHAT_IDS", {})
        if target_id in allowed:
            if perm == "notifications":
                current = allowed[target_id].get("notifications", False)
                allowed[target_id]["notifications"] = not current
            else:
                if "perms" not in allowed[target_id]:
                    allowed[target_id]["perms"] = {}
                current = allowed[target_id]["perms"].get(perm, False)
                allowed[target_id]["perms"][perm] = not current
            set_setting("ALLOWED_CHAT_IDS", allowed)
            # Refresh UI
            self._handle_user_details(chat_id, target_id)

    def _remove_user(self, chat_id: str, target_id: str):
        from config.dynamic_settings import get_setting, set_setting
        allowed = get_setting("ALLOWED_CHAT_IDS", {})
        if target_id in allowed:
            del allowed[target_id]
            set_setting("ALLOWED_CHAT_IDS", allowed)
            _send(f"✅ User <b>{target_id}</b> has been removed.", chat_id=chat_id)
            self._handle_users_menu(chat_id)
            
    def _handle_user_name_input(self, text: str, chat_id: str):
        target_id = getattr(self, '_waiting_for_user_name', None)
        self._waiting_for_user_name = None
        if not target_id:
            return
            
        from config.dynamic_settings import get_setting, set_setting
        allowed = get_setting("ALLOWED_CHAT_IDS", {})
        if target_id in allowed:
            allowed[target_id]["name"] = text.strip()
            set_setting("ALLOWED_CHAT_IDS", allowed)
            _send(f"✅ Name for <b>{target_id}</b> updated to <b>{text.strip()}</b>!", chat_id=chat_id)
            self._handle_user_details(chat_id, target_id)
