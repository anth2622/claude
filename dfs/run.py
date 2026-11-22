#!/usr/bin/env python3
"""DraftKings NFL Classic GPP optimizer — command line entry point.

Example:
    python dfs/fetch_data.py                                   # refresh nflverse data
    python dfs/run.py --dk DKSalaries.csv --week 11 --sims 20000

Outputs (in --out, default dfs/output):
    projections.csv      every player: mean / median / p85 / p95 / sd / projected ownership
    top_lineups.csv      top-N lineups with simulation metrics
    dk_upload.csv        top-N lineups in DraftKings bulk-upload format
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import model as M  # noqa: E402
import optimizer as O  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dk", required=True, help="DKSalaries.csv exported from the contest page")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--candidates", type=int, default=500)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--contest-size", type=int, default=100000)
    ap.add_argument("--min-salary", type=int, default=48500)
    ap.add_argument("--exclude", default="", help="comma-separated player names to exclude")
    ap.add_argument("--ownership", help="optional CSV with Name,Ownership (percent)")
    ap.add_argument("--q-miss", type=float, default=0.12, help="chance a Questionable player sits")
    ap.add_argument("--lines", default="", help='line overrides "AWAY@HOME=homeSpread/total,..." e.g. "MIA@BUF=-16.5/46.5"')
    ap.add_argument("--stack-n", type=int, default=2, help="min WR/TE stacked with QB")
    ap.add_argument("--qb-team-max", type=int, default=None, help="max players (any pos) from QB's team besides QB")
    ap.add_argument("--bringback-max", type=int, default=None, help="max players from QB's opponent")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"))
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    os.makedirs(a.out, exist_ok=True)
    excl = [x.strip() for x in a.exclude.split(",") if x.strip()]

    dk = O.read_dk(a.dk)
    stats, games, inj = M.load_data()
    for ov in filter(None, [x.strip() for x in a.lines.split(",")]):
        gid, v = ov.split("=")
        aw, hm = gid.split("@")
        sp, tot = map(float, v.split("/"))
        sel = (games.season == a.season) & (games.week == a.week) & (games.away_team == aw) & (games.home_team == hm)
        games.loc[sel, "spread_line"] = -sp      # nflverse: positive = home favoured
        games.loc[sel, "total_line"] = tot
    lines = M.slate_lines(games, a.season, a.week, teams=set(dk.team))
    missing = set(dk.team) - set(lines.index)
    if missing:
        print(f"WARNING: no Vegas line for {sorted(missing)}", file=sys.stderr)
    print("Vegas implied totals:")
    print(lines[["opp", "spread", "total", "implied"]].sort_values("implied", ascending=False).round(2).to_string())

    profiles = M.build_profiles(stats, inj, a.week, extra_out=excl, dk=dk)
    defense = M.build_defense(stats, a.week)
    sh = M.avg_spread_history(games, a.season, a.week)
    players, sims, _ = M.simulate(lines, profiles, defense, sh, n_sims=a.sims, seed=a.seed)

    # questionable players: chance of a zero
    rng = np.random.default_rng(a.seed + 1)
    for i in np.where(players.status.values == "Q")[0]:
        sims[i, rng.random(sims.shape[1]) < a.q_miss] = 0.0
    players["mean"] = sims.mean(1)

    pool, psims, unmatched = O.merge_pool(dk, players, sims, exclude=excl)
    if len(unmatched):
        top_unmatched = unmatched.sort_values("Salary", ascending=False).head(15)
        print(f"\n{len(unmatched)} DK players not in usage data (no recent snaps / inactive) — e.g.:")
        print(top_unmatched[["Name", "pos", "team", "Salary"]].to_string(index=False))

    # DK-listed Questionable players not already flagged
    for i in np.where((pool.dk_status.values == "Q") & (pool.status.values != "Q"))[0]:
        psims[i, rng.random(psims.shape[1]) < a.q_miss] = 0.0
    pool["mean"] = psims.mean(1)

    own = None
    if a.ownership:
        od = pd.read_csv(a.ownership)
        od["key"] = od.iloc[:, 0].map(M.norm_name)
        own = pool.key.map(od.set_index("key").iloc[:, 1]).fillna(0.5)
    if own is None:
        own = O.estimate_ownership(pool)

    pool, top, allres = O.optimize(pool, psims, own=own, n_candidates=a.candidates, top_n=a.top,
                                   contest_size=a.contest_size,
                                   ilp_kwargs=dict(min_salary=a.min_salary, stack_n=a.stack_n,
                                                   qb_team_max=a.qb_team_max, bringback_max=a.bringback_max))

    proj = pool[["Name", "ID", "pos", "team", "opp", "Salary", "mean", "median", "p85", "p95", "sd", "own", "status"]]
    proj = proj.assign(value=proj["mean"] / (proj.Salary / 1000)).sort_values("mean", ascending=False)
    proj.round(2).to_csv(os.path.join(a.out, "projections.csv"), index=False)

    print("\nTop projections by position:")
    for pos in ["QB", "RB", "WR", "TE", "DST"]:
        print(proj[proj.pos == pos].head(8)[["Name", "team", "opp", "Salary", "mean", "p95", "own", "value", "status"]].round(2).to_string(index=False))

    rows = []
    for r, row in top.iterrows():
        t = O.lineup_table(pool, row.lineup)
        rows.append({**{f"{s}_{k}": v for k, (s, v) in enumerate(zip(t.slot, t.Name))},
                     **row.drop("lineup").to_dict()})
    pd.DataFrame(rows).round(4).to_csv(os.path.join(a.out, "top_lineups.csv"), index=False)
    O.dk_upload_rows(pool, list(top.lineup)).to_csv(os.path.join(a.out, "dk_upload.csv"), index=False)

    best = top.iloc[0]
    print("\n================ BEST GPP LINEUP ================")
    print(O.lineup_table(pool, best.lineup).round(2).to_string(index=False))
    print(f"Salary ${int(best.salary):,} | mean {best['mean']:.1f} | p90 {best.p90:.1f} | p99 {best.p99:.1f} | "
          f"top-0.1% {best['top0.1%']*100:.2f}% | top-1% {best['top1%']*100:.2f}% | cash {best['cash%']*100:.1f}% | "
          f"own-sum {best.own_sum:.0f}% | exp dupes {best.exp_dupes:.2f}")
    print("\nTop lineups:")
    print(top.drop(columns="lineup").round(3).to_string())
    print(f"\nFiles written to {a.out}")


if __name__ == "__main__":
    main()
