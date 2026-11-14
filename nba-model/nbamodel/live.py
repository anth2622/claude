"""Daily pipeline: update data -> grade -> recalibrate -> predict today -> picks -> report."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
from zoneinfo import ZoneInfo

import numpy as np

from . import backtest, calibrate, espn, model, store
from .store import DATA, ROOT, _line

ET = ZoneInfo("America/New_York")
PRED_PATH = os.path.join(DATA, "predictions.csv")
PICKS_PATH = os.path.join(DATA, "picks.csv")
REPORT_PATH = os.path.join(ROOT, "REPORT.md")
REPORTS_DIR = os.path.join(ROOT, "reports")

PRED_FIELDS = ["day", "id", "tip", "home", "away", "home_id", "away_id",
               "spread_home", "spread_odds_home", "spread_odds_away", "total", "over_odds", "under_odds",
               "open_spread_home", "open_total", "pred_margin", "pred_total", "lineup_adj_home",
               "lineup_adj_away", "p_home_cover", "p_over", "home_score", "away_score",
               "close_spread_home", "close_total", "status"]
PICK_FIELDS = ["day", "id", "matchup", "market", "side", "line", "odds", "prob", "edge_pts", "ev",
               "stake", "result", "profit", "close_line", "clv_pts", "note"]

# Decision policy. The daily review may adjust these (and must log why in CHANGELOG.md).
DEFAULT_POLICY = {
    "min_prob_edge": 0.025,     # model prob must exceed break-even by this much
    "min_edge_pts_spread": 2.0,  # and disagree with the line by at least this many points
    "min_edge_pts_total": 3.0,
    "max_picks_per_day": 4,
    "markets": ["spread", "total"],
    "live_weight": 3.0,          # weight of a live (morning-line) result vs a backtest row
    "stake_units": 1.0,
}


# ------------------------------------------------------------------ csv helpers
def _read(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def _write(path, fields, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def today_et() -> dt.date:
    return dt.datetime.now(ET).date()


# ------------------------------------------------------------------ live lines
def parse_scoreboard_odds(comp: dict) -> dict:
    odds = sorted(comp.get("odds") or [], key=lambda o: o.get("provider", {}).get("priority", 99))
    if not odds:
        return {}
    o = odds[0]
    ps, tot = o.get("pointSpread", {}), o.get("total", {})
    cur = {
        "provider": o.get("provider", {}).get("name"),
        "spread_home": _line(ps.get("home", {}).get("close", {}).get("line")),
        "spread_odds_home": _line(ps.get("home", {}).get("close", {}).get("odds")),
        "spread_odds_away": _line(ps.get("away", {}).get("close", {}).get("odds")),
        "total": _line(tot.get("over", {}).get("close", {}).get("line")),
        "over_odds": _line(tot.get("over", {}).get("close", {}).get("odds")),
        "under_odds": _line(tot.get("under", {}).get("close", {}).get("odds")),
        "open_spread_home": _line(ps.get("home", {}).get("open", {}).get("line")),
        "open_total": _line(tot.get("over", {}).get("open", {}).get("line")),
    }
    if cur["spread_home"] is None and o.get("spread") is not None:
        fav_home = o.get("homeTeamOdds", {}).get("favorite")
        cur["spread_home"] = -abs(o["spread"]) if fav_home else abs(o["spread"])
    if cur["total"] is None:
        cur["total"] = o.get("overUnder")
    return cur


def injury_statuses() -> tuple[dict, list]:
    """athlete_id -> status ('out', 'day-to-day', ...), plus raw rows for the report."""
    out, raw = {}, []
    for t in espn.injuries().get("injuries", []):
        for i in t.get("injuries", []):
            ath = i.get("athlete", {})
            aid = None
            for l in ath.get("links", []):
                m = re.search(r"/id/(\d+)", l.get("href", ""))
                if m:
                    aid = m.group(1)
                    break
            if not aid:
                continue
            status = (i.get("status") or "").lower()
            fant = (i.get("details", {}).get("fantasyStatus", {}) or {}).get("abbreviation", "")
            if fant == "OUT":
                status = "out"
            out[aid] = status
            raw.append({"team": t.get("displayName"), "player": ath.get("displayName"), "status": status,
                        "comment": i.get("shortComment", "")})
    return out, raw


# ------------------------------------------------------------------ grading
def grade(log=print) -> None:
    """Fill results for predictions/picks whose games are now final."""
    games = {g["id"]: g for g in store.load_games()}
    preds = _read(PRED_PATH)
    for p in preds:
        g = games.get(p["id"])
        if p.get("status") == "final" or not g:
            continue
        p["home_score"], p["away_score"] = g["home"]["score"], g["away"]["score"]
        cl = (g.get("lines") or {}).get("close") or {}
        p["close_spread_home"], p["close_total"] = cl.get("spread_home"), cl.get("total")
        p["status"] = "final"
    _write(PRED_PATH, PRED_FIELDS, preds)

    picks = _read(PICKS_PATH)
    for k in picks:
        if k["result"] not in ("", "pending"):
            continue
        g = games.get(k["id"])
        if not g:
            continue
        hs, as_ = g["home"]["score"], g["away"]["score"]
        line, odds, stake = float(k["line"]), float(k["odds"]), float(k["stake"])
        cl = (g.get("lines") or {}).get("close") or {}
        if k["market"] == "spread":
            margin = (hs - as_) if k["side"] == "home" else (as_ - hs)
            v = margin + line
            close = cl.get("spread_home")
            if close is not None and k["side"] == "away":
                close = -close
            clv = None if close is None else line - close  # got more points than close
        else:
            tot = hs + as_
            v = (tot - line) if k["side"] == "over" else (line - tot)
            close = cl.get("total")
            clv = None if close is None else ((close - line) if k["side"] == "over" else (line - close))
        res = "P" if v == 0 else ("W" if v > 0 else "L")
        win = stake * (odds / 100 if odds > 0 else 100 / -odds)
        k["result"] = res
        k["profit"] = round(win if res == "W" else (-stake if res == "L" else 0.0), 3)
        k["close_line"] = "" if close is None else close
        k["clv_pts"] = "" if clv is None else round(clv, 1)
        log(f"  graded {k['day']} {k['matchup']} {k['market']} {k['side']} {k['line']}: {res}")
    _write(PICKS_PATH, PICK_FIELDS, picks)


# ------------------------------------------------------------------ calibration (feedback loop)
def calibration_rows() -> list[dict]:
    rows = backtest.load()
    pol = {**DEFAULT_POLICY, **calibrate.load_state().get("policy", {})}
    # Live morning-line results: the most relevant evidence, weighted up.
    for p in _read(PRED_PATH):
        if p.get("status") != "final":
            continue
        rows.append({
            "home_score": float(p["home_score"]), "away_score": float(p["away_score"]),
            "pred_margin": float(p["pred_margin"]), "pred_total": float(p["pred_total"]),
            "open_spread_home": _f(p["spread_home"]), "open_total": _f(p["total"]),
            "close_spread_home": _f(p["close_spread_home"]), "close_total": _f(p["close_total"]),
            "weight": pol["live_weight"],
        })
    return rows


def recalibrate(log=print) -> dict:
    state = calibrate.load_state()
    cal = calibrate.fit(calibration_rows(), "open")
    state["calibration"] = cal
    state["calibrated_at"] = dt.datetime.now(ET).isoformat(timespec="minutes")
    state.setdefault("policy", {})
    calibrate.save_state(state)
    log(f"  calibration: spread a={cal['spread']['a']:.4f} b={cal['spread']['b']:+.3f} (n={cal['spread']['n']}) | "
        f"total a={cal['total']['a']:.4f} b={cal['total']['b']:+.3f} (n={cal['total']['n']})")
    return state


def refresh_backtest(games, log=print) -> None:
    """Extend the walk-forward backtest with any newly completed days."""
    rows = backtest.load()
    have = {r["id"] for r in rows}
    last = max((model.day_date(r["day"]) for r in rows), default=dt.date(2024, 11, 20))
    params = calibrate.load_state().get("params")
    new = [r for r in backtest.run(games, last, today_et() - dt.timedelta(days=1), params, log=None)
           if r["id"] not in have]
    if new:
        backtest.save(rows + new)
    log(f"  backtest extended by {len(new)} games")


# ------------------------------------------------------------------ predict + pick
def predict_day(day: dt.date, games: list[dict], log=print) -> tuple[list[dict], list[dict], list]:
    state = calibrate.load_state()
    pol = {**DEFAULT_POLICY, **state.get("policy", {})}
    cal = state["calibration"]
    st = model.fit_state(games, day, state.get("params"))
    statuses, inj_raw = injury_statuses()
    sb = espn.scoreboard(day.strftime("%Y%m%d"))
    preds, cands = [], []
    for e in sb.get("events", []):
        comp = e["competitions"][0]
        if comp["status"]["type"]["state"] != "pre":
            continue
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        h, a = teams["home"]["id"], teams["away"]["id"]
        if h not in st.off or a not in st.off:
            continue
        ln = parse_scoreboard_odds(comp)
        pr = model.predict(st, h, a, day, bool(comp.get("neutralSite")), statuses=statuses)
        ha, aa = teams["home"]["team"]["abbreviation"], teams["away"]["team"]["abbreviation"]
        row = {"day": day.strftime("%Y%m%d"), "id": e["id"], "tip": comp.get("date"), "home": ha, "away": aa,
               "home_id": h, "away_id": a, **{k: ln.get(k) for k in PRED_FIELDS if k in ln},
               "pred_margin": round(pr["margin"], 2), "pred_total": round(pr["total"], 2),
               "lineup_adj_home": round(pr["lineup_adj_home"], 2), "lineup_adj_away": round(pr["lineup_adj_away"], 2),
               "status": "pending", "_pr": pr}
        matchup = f"{aa} @ {ha}"
        if ln.get("spread_home") is not None:
            e_s = pr["margin"] + ln["spread_home"]
            p_home = calibrate.prob(cal, "spread", e_s)
            row["p_home_cover"] = round(p_home, 4)
            for side, p, line, odds, edge in (("home", p_home, ln["spread_home"], ln.get("spread_odds_home"), e_s),
                                              ("away", 1 - p_home, -ln["spread_home"], ln.get("spread_odds_away"), -e_s)):
                cands.append(_cand(row, matchup, "spread", side, line, odds, p, edge, pol))
        if ln.get("total") is not None:
            e_t = pr["total"] - ln["total"]
            p_over = calibrate.prob(cal, "total", e_t)
            row["p_over"] = round(p_over, 4)
            for side, p, odds, edge in (("over", p_over, ln.get("over_odds"), e_t),
                                        ("under", 1 - p_over, ln.get("under_odds"), -e_t)):
                cands.append(_cand(row, matchup, "total", side, ln["total"], odds, p, edge, pol))
        preds.append(row)
    picks = [c for c in cands if c["_ok"]]
    picks.sort(key=lambda c: -c["ev"])
    return preds, picks[: int(pol["max_picks_per_day"])], inj_raw


def _cand(row, matchup, market, side, line, odds, p, edge, pol):
    odds = -110.0 if odds is None else float(odds)
    be = calibrate.breakeven(odds)
    dec = store.american_to_decimal(odds)
    ev = p * (dec - 1) - (1 - p)
    min_pts = pol["min_edge_pts_spread"] if market == "spread" else pol["min_edge_pts_total"]
    ok = market in pol["markets"] and p - be >= pol["min_prob_edge"] and edge >= min_pts
    return {"day": row["day"], "id": row["id"], "matchup": matchup, "market": market, "side": side,
            "line": line, "odds": int(odds), "prob": round(p, 4), "edge_pts": round(edge, 1),
            "ev": round(ev, 4), "stake": pol["stake_units"], "result": "pending", "profit": "",
            "close_line": "", "clv_pts": "", "note": "", "_ok": ok, "_row": row}


def pick_label(k: dict) -> str:
    home, away = k["matchup"].split(" @ ")[1], k["matchup"].split(" @ ")[0]
    if k["market"] == "spread":
        team = home if k["side"] == "home" else away
        ln = float(k["line"])
        return f"{team} {'+' if ln > 0 else ''}{ln:g}" if ln != 0 else f"{team} PK"
    return f"{k['side'].upper()} {float(k['line']):g}"


# ------------------------------------------------------------------ reporting
def tally(picks: list[dict]) -> dict:
    done = [k for k in picks if k["result"] in ("W", "L", "P")]

    def agg(rows):
        w = sum(k["result"] == "W" for k in rows); l = sum(k["result"] == "L" for k in rows)
        p = sum(k["result"] == "P" for k in rows)
        prof = sum(float(k["profit"] or 0) for k in rows); risk = sum(float(k["stake"]) for k in rows if k["result"] != "P")
        clv = [float(k["clv_pts"]) for k in rows if k["clv_pts"] not in ("", None)]
        return {"w": w, "l": l, "p": p, "pct": w / (w + l) if w + l else None, "units": prof,
                "roi": prof / risk if risk else None, "clv": float(np.mean(clv)) if clv else None,
                "beat_close": float(np.mean([c > 0 for c in clv])) if clv else None, "n_clv": len(clv)}

    out = {"all": agg(done)}
    for m in ("spread", "total"):
        out[m] = agg([k for k in done if k["market"] == m])
    out["last30"] = agg(done[-30:])
    out["pending"] = sum(k["result"] in ("", "pending") for k in picks)
    return out


def _fmt(a: dict) -> str:
    pct = "—" if a["pct"] is None else f"{100 * a['pct']:.1f}%"
    roi = "—" if a["roi"] is None else f"{100 * a['roi']:+.1f}%"
    clv = "—" if a["clv"] is None else f"{a['clv']:+.2f} pts ({100 * a['beat_close']:.0f}% beat close)"
    return f"| {a['w']}-{a['l']}-{a['p']} | {pct} | {a['units']:+.2f}u | {roi} | {clv} |"


def write_reports(day: dt.date, preds: list[dict], picks_today: list[dict], inj_raw: list, state: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    allpicks = _read(PICKS_PATH)
    t = tally(allpicks)
    cal = state["calibration"]
    lines = [f"# NBA picks — {day:%A, %B %-d, %Y}", ""]
    if picks_today:
        lines += ["| Pick | Game | Odds | Model prob | Edge | EV/unit |", "|---|---|---|---|---|---|"]
        for k in picks_today:
            lines.append(f"| **{pick_label(k)}** | {k['matchup']} | {k['odds']:+d} | {100 * k['prob']:.1f}% | "
                         f"{k['edge_pts']:+.1f} pts | {k['ev']:+.3f} |")
    else:
        lines.append("**No bets today.** Nothing cleared the edge threshold — passing is a position.")
    lines += ["", "## Every game — model percentages", ""]
    for r in preds:
        h, a = r["home"], r["away"]
        parts = [f"**{a} @ {h}**"]
        sh = r.get("spread_home")
        if sh is not None and r.get("p_home_cover") is not None:
            ph = r["p_home_cover"]
            fav_home = ph >= 0.5
            parts.append("Spread: %s %+g **%.1f%%** / %s %+g %.1f%%" % (
                (h, sh, 100 * ph, a, -sh, 100 * (1 - ph)) if fav_home else (a, -sh, 100 * (1 - ph), h, sh, 100 * ph)))
        if r.get("total") is not None and r.get("p_over") is not None:
            po = r["p_over"]
            parts.append("Total %g: %s" % (r["total"], ("**Over %.1f%%** / Under %.1f%%" % (100 * po, 100 * (1 - po)))
                                           if po >= 0.5 else ("**Under %.1f%%** / Over %.1f%%" % (100 * (1 - po), 100 * po))))
        win = "%s by %.1f" % ((h, r["pred_margin"]) if r["pred_margin"] >= 0 else (a, -r["pred_margin"]))
        parts.append(f"Model: {win}, total {r['pred_total']:.1f}")
        lines.append("- " + " · ".join(parts))
    lines += ["", "Break-even at -110 is 52.4%; a bet needs about 54.9%+ and 2+ pts of edge (3+ on totals).",
              "", "## All games (detail)", "",
              "| Game | Line | Model spread | Total | Model total | Lineup adj (H/A) | P(home cover) | P(over) |",
              "|---|---|---|---|---|---|---|---|"]
    for r in preds:
        sh = r.get("spread_home")
        line_s = "" if sh is None else "%s %+g" % (r["home"], sh)
        phc = "" if r.get("p_home_cover") is None else "%.1f%%" % (100 * r["p_home_cover"])
        pov = "" if r.get("p_over") is None else "%.1f%%" % (100 * r["p_over"])
        lines.append(f"| {r['away']} @ {r['home']} | {line_s} | "
                     f"{r['home']} {-r['pred_margin']:+.1f} | {r.get('total') or ''} | {r['pred_total']:.1f} | "
                     f"{r['lineup_adj_home']:+.1f} / {r['lineup_adj_away']:+.1f} | {phc} | {pov} |")
    absn = [(r, side, n) for r in preds for side in ("home", "away") for n in r["_pr"]["absences"][side]]
    if absn:
        lines += ["", "## Key absences priced in", ""]
        for r, side, n in absn:
            lines.append(f"- {r[side]}: {n['player']} ({n['mpg']} mpg, value {n['value']:+.1f}/100) — {n['status']}, "
                         f"play prob {n['play_prob']:.0%}")
    lines += ["", "## Running record", "", "| Split | W-L-P | Hit % | Units | ROI | CLV |", "|---|---|---|---|---|---|"]
    for name in ("all", "spread", "total", "last30"):
        lines.append(f"| {name} " + _fmt(t[name]))
    lines += ["", f"Break-even at -110 is 52.4%. Target: 55%+ over a large sample. Pending: {t['pending']}.",
              f"Calibration (refit daily): spread slope {cal['spread']['a']:.4f}/pt, total slope {cal['total']['a']:.4f}/pt."]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(REPORTS_DIR, f"{day:%Y-%m-%d}.md"), "w") as f:
        f.write(text)
    _write_master(allpicks, t)
    return text


def _write_master(allpicks, t):
    L = ["# NBA model — running record", "",
         "| Split | W-L-P | Hit % | Units | ROI | CLV |", "|---|---|---|---|---|---|"]
    for name in ("all", "spread", "total", "last30"):
        L.append(f"| {name} " + _fmt(t[name]))
    L += ["", "CLV = points of closing-line value (positive means we beat the close). It is the fastest honest "
          "signal of skill: a 55% hit rate takes hundreds of bets to confirm, CLV shows up in dozens.", "",
          "## Pick history", "", "| Date | Game | Pick | Odds | Prob | Result | Units | Close | CLV |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k in reversed(allpicks):
        L.append(f"| {k['day'][:4]}-{k['day'][4:6]}-{k['day'][6:]} | {k['matchup']} | {pick_label(k)} | "
                 f"{int(float(k['odds'])):+d} | {100 * float(k['prob']):.1f}% | {k['result']} | {k['profit']} | "
                 f"{k['close_line']} | {k['clv_pts']} |")
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(L) + "\n")


# ------------------------------------------------------------------ entrypoint
def daily(day: dt.date | None = None, log=print) -> str:
    day = day or today_et()
    games = store.load_games()
    last = max((model.day_date(g["day"]) for g in games), default=dt.date(2024, 10, 1))
    log(f"[1/5] updating results {last} .. {day - dt.timedelta(days=1)}")
    store.update(last - dt.timedelta(days=2), day - dt.timedelta(days=1), log=log)
    games = store.load_games()
    log("[2/5] grading")
    grade(log)
    log("[3/5] extending backtest + recalibrating")
    refresh_backtest(games, log)
    state = recalibrate(log)
    log(f"[4/5] predicting {day}")
    preds, picks, inj_raw = predict_day(day, games, log)
    # persist predictions (replace any earlier run for the same day) and picks
    old = [p for p in _read(PRED_PATH) if p["day"] != day.strftime("%Y%m%d")]
    _write(PRED_PATH, PRED_FIELDS, old + preds)
    allpicks = [k for k in _read(PICKS_PATH) if not (k["day"] == day.strftime("%Y%m%d") and k["result"] == "pending")]
    _write(PICKS_PATH, PICK_FIELDS, allpicks + picks)
    log("[5/5] reports")
    return write_reports(day, preds, picks, inj_raw, state)
