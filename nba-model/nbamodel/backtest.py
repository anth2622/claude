"""Walk-forward backtest: refit each day using only prior games, predict that day's
games, compare with opening and closing lines. Output feeds the calibration."""
from __future__ import annotations

import csv
import datetime as dt
import os

from . import model
from .store import DATA

BT_PATH = os.path.join(DATA, "backtest.csv")
BT_FIELDS = ["day", "id", "season", "home", "away", "home_score", "away_score",
             "pred_margin", "pred_total", "lineup_adj_home", "lineup_adj_away",
             "open_spread_home", "close_spread_home", "open_total", "close_total"]


def run(games: list[dict], start: dt.date, end: dt.date, params: dict | None = None,
        log=print, min_prior_games: int = 150, lineups: str = "inferred") -> list[dict]:
    """lineups: 'inferred' = only info known pre-game (recent DNPs; no injury report
    history exists), 'actual' = who really played (look-ahead; upper bound)."""
    days = sorted({g["day"] for g in games if start <= model.day_date(g["day"]) <= end})
    rows, box_coefs, coef_month = [], None, None
    for i, day in enumerate(days):
        d = model.day_date(day)
        prior = [g for g in games if model.day_date(g["day"]) < d]
        if len(prior) < min_prior_games:
            continue
        if coef_month != (d.year, d.month):
            box_coefs, coef_month = model.fit_box_coefs(prior), (d.year, d.month)
        st = model.fit_state(prior, d, params, box_coefs)
        for g in (g for g in games if g["day"] == day):
            h, a = g["home"]["id"], g["away"]["id"]
            if h not in st.off or a not in st.off:
                continue
            act_h = act_a = None
            if lineups == "actual":
                act_h = {p[1] for p in g["players"] if p[0] == h}
                act_a = {p[1] for p in g["players"] if p[0] == a}
            pr = model.predict(st, h, a, d, g.get("neutral", False), actual_home=act_h, actual_away=act_a)
            L = g.get("lines") or {}
            op, cl = L.get("open") or {}, L.get("close") or {}
            rows.append({
                "day": day, "id": g["id"], "season": g["season"],
                "home": g["home"]["abbr"], "away": g["away"]["abbr"],
                "home_score": g["home"]["score"], "away_score": g["away"]["score"],
                "pred_margin": round(pr["margin"], 2), "pred_total": round(pr["total"], 2),
                "lineup_adj_home": round(pr["lineup_adj_home"], 2), "lineup_adj_away": round(pr["lineup_adj_away"], 2),
                "open_spread_home": op.get("spread_home"), "close_spread_home": cl.get("spread_home"),
                "open_total": op.get("total"), "close_total": cl.get("total"),
            })
        if log and i % 30 == 0:
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
        for k in BT_FIELDS[5:]:
            r[k] = float(r[k]) if r[k] not in ("", "None") else None
    return rows
