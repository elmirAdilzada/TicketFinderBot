"""
browser/keepalive.py – Human-like browser interaction to maintain Cloudflare session.

Uses pyautogui for physical mouse movement and keyboard events.
All interactions are randomized and never robotic.

RULES (per spec):
  - No headless mode
  - No DOM clicking / JS injection
  - Physical mouse movement only (with bezier curves + jitter)
  - Randomized timing
  - Never repetitive patterns
"""
from __future__ import annotations

import logging
import math
import random
import time
from typing import Callable

log = logging.getLogger(__name__)


# ── Lazy imports (pyautogui only available on Windows with display) ────────────

def _pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = True   # move to top-left corner to abort
        pyautogui.PAUSE = 0         # we control timing ourselves
        return pyautogui
    except ImportError:
        log.warning("pyautogui not installed – keepalive actions disabled.")
        return None


def _pywinauto():
    try:
        import pywinauto  # noqa: F401
        return pywinauto
    except ImportError:
        return None


# ── Bezier curve mouse movement ───────────────────────────────────────────────

def _bezier_point(p0, p1, p2, p3, t):
    """Cubic Bezier point at parameter t."""
    x = ((1-t)**3 * p0[0] + 3*(1-t)**2*t * p1[0]
         + 3*(1-t)*t**2 * p2[0] + t**3 * p3[0])
    y = ((1-t)**3 * p0[1] + 3*(1-t)**2*t * p1[1]
         + 3*(1-t)*t**2 * p2[1] + t**3 * p3[1])
    return int(x), int(y)


def _human_move(pag, x: int, y: int, duration: float = 0.8):
    """
    Move mouse to (x, y) along a randomized cubic Bezier curve.
    Includes micro-jitter and random speed variation.
    """
    sx, sy = pag.position()
    # Random control points to produce a natural curve
    cp1 = (sx + random.randint(-150, 150), sy + random.randint(-80, 80))
    cp2 = (x + random.randint(-150, 150), y + random.randint(-80, 80))

    steps = max(30, int(duration * 60))
    last = (sx, sy)
    for i in range(steps + 1):
        t = i / steps
        # Ease in-out: slow start, fast middle, slow end
        t_eased = t * t * (3 - 2 * t)
        px, py = _bezier_point((sx, sy), cp1, cp2, (x, y), t_eased)
        # Random micro-jitter
        jx = px + random.randint(-2, 2)
        jy = py + random.randint(-2, 2)
        if (jx, jy) != last:
            pag.moveTo(jx, jy)
            last = (jx, jy)
        # Variable sleep to simulate acceleration / deceleration
        speed = 0.5 + math.sin(math.pi * t) * 0.8   # faster in the middle
        time.sleep(duration / steps / max(speed, 0.1))


def _random_viewport_point(pag) -> tuple[int, int]:
    """Pick a random point inside the visible browser content area (rough estimate)."""
    sw, sh = pag.size()
    # Avoid browser chrome (tabs, address bar ~100px top, OS taskbar ~40px bottom)
    x = random.randint(50, sw - 50)
    y = random.randint(120, sh - 60)
    return x, y


# ── Individual keep-alive actions ─────────────────────────────────────────────

def _action_idle_mouse(pag):
    """Drift the mouse slightly as if the user is reading."""
    x, y = pag.position()
    dx = random.randint(-30, 30)
    dy = random.randint(-20, 20)
    nx = max(50, x + dx)
    ny = max(120, y + dy)
    _human_move(pag, nx, ny, duration=random.uniform(0.4, 1.2))
    log.debug("keepalive: idle mouse drift")


def _action_scroll(pag):
    """Scroll the page down a little, then back up."""
    x, y = _random_viewport_point(pag)
    _human_move(pag, x, y, duration=random.uniform(0.5, 1.0))
    scroll_amount = random.randint(2, 6)
    pag.scroll(scroll_amount)
    time.sleep(random.uniform(0.8, 2.5))
    pag.scroll(-scroll_amount + random.randint(-1, 1))
    log.debug("keepalive: scroll ±%d", scroll_amount)


def _action_move_to_random(pag):
    """Move to a random part of the browser window."""
    x, y = _random_viewport_point(pag)
    _human_move(pag, x, y, duration=random.uniform(0.6, 1.8))
    time.sleep(random.uniform(0.3, 1.2))
    log.debug("keepalive: random move → (%d, %d)", x, y)


def _action_refocus(pag):
    """
    Click on an empty area to keep the window focused.
    We pick a spot away from interactive elements (top-right corner of content).
    """
    sw, sh = pag.size()
    # Top-right of page content – generally safe empty space
    x = random.randint(int(sw * 0.65), int(sw * 0.85))
    y = random.randint(130, 200)
    _human_move(pag, x, y, duration=random.uniform(0.5, 1.2))
    time.sleep(random.uniform(0.2, 0.5))
    pag.click()
    log.debug("keepalive: refocus click at (%d, %d)", x, y)


def _action_hover_pause(pag):
    """Move to a point and linger as if reading something."""
    x, y = _random_viewport_point(pag)
    _human_move(pag, x, y, duration=random.uniform(0.8, 2.0))
    time.sleep(random.uniform(2.0, 5.0))  # reading pause
    log.debug("keepalive: hover pause at (%d, %d)", x, y)


# ── Keepalive orchestrator ────────────────────────────────────────────────────

# Weighted action list: (weight, action_fn)
_ACTIONS: list[tuple[int, Callable]] = [
    (40, _action_idle_mouse),
    (25, _action_scroll),
    (20, _action_move_to_random),
    (10, _action_refocus),
    (5,  _action_hover_pause),
]


def perform_keepalive_action() -> bool:
    """
    Perform one random human-like browser action.
    Returns True if action was performed, False if pyautogui not available.
    """
    pag = _pyautogui()
    if pag is None:
        log.debug("keepalive skipped (pyautogui unavailable)")
        return False

    # Weighted random choice
    weights = [w for w, _ in _ACTIONS]
    actions = [fn for _, fn in _ACTIONS]
    chosen = random.choices(actions, weights=weights, k=1)[0]

    # Random pre-action pause (feels more human)
    time.sleep(random.uniform(0.5, 2.0))

    try:
        chosen(pag)
    except Exception as exc:
        log.debug("keepalive action failed: %s", exc)
        return False

    return True


def focus_browser_window(title_fragment: str = "ticket.ady.az") -> bool:
    """
    Bring the browser window containing title_fragment to the foreground.
    Uses pywinauto on Windows.
    """
    pw = _pywinauto()
    if pw is None:
        return False
    try:
        app = pw.Application(backend="uia")
        # Find any window whose title contains the site name
        for win in pw.Desktop(backend="uia").windows():
            try:
                if title_fragment.lower() in win.window_text().lower():
                    win.set_focus()
                    time.sleep(0.5)
                    log.debug("Focused window: %s", win.window_text()[:60])
                    return True
            except Exception:
                continue
    except Exception as exc:
        log.debug("focus_browser_window error: %s", exc)
    return False
