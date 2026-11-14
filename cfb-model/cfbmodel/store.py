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

# ESPN conference ids that are FBS (ACC, Big 12, Big Ten, SEC, Pac-12, C-USA, MAC,
# Mountain West, FBS Independents, Sun Belt, American). Everything else is FCS.
FBS_CONFS = {"1", "4", "5", "8", "9", "12", "15", "17", "18", "37", "151"}

# passer row: team_id, athlete_id, name, then PASSER_FIELDS
PASSER_FIELDS = ["cmp", "att", "yds", "td", "int", "rush_att", "rush_yds"]


def _num(x) -> float:
    try:
        return float(str(x).replace("+", ""))
    except (TypeError, ValueError):
        return 0.0


def _pair(s, sep) -> tuple[float, float]:
    a, b = (str(s or "0").split(sep) + ["0"])[:2]
    return _num(a), _num(b)


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
        return {
            "spread_home": _line(_am(h, when, "pointSpread", "american")),
            "spread_odds_home": _line(_am(h, when, "spread", "american")),
            "spread_odds_away": _line(_am(a, when, "spread", "american")),
            "total": _line(_am(it, when, "total", "american")),
            "over_odds": _line(_am(it, when, "over", "american")),
            "under_odds": _line(_am(it, when, "under", "american")),
            "ml_home": _line(_am(h, when, "moneyLine", "american")),
            "ml_away": _line(_am(a, when, "moneyLine", "american")),
        }

    close = snap("close")
    if close["spread_home"] is None and it.get("spread") is not None:
        close["spread_home"] = float(it["spread"])
    if close["total"] is None and it.get("overUnder") is not None:
        close["total"] = float(it["overUnder"])
    return {"provider": it.get("provider", {}).get("name"), "open": snap("open"), "close": close}


def parse_summary(s: dict, day: str) -> dict | None:
    hdr = s.get("header", {})
    comp = (hdr.get("competitions") or [{}])[0]
    if not comp.get("status", {}).get("type", {}).get("completed"):
        return None
    teams = {}
    for c in comp.get("competitors", []):
        conf = str(c.get("team", {}).get("conferenceId") or "")
        teams[c["homeAway"]] = {"id": c["id"], "abbr": c["team"].get("abbreviation", c["id"]),
                                "score": int(_num(c.get("score"))), "conf": conf}
    if set(teams) != {"home", "away"}:
        return None
    box = s.get("boxscore", {})
    for bt in box.get("teams", []):
        side = "home" if bt["team"]["id"] == teams["home"]["id"] else "away"
        raw = {st["name"]: st.get("displayValue") for st in bt.get("statistics", [])}
        cmp_, att = _pair(raw.get("completionAttempts"), "/")
        b = {
            "yards": _num(raw.get("totalYards")), "pass_yds": _num(raw.get("netPassingYards")),
            "rush_yds": _num(raw.get("rushingYards")), "pass_att": att, "cmp": cmp_,
            "rush_att": _num(raw.get("rushingAttempts")), "to": _num(raw.get("turnovers")),
            "first_downs": _num(raw.get("firstDowns")),
        }
        b["plays"] = b["pass_att"] + b["rush_att"]
        teams[side]["box"] = b
    # drives per team (possessions)
    for d in (s.get("drives") or {}).get("previous", []) or []:
        tid = (d.get("team") or {}).get("id")
        for side in ("home", "away"):
            if teams[side]["id"] == tid and "box" in teams[side]:
                teams[side]["box"]["drives"] = teams[side]["box"].get("drives", 0) + 1
    passers = []
    for pt in box.get("players", []):
        tid = pt["team"]["id"]
        rush = {}
        for grp in pt.get("statistics", []):
            if grp.get("name") == "rushing":
                keys = grp.get("keys", [])
                for ath in grp.get("athletes", []):
                    r = dict(zip(keys, ath.get("stats") or []))
                    rush[ath["athlete"]["id"]] = (_num(r.get("rushingAttempts")), _num(r.get("rushingYards")))
        for grp in pt.get("statistics", []):
            if grp.get("name") != "passing":
                continue
            keys = grp.get("keys", [])
            for ath in grp.get("athletes", []):
                r = dict(zip(keys, ath.get("stats") or []))
                c, a = _pair(r.get("completions/passingAttempts"), "/")
                if a <= 0:
                    continue
                ra, ry = rush.get(ath["athlete"]["id"], (0.0, 0.0))
                passers.append([tid, ath["athlete"]["id"], ath["athlete"].get("displayName", ""),
                                c, a, _num(r.get("passingYards")), _num(r.get("passingTouchdowns")),
                                _num(r.get("interceptions")), ra, ry])
    season = hdr.get("season", {})
    return {
        "id": hdr.get("id"), "day": day, "date": comp.get("date"),
        "season": season.get("year"), "stype": season.get("type"), "week": hdr.get("week"),
        "neutral": bool(comp.get("neutralSite")), "conf_game": bool(comp.get("conferenceCompetition")),
        "home": teams["home"], "away": teams["away"], "passers": passers,
    }


def is_fbs(team: dict) -> bool:
    return team.get("conf") in FBS_CONFS


# ---------------------------------------------------------------- persistence
def season_of(day: str) -> int:
    """College football season (calendar year of the fall) for a YYYYMMDD day."""
    y, m = int(day[:4]), int(day[4:6])
    return y if m >= 7 else y - 1


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
        # no college football games in March-July
        if d.month >= 8 or d.month <= 2:
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
                    if e.get("season", {}).get("type") not in (2, 3):  # regular season + bowls only
                        continue
                    confs = {c["id"]: str(c.get("team", {}).get("conferenceId") or "")
                             for c in e["competitions"][0]["competitors"]}
                    todo.append((e["id"], day, confs))
                    have.add(e["id"])
    log(f"  {len(todo)} new completed games to fetch")

    def fetch(item):
        eid, day, confs = item
        try:
            g = parse_summary(espn.summary(eid), day)
            if g:
                for side in ("home", "away"):
                    g[side]["conf"] = confs.get(g[side]["id"], "") or g[side]["conf"]
                g["lines"] = parse_core_odds(espn.core_odds(eid))
            return g
        except Exception as e:  # keep going; it is retried next run
            log(f"  summary {eid}: {e!r}")
            return None

    new = []
    with ThreadPoolExecutor(workers) as ex:
        for i, g in enumerate(ex.map(fetch, todo), 1):
            if g:
                new.append(g)
            if i % 200 == 0:
                log(f"  fetched {i}/{len(todo)}")
                save_games(games + new)
    save_games(games + new)
    return len(new)
