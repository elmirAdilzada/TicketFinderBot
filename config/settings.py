"""
ADY Monitor – Configuration
Edit this file before running.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Proxy ─────────────────────────────────────────────────────────────────────
PROXY_SERVER   = os.getenv("PROXY_SERVER", "")    # e.g. "http://host:port"
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

# ── Routes to monitor ─────────────────────────────────────────────────────────
# Station IDs discovered from HAR: Baku RWS = 232, Tbilisi = 170
ROUTES = [
    {
        "label":       "Baku → Tbilisi",
        "from_station": 232,
        "to_station":   170,
        "way":          1,   # 1 = outbound
    },
    {
        "label":       "Tbilisi → Baku",
        "from_station": 170,
        "to_station":   232,
        "way":          1,
    },
]

# ── Polling ───────────────────────────────────────────────────────────────────
# Random interval between polls (seconds)
POLL_MIN_SECONDS = 1 * 60    # 1 minutes
POLL_MAX_SECONDS = 2 * 60    # 2 minutes

# When new dates appear, list them individually up to this limit.
# If more than this many new dates appear at once (e.g. 3 months of dates),
# send a compact summary instead of listing each date.
MAX_NEW_DATES_LISTED = 5

# ── API ───────────────────────────────────────────────────────────────────────
BASE_URL    = "https://ticket.ady.az"
API_BASE    = f"{BASE_URL}/ticket-api"
LANG_PREFIX = "/az"

ENDPOINTS = {
    "get_trip_dates": f"{API_BASE}/get_trip_dates",
    "get_trip":       f"{API_BASE}/get_trip",
    "get_traintrip":  f"{API_BASE}/get_traintrip",
    "stations":       f"{API_BASE}/stations_in_route",
}

# ── Browser keep-alive ────────────────────────────────────────────────────────
# How often (seconds) to perform a human-like browser action to keep Cloudflare
# session alive (independent of the poll interval).
KEEPALIVE_MIN_SECONDS = 3 * 60
KEEPALIVE_MAX_SECONDS = 8 * 60

# ── Persistence ───────────────────────────────────────────────────────────────
STATE_FILE = "monitor_state.json"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE  = "ady_monitor.log"
LOG_LEVEL = "INFO"
