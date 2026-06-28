"""
monitor/state.py – Persist route date snapshots and detect changes between polls.

State is written to STATE_FILE as JSON after every poll so the monitor
can resume without false-positive notifications after a restart.

In this simplified version, state only tracks available dates (not seat details).
Seat details are fetched on-demand when the user queries a specific date.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from config.settings import STATE_FILE

log = logging.getLogger(__name__)


# ── Date Snapshot ─────────────────────────────────────────────────────────────

@dataclass
class DateSnapshot:
    """
    Snapshot of available dates for one route.
    dates: dict mapping date_val (YYYY-MM-DD) → {trip_date_txt, min_amount}
    """
    label: str
    from_station: int
    to_station: int
    dates: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "from_station": self.from_station,
            "to_station": self.to_station,
            "dates": self.dates,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DateSnapshot":
        return cls(
            label=d["label"],
            from_station=d["from_station"],
            to_station=d["to_station"],
            dates=d.get("dates", {}),
        )


# ── Persistence ───────────────────────────────────────────────────────────────

def load_state() -> dict[str, DateSnapshot]:
    """
    Load previously saved state from STATE_FILE.
    Returns dict keyed by route label → DateSnapshot.
    Returns {} if file missing or corrupt.
    """
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: DateSnapshot.from_dict(v) for k, v in raw.items()}
    except Exception as exc:
        log.warning("Could not load state file: %s – starting fresh.", exc)
        return {}


def save_state(state: dict[str, DateSnapshot]) -> None:
    """Atomically write state to STATE_FILE."""
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in state.items()}, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        log.error("Failed to save state: %s", exc)


# ── Diff logic ────────────────────────────────────────────────────────────────

@dataclass
class DateDiff:
    """Result of comparing old and new date snapshots for one route."""
    label: str
    new_dates: set[str]           # date_vals that appeared
    disappeared_dates: set[str]   # date_vals that disappeared
    all_dates: list[dict]         # current full date list for display
    has_changes: bool


def diff_dates(
    old: Optional[DateSnapshot],
    new: DateSnapshot,
) -> DateDiff:
    """
    Compare old and new DateSnapshot for one route.
    Returns a DateDiff describing what changed.
    """
    all_dates = [
        {"date_val": dv, **info}
        for dv, info in sorted(new.dates.items())
    ]

    if old is None:
        # First run – everything is new
        new_set = set(new.dates.keys())
        return DateDiff(
            label=new.label,
            new_dates=new_set,
            disappeared_dates=set(),
            all_dates=all_dates,
            has_changes=bool(new_set),
        )

    old_set = set(old.dates.keys())
    new_set = set(new.dates.keys())

    appeared = new_set - old_set
    disappeared = old_set - new_set

    return DateDiff(
        label=new.label,
        new_dates=appeared,
        disappeared_dates=disappeared,
        all_dates=all_dates,
        has_changes=bool(appeared or disappeared),
    )
