#!/usr/bin/env python3
"""DraftKings NBA Classic GPP optimizer.

    python dfs/nba/fetch_espn.py 20261124
    python dfs/nba/nba_run.py --dk DKSalaries_NBA.csv --espn dfs/nba/data/espn_20261124.json

Slots: PG, SG, SF, PF, C, G, F, UTIL, $50,000 cap, players from >= 2 games.
Lineups are generated from simulated outcomes, ranked by expected payout against a simulated
field (ownership = field exposure) and re-ranked on fresh simulations.
"""
import argparse
import json
import os
import sys

import highspy
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import nba_model as M  # noqa: E402
from optimizer import payout_table  # noqa: E402  (shared with NFL)

SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
CAP = 50000


def read_dk(path):
    dk = pd.read_csv(path, encoding="utf-8-sig")
    dk.columns = [c.strip() for c in dk.columns]
    dk["team"] = dk.TeamAbbrev.str.strip()
    alias = {"alexandre sarr": "alex sarr"}
    dk["key"] = dk.Name.map(M.norm_name).replace(alias)
    dk["elig"] = dk["Roster Position"].str.split("/")
    dk["dk_status"] = dk["Status"].fillna("").astype(str).str.upper() if "Status" in dk else ""
    return dk


class NBAILP:
    def __init__(self, pool, min_salary=49000, max_per_team=None):
        self.pool = pool
        n = self.n = len(pool)
        self.var = [(i, s) for i in range(n) for s in SLOTS if s in pool.elig.iloc[i]]
        V = len(self.var)
        h = self.h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.addVars(V, np.zeros(V), np.ones(V))
        h.changeColsIntegrality(V, np.arange(V, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * V))
        h.changeObjectiveSense(highspy.ObjSense.kMaximize)
        inf = highspy.kHighsInf
        add = lambda idx, c, lo, hi: h.addRow(lo, hi, len(idx), np.asarray(idx, np.int32), np.asarray(c, float))
        for s in SLOTS:
            idx = [k for k, (i, ss) in enumerate(self.var) if ss == s]
            add(idx, np.ones(len(idx)), 1, 1)
        for i in range(n):
            idx = [k for k, (ii, _) in enumerate(self.var) if ii == i]
            if idx:
                add(idx, np.ones(len(idx)), 0, 1)
        sal = pool.Salary.values
        add(list(range(V)), [sal[i] for i, _ in self.var], min_salary, CAP)
        games = pool.game.values
        gl = sorted(set(games))
        if len(gl) > 1:  # at least 2 games: no game may hold all 8
            for g in gl:
                idx = [k for k, (i, _) in enumerate(self.var) if games[i] == g]
                add(idx, np.ones(len(idx)), 0, 7)
        if max_per_team:
            for t in set(pool.team):
                idx = [k for k, (i, _) in enumerate(self.var) if pool.team.iloc[i] == t]
                add(idx, np.ones(len(idx)), 0, max_per_team)

    def solve(self, obj):
        c = np.array([obj[i] for i, _ in self.var], float)
        self.h.changeColsCost(len(c), np.arange(len(c), dtype=np.int32), c)
        self.h.run()
        if self.h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            return None
        v = np.asarray(self.h.getSolution().col_value)
        sel = [self.var[k] for k in np.where(v > 0.5)[0]]
        return tuple(sorted(i for i, _ in sel)), {s: i for i, s in sel}


