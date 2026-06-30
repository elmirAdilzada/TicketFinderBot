"""
browser/keepalive.py – Virtual browser interaction to maintain Cloudflare session.

Uses Playwright's virtual mouse API to simulate human interactions (mouse movement,
scrolling, clicking). This works seamlessly in headless/server environments.
"""
from __future__ import annotations

import logging
import random
import time

log = logging.getLogger(__name__)

def perform_keepalive_action(page) -> bool:
    """
    Perform one random human-like browser action using Playwright.
    """
    if not page or page.is_closed():
        return False

    actions = [
        _action_idle_mouse,
        _action_scroll,
        _action_move_to_random,
        _action_refocus,
        _action_hover_pause,
    ]
    weights = [40, 25, 20, 10, 5]
    
    chosen = random.choices(actions, weights=weights, k=1)[0]
    
    time.sleep(random.uniform(0.5, 2.0))
    
    try:
        chosen(page)
        return True
    except Exception as exc:
        log.debug("keepalive action failed: %s", exc)
        return False


def _random_viewport_point(page) -> tuple[int, int]:
    """Pick a random point inside the viewport."""
    try:
        vp = page.viewport_size
        if vp:
            sw, sh = vp['width'], vp['height']
        else:
            sw, sh = 1920, 1080
    except Exception:
        sw, sh = 1920, 1080
        
    x = random.randint(50, max(60, sw - 50))
    y = random.randint(120, max(130, sh - 60))
    return x, y


def _human_move(page, x: int, y: int, steps: int = 10):
    """Move mouse to (x, y) over a few steps."""
    page.mouse.move(x, y, steps=steps)


def _action_idle_mouse(page):
    """Drift the mouse slightly."""
    x, y = _random_viewport_point(page)
    _human_move(page, x, y, steps=15)
    log.debug("keepalive: idle mouse drift")


def _action_scroll(page):
    """Scroll the page down a little, then back up."""
    x, y = _random_viewport_point(page)
    _human_move(page, x, y, steps=5)
    
    scroll_amount = random.randint(100, 400)
    page.mouse.wheel(delta_x=0, delta_y=scroll_amount)
    time.sleep(random.uniform(0.8, 2.5))
    page.mouse.wheel(delta_x=0, delta_y=-scroll_amount + random.randint(-50, 50))
    log.debug("keepalive: scroll ±%d", scroll_amount)


def _action_move_to_random(page):
    """Move to a random part of the browser window."""
    x, y = _random_viewport_point(page)
    _human_move(page, x, y, steps=20)
    time.sleep(random.uniform(0.3, 1.2))
    log.debug("keepalive: random move → (%d, %d)", x, y)


def _action_refocus(page):
    """Click on an empty area."""
    x, y = _random_viewport_point(page)
    _human_move(page, x, y, steps=10)
    time.sleep(random.uniform(0.2, 0.5))
    page.mouse.click(x, y)
    log.debug("keepalive: refocus click at (%d, %d)", x, y)


def _action_hover_pause(page):
    """Move to a point and linger."""
    x, y = _random_viewport_point(page)
    _human_move(page, x, y, steps=25)
    time.sleep(random.uniform(2.0, 5.0))  # reading pause
    log.debug("keepalive: hover pause at (%d, %d)", x, y)
