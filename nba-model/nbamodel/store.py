"""Compact local game store (gzipped JSON lines, one file per season) + parsers."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor

from . import espn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
GAMES_DIR = os.path.join(DATA, "games")

BOX_KEYS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
    "offensiveRebounds": "oreb",
    "defensiveRebounds": "dreb",
    "assists": "ast",
    "steals": "stl",
    "blocks": "blk",
    "totalTurnovers": "tov",
    "fouls": "pf",
}
# player row: team_id, athlete_id, name, starter, then PLAYER_FIELDS
PLAYER_FIELDS = ["min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                 "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "pm"]


def _num(x) -> float:
    try:
        return float(str(x).replace("+", ""))
    except (TypeError, ValueError):
        return 0.0


def american_to_decimal(odds: float) -> float:
    return 1 + (odds / 100 if odds > 0 else 100 / -odds)


def _line(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().lstrip("ou")
    if s.upper() in ("PK", "EVEN"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def parse_pickcenter(pc: list, home_id: str) -> dict | None:
    """Closing (or current) lines from a summary's pickcenter, home perspective."""
    if not pc:
        return None
    pc = sorted(pc, key=lambda p: p.get("provider", {}).get("priority", 99))
    p = pc[0]
    h, a = p.get("homeTeamOdds", {}), p.get("awayTeamOdds", {})
    spread = p.get("spread")
    if spread is not None:
        spread = abs(float(spread))
        if h.get("favorite"):
            spread_home = -spread
        elif a.get("favorite"):
            spread_home = spread
        else:
            spread_home = float(p.get("spread"))
    else:
        spread_home = None
    return {
        "provider": p.get("provider", {}).get("name"),
        "spread_home": spread_home,
        "spread_odds_home": h.get("spreadOdds"),
        "spread_odds_away": a.get("spreadOdds"),
        "total": p.get("overUnder"),
        "over_odds": p.get("overOdds"),
        "under_odds": p.get("underOdds"),
        "ml_home": h.get("moneyLine"),
        "ml_away": a.get("moneyLine"),
    }


