"""Thin client for ESPN's public college football API (no key needed)."""
from __future__ import annotations

import time

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
FBS_GROUP = 80  # scoreboard group: every game involving an FBS team

_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (cfb-model)"


def _get(url: str, params: dict | None = None, tries: int = 5, allow_404: bool = False) -> dict:
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404 and allow_404:
                return {}
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:  # network or bad JSON
            last = repr(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"ESPN request failed: {url} {params} ({last})")


def scoreboard(day: str) -> dict:
    """day = YYYYMMDD (US/Eastern game day). FBS games only (incl. FBS vs FCS)."""
    return _get(f"{BASE}/scoreboard", {"dates": day, "groups": FBS_GROUP, "limit": 400})


def summary(event_id: str) -> dict:
    return _get(f"{BASE}/summary", {"event": event_id})


def core_odds(event_id: str) -> dict:
    return _get(f"{CORE}/events/{event_id}/competitions/{event_id}/odds", allow_404=True)
