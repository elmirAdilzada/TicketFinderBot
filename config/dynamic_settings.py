import json
import os
import logging
from config import settings

log = logging.getLogger(__name__)

DYNAMIC_SETTINGS_FILE = "dynamic_settings.json"

# These are the default values if not overwritten
_DEFAULTS = {
    "POLL_MIN_SECONDS": getattr(settings, "POLL_MIN_SECONDS", 60),
    "POLL_MAX_SECONDS": getattr(settings, "POLL_MAX_SECONDS", 120),
    "MAX_NEW_DATES_LISTED": getattr(settings, "MAX_NEW_DATES_LISTED", 5),
}

_cache = {}

def _load():
    if not os.path.exists(DYNAMIC_SETTINGS_FILE):
        return {}
    try:
        with open(DYNAMIC_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.error("Failed to load dynamic settings: %s", exc)
        return {}

def _save(data):
    try:
        with open(DYNAMIC_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        log.error("Failed to save dynamic settings: %s", exc)

def get_setting(key: str, default=None):
    """Get a setting, preferring dynamic overrides over defaults."""
    global _cache
    if not _cache:
        _cache = _load()
    
    if key in _cache:
        return _cache[key]
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    return default

def set_setting(key: str, value: int):
    """Save a setting dynamically."""
    global _cache
    _cache = _load()
    _cache[key] = value
    _save(_cache)
    log.info("Dynamic setting %s updated to %s", key, value)