def _am(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def parse_core_odds(o: dict) -> dict | None:
    """Opening + closing lines (home perspective) from ESPN core odds."""
    items = [i for i in (o or {}).get("items", []) if "live" not in i.get("provider", {}).get("name", "").lower()]
    if not items:
        return None
    items.sort(key=lambda i: i.get("provider", {}).get("priority", 99))
    it = items[0]
    h, a = it.get("homeTeamOdds", {}), it.get("awayTeamOdds", {})

    def snap(when):
        sh = _line(_am(h, when, "pointSpread", "american"))
        tot = _line(_am(it, when, "total", "total", "american")) if when != "close" else None
        tot = _line(_am(it, when, "total", "american")) if tot is None else tot
        return {
            "spread_home": sh,
            "spread_odds_home": _line(_am(h, when, "spread", "american")),
            "spread_odds_away": _line(_am(a, when, "spread", "american")),
            "total": tot,
            "over_odds": _line(_am(it, when, "over", "american")),
            "under_odds": _line(_am(it, when, "under", "american")),
            "ml_home": _line(_am(h, when, "moneyLine", "american")),
            "ml_away": _line(_am(a, when, "moneyLine", "american")),
        }

    close = snap("close")
    if close["spread_home"] is None and it.get("spread") is not None:
        close["spread_home"] = float(it["spread"])
    if close["total"] is None:
        close["total"] = it.get("overUnder")
    return {"provider": it.get("provider", {}).get("name"), "open": snap("open"), "close": close}


def parse_summary(s: dict, day: str) -> dict | None:
    hdr = s.get("header", {})
    comp = (hdr.get("competitions") or [{}])[0]
    if not comp.get("status", {}).get("type", {}).get("completed"):
        return None
    teams = {}
    for c in comp.get("competitors", []):
        teams[c["homeAway"]] = {"id": c["id"], "abbr": c["team"]["abbreviation"],
                                "score": int(_num(c.get("score")))}
    if set(teams) != {"home", "away"}:
        return None
    box = s.get("boxscore", {})
    for bt in box.get("teams", []):
        side = "home" if bt["team"]["id"] == teams["home"]["id"] else "away"
        b = {}
        for st in bt.get("statistics", []):
            k = BOX_KEYS.get(st["name"])
            if k is None:
                continue
            if isinstance(k, tuple):
                m, a = (st["displayValue"].split("-") + ["0"])[:2]
                b[k[0]], b[k[1]] = _num(m), _num(a)
            else:
                b[k] = _num(st["displayValue"])
        teams[side]["box"] = b
    players = []
    for pt in box.get("players", []):
        tid = pt["team"]["id"]
        for grp in pt.get("statistics", []):
            keys = grp.get("keys", [])
            for ath in grp.get("athletes", []):
                st = ath.get("stats") or []
                if not st or ath.get("didNotPlay"):
                    continue
                raw = dict(zip(keys, st))
                row = {
                    "min": _num(raw.get("minutes")), "pts": _num(raw.get("points")),
                    "oreb": _num(raw.get("offensiveRebounds")), "dreb": _num(raw.get("defensiveRebounds")),
                    "ast": _num(raw.get("assists")), "stl": _num(raw.get("steals")),
                    "blk": _num(raw.get("blocks")), "tov": _num(raw.get("turnovers")),
                    "pf": _num(raw.get("fouls")), "pm": _num(raw.get("plusMinus")),
                }
                for key, (m, a) in (("fieldGoalsMade-fieldGoalsAttempted", ("fgm", "fga")),
                                    ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", ("fg3m", "fg3a")),
                                    ("freeThrowsMade-freeThrowsAttempted", ("ftm", "fta"))):
                    mm, aa = (str(raw.get(key, "0-0")).split("-") + ["0"])[:2]
                    row[m], row[a] = _num(mm), _num(aa)
                if row["min"] <= 0:
                    continue
                players.append([tid, ath["athlete"]["id"], ath["athlete"]["displayName"],
                                bool(ath.get("starter"))] + [row[f] for f in PLAYER_FIELDS])
    season = hdr.get("season", {})
    return {
        "id": hdr.get("id"), "day": day, "date": comp.get("date"),
        "season": season.get("year"), "stype": season.get("type"),
        "neutral": bool(comp.get("neutralSite")),
        "home": teams["home"], "away": teams["away"],
        "players": players,
    }


# ---------------------------------------------------------------- persistence
def season_of(day: str) -> int:
    """ESPN season year (2026 = 2025-26) for a YYYYMMDD day."""
    y, m = int(day[:4]), int(day[4:6])
    return y + 1 if m >= 8 else y


def _path(season: int) -> str:
    return os.path.join(GAMES_DIR, f"{season}.jsonl.gz")


def load_games(seasons=None) -> list[dict]:
    out = []
    if not os.path.isdir(GAMES_DIR):
        return out
    for fn in sorted(os.listdir(GAMES_DIR)):
        if not fn.endswith(".jsonl.gz"):
            continue
        if seasons and int(fn.split(".")[0]) not in seasons:
            continue
        with gzip.open(os.path.join(GAMES_DIR, fn), "rt") as f:
            out.extend(json.loads(line) for line in f if line.strip())
    out.sort(key=lambda g: (g["date"] or "", g["id"]))
    return out


def save_games(games: list[dict]) -> None:
    os.makedirs(GAMES_DIR, exist_ok=True)
    by = {}
    for g in games:
        by.setdefault(season_of(g["day"]), []).append(g)
    for season, gs in by.items():
        gs.sort(key=lambda g: (g["date"] or "", g["id"]))
        tmp = _path(season) + ".tmp"
        with gzip.open(tmp, "wt") as f:
            for g in gs:
                f.write(json.dumps(g, separators=(",", ":")) + "\n")
        os.replace(tmp, _path(season))


def days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def update(start: dt.date, end: dt.date, workers: int = 8, log=print) -> int:
    """Fetch every completed game between start and end (inclusive) not already stored."""
    games = load_games()
    have = {g["id"] for g in games}
    todo = []

    def sb(day):
        try:
            return day, espn.scoreboard(day).get("events", [])
        except RuntimeError as e:
            log(f"  scoreboard {day}: {e}")
            return day, []

    with ThreadPoolExecutor(workers) as ex:
        for day, events in ex.map(sb, list(days(start, end))):
            for e in events:
                st = e["competitions"][0]["status"]["type"]
                if st.get("completed") and e["id"] not in have:
                    if e.get("season", {}).get("type") in (1,):  # skip preseason
                        continue
                    todo.append((e["id"], day))
    log(f"  {len(todo)} new completed games to fetch")

    def fetch(item):
        eid, day = item
        try:
            g = parse_summary(espn.summary(eid), day)
            if g:
                g["lines"] = parse_core_odds(espn.core_odds(eid))
            return g
        except Exception as e:  # keep going; it is retried next run
            log(f"  summary {eid}: {e!r}")
            return None

    new = []
    with ThreadPoolExecutor(workers) as ex:
        for i, g in enumerate(ex.map(fetch, todo), 1):
            if g and g.get("stype") != 1:
                new.append(g)
            if i % 200 == 0:
                log(f"  fetched {i}/{len(todo)}")
                save_games(games + new)
    save_games(games + new)
    return len(new)
