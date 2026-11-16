"""Diagnostics for the feedback loop: is the model earning its edges on live data?"""
from __future__ import annotations

import numpy as np

from . import calibrate, live


def run() -> str:
    preds = [p for p in live._read(live.PRED_PATH) if p.get("status") == "final"]
    picks = [k for k in live._read(live.PICKS_PATH) if k["result"] in ("W", "L", "P")]
    state = calibrate.load_state()
    out = [f"Live graded games: {len(preds)} | graded picks: {len(picks)}"]
    t = live.tally(live._read(live.PICKS_PATH))
    for name in ("all", "spread", "total", "last30"):
        out.append(f"  {name:7}{live._fmt(t[name])}")

    # Calibration fitted on live morning-line data only vs. the blended one in use.
    rows = live.calibration_rows()
    live_rows = [r for r in rows if r.get("weight", 1) != 1]
    if len(live_rows) >= 50:
        lc = calibrate.fit(live_rows, "open")
        out.append(f"Live-only calibration slopes: spread {lc['spread']['a']:.4f} (n={lc['spread']['n']}), "
                   f"total {lc['total']['a']:.4f} (n={lc['total']['n']})")
    cal = state.get("calibration", {})
    out.append(f"In-use calibration slopes: spread {cal.get('spread', {}).get('a', 0):.4f}, "
               f"total {cal.get('total', {}).get('a', 0):.4f}")

    # Every live game (not just picks): hit rate by edge bucket, and line movement toward us.
    for market in ("spread", "total"):
        E = []
        for p in preds:
            hs, as_ = float(p["home_score"]), float(p["away_score"])
            if market == "spread" and p["spread_home"] not in ("", None):
                e = float(p["pred_margin"]) + float(p["spread_home"])
                y = calibrate.cover_outcome(hs - as_, float(p["spread_home"]))
                mv = None if p["close_spread_home"] in ("", None) else float(p["spread_home"]) - float(p["close_spread_home"])
            elif market == "total" and p["total"] not in ("", None):
                e = float(p["pred_total"]) - float(p["total"])
                y = calibrate.over_outcome(hs + as_, float(p["total"]))
                mv = None if p["close_total"] in ("", None) else float(p["close_total"]) - float(p["total"])
            else:
                continue
            E.append((e, y, mv))
        if not E:
            continue
        out.append(f"{market}: all live games vs morning line")
        for th in (0, 2, 3, 4, 6):
            sel = [(e, y, mv) for e, y, mv in E if abs(e) >= th]
            res = [(y if e > 0 else 1 - y) for e, y, _ in sel if y is not None]
            mvs = [np.sign(e) * mv for e, _, mv in sel if mv is not None]
            if res:
                out.append(f"  |edge|>={th}: n={len(res):4d} hit {np.mean(res):.3f} | "
                           f"line moved our way {np.mean([m > 0 for m in mvs]) if mvs else float('nan'):.2f}, "
                           f"against {np.mean([m < 0 for m in mvs]) if mvs else float('nan'):.2f}")
    out.append(side_split())
    out.append(f"Policy in use: {{**DEFAULT_POLICY, **{state.get('policy', {})}}}")
    out.append(f"Params override: {state.get('params', {})}")
    return "\n".join(out)


def side_split() -> str:
    """Over vs under (and home vs away) tracking, flagged by the user on 2026-11-16:
    backtest over picks hit 52.1% vs unders 54.1% (|edge| >= 3 vs open)."""
    preds = [p for p in live._read(live.PRED_PATH) if p.get("status") == "final"]
    picks = [k for k in live._read(live.PICKS_PATH) if k["result"] in ("W", "L")]
    out = ["Side split (bias watch):"]
    acc = {"over": [], "under": [], "home": [], "away": []}
    for p in preds:
        hs, as_ = float(p["home_score"]), float(p["away_score"])
        if p.get("p_over") not in ("", None) and p["total"] not in ("", None):
            y = calibrate.over_outcome(hs + as_, float(p["total"]))
            if y is not None:
                over = float(p["p_over"]) >= 0.5
                mv = None if p["close_total"] in ("", None) else float(p["close_total"]) - float(p["total"])
                acc["over" if over else "under"].append((y if over else 1 - y, None if mv is None else (mv if over else -mv)))
        if p.get("p_home_cover") not in ("", None) and p["spread_home"] not in ("", None):
            y = calibrate.cover_outcome(hs - as_, float(p["spread_home"]))
            if y is not None:
                home = float(p["p_home_cover"]) >= 0.5
                mv = None if p["close_spread_home"] in ("", None) else float(p["spread_home"]) - float(p["close_spread_home"])
                acc["home" if home else "away"].append((y if home else 1 - y, None if mv is None else (mv if home else -mv)))
    for k, v in acc.items():
        if not v:
            continue
        clv = [m for _, m in v if m is not None]
        out.append(f"  leans {k:5}: n={len(v):3d} hit {np.mean([w for w, _ in v]):.3f} | "
                   f"close moved our way {np.mean([m > 0 for m in clv]) if clv else float('nan'):.2f}, "
                   f"avg {np.mean(clv) if clv else float('nan'):+.2f} pts")
    for side in ("over", "under", "home", "away"):
        ks = [k for k in picks if k["side"] == side]
        if ks:
            w = sum(k["result"] == "W" for k in ks)
            out.append(f"  official {side:5}: {w}-{len(ks) - w} ({w / len(ks):.3f}), "
                       f"{sum(float(k['profit']) for k in ks):+.2f}u")
    return "\n".join(out)
