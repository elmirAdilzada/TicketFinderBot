"""
ADY API client.

All POST calls use the exact same headers observed in the HAR capture.
The g_token is a Google reCAPTCHA v3 token – the site uses it but the
API does NOT enforce it server-side (confirmed: all HAR responses return
data even across sessions).  We send an empty string so the payload
shape remains identical to what the browser sends.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import BASE_URL, ENDPOINTS
from models.trip import TripDate, Trip, WagonClass, RouteSnapshot

log = logging.getLogger(__name__)


# ── Session factory ────────────────────────────────────────────────────────────

def _get_cdp_user_agent() -> str:
    """Fetch the browser's actual User-Agent via CDP to ensure a perfect match."""
    try:
        import requests
        resp = requests.get("http://localhost:9222/json/version", timeout=2)
        ua = resp.json().get("User-Agent")
        if ua:
            return ua
    except Exception:
        pass
    # Fallback to a standard desktop Chrome UA if CDP is unavailable
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )

def _build_session(cf_cookies: dict[str, str]) -> requests.Session:
    """
    Build a requests Session that mirrors what Chrome sends.
    cf_cookies: dict with at minimum {'cf_clearance': '...', '__ddg1_': '...'}
    obtained from the live browser (passed in by the browser module).
    """
    s = requests.Session()

    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    # Mirror the exact headers observed in HAR
    s.headers.update(
        {
            "User-Agent": _get_cdp_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
    )

    for name, value in cf_cookies.items():
        s.cookies.set(name, value, domain="ticket.ady.az")

    return s


# ── Low-level API calls ────────────────────────────────────────────────────────

class ADYApiClient:
    """
    Thin wrapper around the three endpoints we need.

    Lifecycle:
      1. Caller supplies Cloudflare cookies extracted from the live browser.
      2. Client polls get_trip_dates → discovers available dates.
      3. For each date, calls get_traintrip → discovers trains + seats.
      4. Caller decides whether to also call get_trip for the calendar view.
    """

    CLOUDFLARE_TITLES = {"Just a moment", "Attention Required", "Access denied"}

    def __init__(self, browser):
        """browser: BrowserManager instance (thread-safe evaluate queue)."""
        self._browser = browser

    def refresh_cookies(self, cf_cookies: dict[str, str]) -> None:
        pass # Playwright manages cookies natively, so we don't need this method anymore

    # ── private helpers ────────────────────────────────────────────────────────

    def _post(self, url: str, payload: dict, timeout: int = 20) -> Optional[dict]:
        """
        POST JSON to *url*.  Returns parsed JSON or None on failure.
        Raises CloudflareChallenge if CF blocks us.
        """
        try:
            resp = self._session.post(url, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            log.warning("Request error → %s: %s", url, exc)
            return None

        # Cloudflare challenge pages come back as HTML with status 403 / 503
        if resp.status_code in (403, 503):
            ct = resp.headers.get("content-type", "")
            if "text/html" in ct:
                raise CloudflareChallenge(
                    f"Cloudflare challenge detected (HTTP {resp.status_code})"
                )

        if resp.status_code != 200:
            log.warning("Non-200 from %s: %s", url, resp.status_code)
            return None

        try:
            data = resp.json()
        except ValueError:
            log.warning("Non-JSON body from %s", url)
            return None

        return data

    def _playwright_execute_fetch(self, url: str, payload: dict) -> Optional[dict]:
        """
        Execute the fetch request natively inside the Playwright browser page.
        Routes through the thread-safe BrowserManager queue.
        Waits for grecaptcha to be ready and always sends a valid token.
        """
        import json

        js_code = f'''
        async () => {{
            // Wait up to 10s for grecaptcha to become available
            const waitForRecaptcha = () => new Promise((resolve, reject) => {{
                let attempts = 0;
                const check = () => {{
                    if (typeof grecaptcha !== 'undefined' && grecaptcha.ready) {{
                        resolve();
                    }} else if (attempts++ > 50) {{
                        reject(new Error("grecaptcha not available after 10s"));
                    }} else {{
                        setTimeout(check, 200);
                    }}
                }};
                check();
            }});

            try {{
                await waitForRecaptcha();

                const token = await new Promise((resolve, reject) => {{
                    grecaptcha.ready(() => {{
                        grecaptcha.execute('6LecJSYtAAAAAMSGKGKhA72oiCfAWr8EoAUzEMgj', {{action: 'submit'}})
                            .then(resolve)
                            .catch(reject);
                    }});
                    setTimeout(() => reject(new Error("grecaptcha.execute timeout")), 10000);
                }});

                const p = {json.dumps(payload)};
                p.g_token = token;

                const r = await fetch('{url}', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }},
                    body: JSON.stringify(p)
                }});

                const j = await r.json();
                return JSON.stringify({{status: r.status, data: j}});

            }} catch(e) {{
                return JSON.stringify({{status: 500, error: e.toString()}});
            }}
        }}
        '''

        try:
            val = self._browser.evaluate(js_code, timeout=30.0)
            if val:
                parsed = json.loads(val)
                if parsed.get("status") in (403, 503):
                    raise CloudflareChallenge(f"Cloudflare challenge detected (HTTP {parsed.get('status')})")
                if parsed.get("status") == 200 and "data" in parsed:
                    return parsed["data"]
                log.warning("Playwright fetch returned non-200 or missing data: %s", parsed)
            return None
        except CloudflareChallenge:
            raise
        except Exception as exc:
            log.warning("Playwright fetch error: %s", exc)
            return None

    def get_trip_dates(
        self, from_station: int, to_station: int, way: int = 1
    ) -> list[TripDate]:
        """
        Call /ticket-api/get_trip_dates.
        """
        payload = {
            "from_station": from_station,
            "to_station": to_station,
            "way": way,
            "is_exclusive": 0,
            "g_token": "",
        }
        raw = self._playwright_execute_fetch(ENDPOINTS["get_trip_dates"], payload)
        if raw is None:
            raise RuntimeError("get_trip_dates CDP fetch failed or timed out")
        if raw.get("error"):
            log.debug("get_trip_dates returned error/no data: %s", raw)
            return []

        dates: list[TripDate] = []
        for _way_key, items in raw["data"].items():
            if not isinstance(items, list):
                continue
            for item in items:
                try:
                    dates.append(
                        TripDate(
                            trip_date_val=_txt_to_val(item["trip_date"]),
                            trip_date_txt=item["trip_date"],
                            min_amount=float(item.get("min_amount", 0)),
                            min_coefficient=float(item.get("min_cofficient", 1)),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    log.debug("Skipping malformed trip_date item %s: %s", item, exc)

        log.debug(
            "get_trip_dates(%s→%s) → %d dates", from_station, to_station, len(dates)
        )
        return dates

    def get_trip(
        self, from_station: int, to_station: int, trip_date: str
    ) -> list[TripDate]:
        """
        Call /ticket-api/get_trip.
        """
        payload = {
            "from_station": from_station,
            "to_station": to_station,
            "trip_date": trip_date,
            "is_exclusive": 0,
            "g_token": "",
        }
        raw = self._playwright_execute_fetch(ENDPOINTS["get_trip"], payload)
        if raw is None:
            raise RuntimeError("get_trip CDP fetch failed or timed out")
        if raw.get("error") or not isinstance(raw.get("data"), list):
            return []

        dates: list[TripDate] = []
        for item in raw["data"]:
            try:
                dates.append(
                    TripDate(
                        trip_date_val=item["trip_date_val"],
                        trip_date_txt=item["trip_date_txt"],
                        min_amount=float(item.get("min_amount", 0)),
                        min_coefficient=float(item.get("min_cofficient", 1)),
                    )
                )
            except (KeyError, ValueError):
                pass
        return dates

    def get_traintrip(
        self, from_station: int, to_station: int, trip_date: str
    ) -> Optional[Trip]:
        """
        Call /ticket-api/get_traintrip.
        """
        payload = {
            "from_station": from_station,
            "to_station": to_station,
            "trip_date": trip_date,
            "check": False,
            "is_exclusive": 0,
            "g_token": "",
        }
        raw = self._playwright_execute_fetch(ENDPOINTS["get_traintrip"], payload)

        if raw is None:
            raise RuntimeError("get_traintrip CDP fetch failed or timed out")

        if raw.get("error"):
            msg = raw.get("message", "") or raw.get("data", "")
            log.debug("get_traintrip(%s) → error: %s", trip_date, msg)
            return None

        data = raw.get("data", {})
        trip_raw = data.get("1") or (list(data.values())[0] if data else None)
        if not trip_raw:
            return None

        return _parse_traintrip(trip_raw)


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_traintrip(raw: dict) -> Trip:
    wagon_classes = _parse_wagon_classes(raw.get("wagon", {}))
    return Trip(
        trip_id=raw.get("trip_id", 0),
        train_number=raw.get("train_number", "?"),
        train_type=raw.get("train_type", ""),
        route_name=raw.get("route_name", ""),
        depart_datetime=raw.get("depart_datetime", ""),
        arrival_datetime=raw.get("arrival_datetime", ""),
        last_sale_time=raw.get("last_sale_time", ""),
        wagon_classes=wagon_classes,
    )


def _parse_wagon_classes(wagon_root: dict) -> list[WagonClass]:
    """
    wagon_root structure:
      { "1": { "5": { wagon_type, seat_class, tam_st_alt, wagons: [...] } } }
    """
    classes: list[WagonClass] = []
    for _direction, class_map in wagon_root.items():
        if not isinstance(class_map, dict):
            continue
        for _class_id, cls_raw in class_map.items():
            if not isinstance(cls_raw, dict):
                continue
            wagons = cls_raw.get("wagons", [])
            free_seats = sum(w.get("free_seats_count", 0) for w in wagons)
            wagon_ids = [w.get("wagon_id", 0) for w in wagons]
            try:
                wc = WagonClass(
                    wagon_type=cls_raw.get("wagon_type", ""),
                    seat_class=cls_raw.get("seat_class", ""),
                    seat_class_id=int(cls_raw.get("seat_class_id", 0)),
                    price_adult_lower=float(cls_raw.get("tam_st_alt") or 0),
                    price_adult_upper=float(cls_raw.get("tam_st_ust") or 0),
                    total_free_seats=free_seats,
                    wagon_ids=wagon_ids,
                )
                classes.append(wc)
            except (TypeError, ValueError) as exc:
                log.debug("Skipping wagon class: %s", exc)
    return classes


def _txt_to_val(txt: str) -> str:
    """Convert "26-06-2026" → "2026-06-26"."""
    try:
        d, m, y = txt.split("-")
        return f"{y}-{m}-{d}"
    except ValueError:
        return txt


# ── Exception ─────────────────────────────────────────────────────────────────

class CloudflareChallenge(Exception):
    """Raised when Cloudflare blocks the API request."""
