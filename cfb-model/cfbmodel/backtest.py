"""Walk-forward backtest: refit each game day using only prior games, predict that day's
games, compare with opening and closing lines. Output feeds the calibration."""
from __future__ import annotations

import csv
import datetime as dt
import os

from . import model
from .store import DATA

BT_PATH = os.path.join(DATA, "backtest.csv")
BT_FIELDS = ["day", "id", "season", "week", "stype", "home", "away", "home_score", "away_score",
             "pred_margin", "pred_total", "qb_adj_home", "qb_adj_away",
             "open_spread_home", "close_spread_home", "open_total", "close_total"]


def run(games: list[dict], start: dt.date, end: dt.date, params: dict | None = None,
        log=print, qb: str = "inferred", frame: model.Frame | None = None) -> list[dict]:
    """qb: 'inferred' = starter from the team's last game (known pre-game),
    'actual' = who really started (look-ahead; upper bound on QB-news value)."""
    fr = frame or model.Frame(games)
    days = sorted({g["day"] for g in fr.games if start <= model.day_date(g["day"]) <= end})
    rows = []
    for i, day in enumerate(days):
        d = model.day_date(day)
        st = model.fit_state(fr, d, params)
        for gi in fr.idx_by_ord[d.toordinal()]:
            g = fr.games[gi]
            h, a = g["home"]["id"], g["away"]["id"]
            known = qb == "actual"
            pr = model.predict(st, h, a, d, g.get("neutral", False),
                               model.starter_of(g, h) if known else None,
                               model.starter_of(g, a) if known else None)
            L = g.get("lines") or {}
            op, cl = L.get("open") or {}, L.get("close") or {}
            rows.append({
                "day": day, "id": g["id"], "season": g["season"], "week": g.get("week"), "stype": g.get("stype"),
                "home": g["home"]["abbr"], "away": g["away"]["abbr"],
                "home_score": g["home"]["score"], "away_score": g["away"]["score"],
                "pred_margin": round(pr["margin"], 2), "pred_total": round(pr["total"], 2),
                "qb_adj_home": round(pr["qb_adj_home"], 2), "qb_adj_away": round(pr["qb_adj_away"], 2),
                "open_spread_home": op.get("spread_home"), "close_spread_home": cl.get("spread_home"),
                "open_total": op.get("total"), "close_total": cl.get("total"),
            })
        if log and i % 20 == 0:
            log(f"  backtest {day}: {len(rows)} games")
    return rows


def save(rows: list[dict], path: str = BT_PATH) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, BT_FIELDS)
        w.writeheader()
        w.writerows(rows)


def load(path: str = BT_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in BT_FIELDS[7:]:
            r[k] = float(r[k]) if r[k] not in ("", "None") else None
    return rows