def score(lineups, sims, field, pay, contest_size):
    X = np.zeros((len(lineups), sims.shape[0]), np.float32)
    for r, lu in enumerate(lineups):
        X[r, list(lu)] = 1
    cs = X @ sims
    fs = np.sort(field @ sims, axis=0)
    F, S = fs.shape
    paid = int((pay > 0).sum())
    ev = np.zeros(len(lineups)); ev2 = np.zeros(len(lineups)); win = np.zeros(len(lineups))
    top1 = np.zeros(len(lineups)); cash = np.zeros(len(lineups))
    for j in range(S):
        b = F - np.searchsorted(fs[:, j], cs[:, j], side="right")
        rank = np.minimum((b / F * (contest_size - 1)).astype(int), contest_size - 1)
        p = pay[rank]
        ev += p; ev2 += p * p; win += rank == 0; top1 += b / F < 0.01; cash += rank < paid
    m = ev / S
    return pd.DataFrame({"lineup": list(lineups), "mean": cs.mean(1), "p90": np.percentile(cs, 90, 1),
                         "win%": win / S, "top1%": top1 / S, "cash%": cash / S,
                         "roi%": 100 * (m - 1), "roi_se": 100 * np.sqrt(np.maximum(ev2 / S - m * m, 0) / S)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dk", required=True)
    ap.add_argument("--espn", required=True)
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--candidates", type=int, default=500)
    ap.add_argument("--contest-size", type=int, default=1500)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--q-play", type=float, default=0.6, help="chance a Day-To-Day/Q player plays")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    pd.set_option("display.width", 220)
    rng = np.random.default_rng(3)

    data = json.load(open(a.espn))
    dk = read_dk(a.dk)
    excl = [x.strip() for x in a.exclude.split(",") if x.strip()]
    out_dk = list(dk[dk.dk_status.isin(["OUT", "O", "D"])].Name)
    pr, lines = M.build_projection(data, out=excl + out_dk, questionable_play=a.q_play)
    pr, sims = M.simulate(pr, lines, a.sims, seed=7)
    _, sims_eval = M.simulate(pr, lines, a.sims * 3, seed=1007)

    pr["row"] = np.arange(len(pr))
    pool = dk.merge(pr[["key", "team", "row", "mean", "p85", "p95", "proj_min", "status", "game", "opp"]],
                    on=["key", "team"], how="inner")
    missing = dk[~dk.key.isin(pool.key) & (dk.Salary >= 4000)]
    if len(missing):
        print("DK players without a projection (inactive / no games):", ", ".join(missing.Name.head(20)))
    pool = pool[(pool["mean"] >= 4) & ~pool.key.isin([M.norm_name(x) for x in excl])].reset_index(drop=True)
    S1, S2 = sims[pool.row.values], sims_eval[pool.row.values]
    mean = pool["mean"].values

    ilp = NBAILP(pool)
    # simulated field: noisy-projection optimisers (what most entrants play)
    field = []
    for _ in range(400):
        r = ilp.solve(mean * rng.lognormal(0, 0.22, len(mean)))
        if r:
            v = np.zeros(len(pool), np.float32); v[list(r[0])] = 1; field.append(v)
    field = np.array(field * 5)
    pool["own"] = field.mean(0) * 100

    cands = {}
    for k in range(a.candidates + 1):
        lam = 0 if k == 0 else rng.uniform(0.3, 0.8)
        obj = (1 - lam) * mean + lam * S1[:, rng.integers(S1.shape[1])] - (0.03 * pool.own.values if k % 2 else 0)
        r = ilp.solve(obj)
        if r:
            cands[r[0]] = r[1]
    pay = payout_table(a.contest_size)
    res = score(list(cands), S1, field, pay, a.contest_size).sort_values("roi%", ascending=False)
    fin = list(res.lineup[:60])
    mm = max(cands, key=lambda lu: mean[list(lu)].sum())
    if mm not in fin:
        fin.append(mm)
    res = score(fin, S2, field, pay, a.contest_size).sort_values("roi%", ascending=False).reset_index(drop=True)

    def table(lu):
        slots = cands[lu]
        return pd.DataFrame([dict(slot=s, **pool.loc[slots[s], ["Name", "ID", "team", "opp", "Salary", "mean", "p95", "proj_min", "own"]].to_dict())
                             for s in SLOTS])

    proj = pool[["Name", "ID", "Roster Position", "team", "opp", "Salary", "proj_min", "mean", "p85", "p95", "own", "status"]]
    proj = proj.assign(value=proj["mean"] / proj.Salary * 1000).sort_values("mean", ascending=False)
    proj.round(2).to_csv(os.path.join(a.out, "projections.csv"), index=False)
    print(proj.head(30).round(1).to_string(index=False))
    up = [[int(pool.ID.iloc[cands[lu][s]]) for s in SLOTS] for lu in res.lineup[:a.top]]
    pd.DataFrame(up, columns=SLOTS).to_csv(os.path.join(a.out, "dk_upload.csv"), index=False)
    res.assign(players=[" | ".join(pool.Name.values[list(lu)]) for lu in res.lineup]).drop(columns="lineup") \
        .round(4).to_csv(os.path.join(a.out, "final_ranking.csv"), index=False)

    b = res.iloc[0]
    print(f"\n===== BEST GPP LINEUP ({S2.shape[1]:,} out-of-sample sims, contest {a.contest_size}) =====")
    print(table(b.lineup).round(1).to_string(index=False))
    print(f"salary {int(pool.Salary.values[list(b.lineup)].sum())} | mean {b['mean']:.1f} | p90 {b.p90:.1f} | "
          f"ROI {b['roi%']:.0f}% +/- {b.roi_se:.0f} | win {b['win%']*100:.2f}% | top1 {b['top1%']*100:.1f}% | cash {b['cash%']*100:.1f}%")
    r = res[res.lineup == mm].iloc[0]
    print("\n===== HIGHEST PROJECTED LINEUP =====")
    print(table(mm).round(1).to_string(index=False))
    print(f"mean {r['mean']:.1f} | ROI {r['roi%']:.0f}% | cash {r['cash%']*100:.1f}%")
    print("\n", res.drop(columns="lineup").head(10).round(3).to_string())


if __name__ == "__main__":
    main()
