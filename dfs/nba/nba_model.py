"""NBA DraftKings projection model + correlated Monte Carlo simulator (ESPN data).

Projection
----------
* Minutes: recency-weighted minutes over games played (last ~10 weigh most), regressed toward
  the season average. Active players' minutes on each team are rescaled to 240, so minutes from
  players who are Out go to the teammates who are playing.
* Per-minute production (PTS, 3PM, REB, AST, STL, BLK, TOV): recency-weighted, regressed toward
  the position average with 120 pseudo-minutes.
* Vegas: scoring is scaled so the active players' projected points sum to the implied team
  total. Other counting stats scale with expected pace (game total vs the two teams' season
  average) to the 0.6 power.
* Blowout risk: the spread sets the margin distribution. Starters lose minutes in lopsided sims and
  the bench gains them.

DK scoring: PTS 1, 3PM 0.5, REB 1.25, AST 1.5, STL 2, BLK 2, TOV -0.5, double-double +1.5, triple-double +3.
"""
from __future__ import annotations

import json
import re
import unicodedata

import numpy as np
import pandas as pd

ESPN_TO_DK = {"WSH": "WAS", "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "PHX": "PHO"}
STATS = ["pts", "tpm", "reb", "ast", "stl", "blk", "tov"]
POS_PRIOR = {  # per-minute
    "G": dict(pts=0.48, tpm=0.065, reb=0.13, ast=0.14, stl=0.033, blk=0.010, tov=0.06),
    "F": dict(pts=0.45, tpm=0.050, reb=0.20, ast=0.075, stl=0.028, blk=0.020, tov=0.045),
    "C": dict(pts=0.45, tpm=0.020, reb=0.30, ast=0.07, stl=0.025, blk=0.045, tov=0.055),
}


def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def dk_team(t):
    return ESPN_TO_DK.get(t, t)


def parse_logs(data, season_label="2026-27 Regular Season"):
    rows = []
    for p in data["players"]:
        lg = p["gamelog"]
        names = lg.get("names", [])
        evmeta = lg.get("events", {})
        for st in lg.get("seasonTypes", []):
            if st.get("displayName") != season_label:
                continue
            for cat in st.get("categories", []):
                for ev in cat.get("events", []) or []:
                    v = dict(zip(names, ev["stats"]))
                    meta = evmeta.get(ev["eventId"], {})
                    try:
                        mins = float(v.get("minutes", 0))
                    except ValueError:
                        mins = 0.0
                    t3 = str(v.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0-0")).split("-")[0]
                    rows.append(dict(
                        pid=p["id"], name=p["name"], team=dk_team(p["team"]), pos=p["pos"], date=meta.get("gameDate"),
                        min=mins, pts=float(v.get("points", 0)), tpm=float(t3 or 0), reb=float(v.get("totalRebounds", 0)),
                        ast=float(v.get("assists", 0)), stl=float(v.get("steals", 0)), blk=float(v.get("blocks", 0)),
                        tov=float(v.get("turnovers", 0)), team_score=meta.get("homeTeamScore") if meta.get("atVs") == "vs" else meta.get("awayTeamScore"),
                        opp_score=meta.get("awayTeamScore") if meta.get("atVs") == "vs" else meta.get("homeTeamScore"),
                    ))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df.date)
    return df


def lines_from(data):
    rows = []
    for g in data["games"]:
        home, away = dk_team(g["home"]), dk_team(g["away"])
        tot = float(g["total"])
        hs = float(g["spread"])  # home spread, negative = home favoured
        rows.append(dict(team=home, opp=away, total=tot, spread=hs, implied=tot / 2 - hs / 2, game=f"{away}@{home}"))
        rows.append(dict(team=away, opp=home, total=tot, spread=-hs, implied=tot / 2 + hs / 2, game=f"{away}@{home}"))
    return pd.DataFrame(rows).set_index("team")


def pos_group(p):
    p = str(p or "F")
    return "C" if p.startswith("C") else ("G" if "G" in p else "F")


def build_projection(data, out=(), questionable_play=0.6, decay=0.88):
    """Returns per-player projection table (expected minutes, per-minute rates, status)."""
    logs = parse_logs(data)
    lines = lines_from(data)
    status = {(dk_team(p["team"]), norm_name(p["name"])): p["injury"] for p in data["players"]}
    out = {norm_name(x) for x in out}
    team_dates = {t: sorted(g.date.unique()) for t, g in logs.groupby("team")}
    recs = []
    for pid, g in logs[logs["min"] > 0].sort_values("date").groupby("pid"):
        g = g.tail(25)
        n = len(g)
        w = decay ** np.arange(n)[::-1]
        mins = g["min"].values
        wmin = (mins * w).sum() / w.sum()
        smin = mins.mean()
        exp_min = 0.75 * wmin + 0.25 * smin
        pg = pos_group(g.pos.iloc[-1])
        pri = POS_PRIOR[pg]
        rates = {}
        tot_min_w = (mins * w).sum()
        for s in STATS:
            rates[s] = ((g[s].values * w).sum() + 30 * pri[s]) / (tot_min_w + 30)
        team = g.team.iloc[-1]
        last10 = team_dates[team][-10:]
        appear = g.date.isin(last10).sum() / max(len(last10), 1)
        key = norm_name(g.name.iloc[-1])
        st = status.get((team, key), "")
        recs.append(dict(pid=pid, name=g.name.iloc[-1], key=key, team=team, pos=g.pos.iloc[-1], pg=pg,
                         games=n, last_min=mins[-1], exp_min=exp_min, status=st,
                         days_since=(logs.date.max() - g.date.iloc[-1]).days, appear=appear, **{f"r_{s}": v for s, v in rates.items()}))
    pr = pd.DataFrame(recs)
    pr = pr[pr.team.isin(lines.index)]
    pr.loc[pr.key.isin(out) | (pr.status == "Out"), "status"] = "Out"
    # players who have not played in 12+ days and aren't listed are likely inactive/injured
    pr = pr[(pr.days_since <= 12) | (pr.status == "Day-To-Day")]
    # play probability: Out = 0, Day-To-Day = questionable_play; fringe players by appearance rate
    pr["dtd"] = pr.status == "Day-To-Day"
    pr["play_p"] = np.where(pr.status == "Out", 0.0, np.where(pr.dtd, questionable_play, 1.0))
    pr["base_p"] = np.where(pr.status == "Out", 0.0, np.where(pr.exp_min >= 20, 1.0, np.clip(pr.appear, 0.15, 1.0)))
    team_avg = logs.groupby(["team", "date"]).team_score.first().astype(float).groupby(level=0).mean()
    pr = pr.reset_index(drop=True)
    pr["proj_min"] = 0.0
    pr["f_pts"] = 1.0
    pr["f_other"] = 1.0
    scen = {}
    for t, g in pr.groupby("team"):
        idx = g.index.values
        dtd = [i for i in idx if pr.at[i, "dtd"]]
        r = lines.loc[t]
        avg_t, avg_o = team_avg.get(t, 115), team_avg.get(r.opp, 115)
        pace = np.clip(r.total / (avg_t + avg_o), 0.9, 1.1)
        combos = {}
        for mask in range(2 ** len(dtd)):
            plays = {d: bool(mask >> k & 1) for k, d in enumerate(dtd)}
            pp = np.array([pr.at[i, "base_p"] * (plays[i] if i in plays else 1.0) for i in idx])
            pm = _allocate(g.exp_min.values.copy(), pp)
            raw = (g.r_pts.values * pm * pp).sum()
            f_pts = float(np.clip(r.implied / max(raw, 1), 0.8, 1.25))
            combos[mask] = (pm, pp, f_pts)
        scen[t] = dict(idx=idx, dtd=dtd, combos=combos)
        # expected values for reporting (weighted over scenarios)
        w = {m: np.prod([questionable_play if (m >> k & 1) else 1 - questionable_play for k in range(len(dtd))])
             for m in combos}
        pr.loc[idx, "proj_min"] = sum(w[m] * combos[m][0] * (combos[m][1] > 0) for m in combos) / \
            np.maximum(sum(w[m] * (combos[m][1] > 0) for m in combos), 1e-9)
        pr.loc[idx, "f_pts"] = sum(w[m] * combos[m][2] for m in combos)
        pr.loc[idx, "f_other"] = pace ** 0.6
    lines.attrs["scen"] = scen
    lines.attrs["q_play"] = questionable_play
    return pr.reset_index(drop=True), lines


def _allocate(pm, pp):
    """Rescale expected team minutes to 240 (trim fringe players first, give vacated minutes to rotation)."""
    for _ in range(25):
        tot = (pm * pp).sum()
        if abs(tot - 240) < 0.5:
            break
        if tot > 240:
            wgt = np.clip((38 - pm) / 20, 0.05, 1.0) * (pp > 0)
        else:
            wgt = np.clip(pm / 30, 0.2, 1.3) * (pp > 0)
        pm = np.clip(pm + (240 - tot) * wgt / max((wgt * pp).sum(), 1e-9), 0, 42)
    return pm


def simulate(pr, lines, n_sims=20000, seed=7):
    rng = np.random.default_rng(seed)
    S = n_sims
    margin, pace = {}, {}
    for gid, g in lines.groupby("game"):
        hs = lines.loc[gid.split("@")[1], "spread"]
        m = rng.normal(-hs, 12.5, S)               # home margin
        p = np.exp(rng.normal(0, 0.06, S))
        h, a = gid.split("@")[1], gid.split("@")[0]
        margin[h], margin[a] = m, -m
        pace[h] = pace[a] = p
    team_eff = {t: np.exp(rng.normal(0, 0.07, S)) for t in lines.index}

    scen, qp = lines.attrs["scen"], lines.attrs["q_play"]
    mean_min = np.zeros((len(pr), S), np.float32)
    play_ok = np.zeros((len(pr), S), bool)
    fpts = np.ones((len(pr), S), np.float32)
    for t, sc in scen.items():
        dmask = np.zeros(S, int)
        for k, d in enumerate(sc["dtd"]):
            dmask |= (rng.random(S) < qp).astype(int) << k
        for m, (pm, pp, f) in sc["combos"].items():
            sel = dmask == m
            for j, i in enumerate(sc["idx"]):
                mean_min[i, sel] = pm[j]
                play_ok[i, sel] = rng.random(sel.sum()) < pp[j]
                fpts[i, sel] = f
    out = np.zeros((len(pr), S), np.float32)
    for i, r in pr.iterrows():
        plays = play_ok[i]
        blow = np.abs(margin[r.team])
        starter = r.exp_min >= 26
        # blowouts: starters lose up to ~25% of minutes, bench gains
        bfac = np.where(blow > 15, 1 - np.clip((blow - 15) / 40, 0, 0.28), 1.0) if starter else \
            np.where(blow > 15, 1 + np.clip((blow - 15) / 30, 0, 0.45), 1.0)
        mm = mean_min[i]
        mins = np.clip(rng.normal(mm, 3.5 + 0.06 * mm, S) * bfac, 0, 48) * plays
        game_f = pace[r.team] * team_eff[r.team]
        pnoise = np.exp(rng.normal(0, 0.18, S) - 0.0162)        # player-specific shooting/role night
        lam = {}
        for s in STATS:
            f = fpts[i] if s in ("pts", "tpm") else r.f_other
            g_ = game_f if s in ("pts", "tpm", "ast") else pace[r.team]
            pn = pnoise if s in ("pts", "tpm", "ast", "tov") else np.exp(rng.normal(0, 0.12, S) - 0.0072)
            lam[s] = r[f"r_{s}"] * mins * f * g_ * pn
        pts = rng.poisson(np.maximum(lam["pts"] / 2.0, 0)) * 2.0
        tpm = np.minimum(rng.poisson(lam["tpm"]), pts // 3)
        reb = rng.poisson(lam["reb"]); ast = rng.poisson(lam["ast"])
        stl = rng.poisson(lam["stl"]); blk = rng.poisson(lam["blk"]); tov = rng.poisson(lam["tov"])
        dd_cnt = (pts >= 10).astype(int) + (reb >= 10) + (ast >= 10) + (stl >= 10) + (blk >= 10)
        dk = pts + 0.5 * tpm + 1.25 * reb + 1.5 * ast + 2 * stl + 2 * blk - 0.5 * tov + 1.5 * (dd_cnt >= 2) + 3 * (dd_cnt >= 3)
        out[i] = dk
    pr = pr.copy()
    pr["mean"] = out.mean(1)
    pr["median"] = np.median(out, 1)
    pr["p85"] = np.percentile(out, 85, axis=1)
    pr["p95"] = np.percentile(out, 95, axis=1)
    pr["sd"] = out.std(1)
    pr["opp"] = pr.team.map(lines.opp)
    pr["game"] = pr.team.map(lines.game)
    return pr, out


if __name__ == "__main__":
    import sys
    d = json.load(open(sys.argv[1]))
    pr, lines = build_projection(d)
    pr, sims = simulate(pr, lines, 10000)
    pd.set_option("display.width", 200)
    print(lines)
    print(pr.sort_values("mean", ascending=False).head(40)[
        ["name", "team", "pos", "status", "proj_min", "f_pts", "mean", "p95", "sd"]].round(2).to_string(index=False))
