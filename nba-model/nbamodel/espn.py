"""Thin client for ESPN's public site API (no key needed)."""
from __future__ import annotations

import time

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (nba-model)"


def get(path: str, params: dict | None = None, tries: int = 5) -> dict:
    url = f"{BASE}/{path}"
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:  # network or bad JSON
            last = repr(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"ESPN request failed: {url} {params} ({last})")


def scoreboard(day: str) -> dict:
    """day = YYYYMMDD (US/Eastern game day)."""
    return get("scoreboard", {"dates": day, "limit": 100})


def summary(event_id: str) -> dict:
    return get("summary", {"event": event_id})


def injuries() -> dict:
    return get("injuries")


CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def core_odds(event_id: str) -> dict:
    url = f"{CORE}/events/{event_id}/competitions/{event_id}/odds"
    last = None
    for i in range(5):
        try:
            r = _session.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return {}
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"ESPN core odds failed: {event_id} ({last})")
