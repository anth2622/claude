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
    out.append(f"Policy in use: {{**DEFAULT_POLICY, **{state.get('policy', {})}}}")
    out.append(f"Params override: {state.get('params', {})}")
    return "\n".join(out)
