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
    ap.add_argument("--candidates", type=int, default=600)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--contest-size", type=int, default=1500)
    ap.add_argument("--min-salary", type=int, default=48500)
    ap.add_argument("--exclude", default="", help="comma-separated player names to exclude")
    ap.add_argument("--ownership", help="optional CSV with Name,Ownership (percent)")
    ap.add_argument("--q-miss", type=float, default=0.12, help="chance a Questionable player sits")
    ap.add_argument("--lines", default="", help='line overrides "AWAY@HOME=homeSpread/total,..." e.g. "MIA@BUF=-16.5/46.5"')
    ap.add_argument("--stack-n", type=int, default=2, help="min WR/TE stacked with QB")
    ap.add_argument("--qb-team-max", type=int, default=None, help="max players (any pos) from QB's team besides QB")
    ap.add_argument("--bringback-max", type=int, default=None, help="max players from QB's opponent")
    ap.add_argument("--no-te-flex", action="store_true", help="FLEX must be a WR")
    ap.add_argument("--save-sims", action="store_true", help="save pool + sims to out/sims.npz for diagnostics")
    ap.add_argument("--final-chunks", type=int, default=5, help="blocks of fresh sims for the final ranking")
    ap.add_argument("--final-n", type=int, default=40, help="lineups carried into the final ranking")
    ap.add_argument("--allow-qb-rb", action="store_true", help="allow an RB from the QB's team")
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

    # independent simulation set for out-of-sample lineup ranking
    players2, sims2, _ = M.simulate(lines, profiles, defense, sh, n_sims=a.sims, seed=a.seed + 1000)
    rng2 = np.random.default_rng(a.seed + 1001)
    for i in np.where(players2.status.values == "Q")[0]:
        sims2[i, rng2.random(sims2.shape[1]) < a.q_miss] = 0.0
    pool, psims, unmatched = O.merge_pool(dk, players, sims, exclude=excl)
    esims = sims2[pool.row.values]
    for i in np.where((pool.dk_status.values == "Q") & (pool.status.values != "Q"))[0]:
        esims[i, rng2.random(esims.shape[1]) < a.q_miss] = 0.0
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

    pool, top, allres = O.optimize(pool, psims, own=own, eval_sims=esims, n_candidates=a.candidates, top_n=a.top,
                                   contest_size=a.contest_size,
                                   ilp_kwargs=dict(min_salary=a.min_salary, stack_n=a.stack_n,
                                                   qb_team_max=a.qb_team_max, bringback_max=a.bringback_max,
                                                   no_qb_rb=not a.allow_qb_rb, no_te_flex=a.no_te_flex))

    if a.save_sims:
        pool.to_pickle(os.path.join(a.out, "pool.pkl"))
        np.save(os.path.join(a.out, "sims.npy"), psims)
    # ---- final ranking on large, fresh simulation blocks (removes selection bias / noise) ----
    rules = dict(min_salary=a.min_salary, stack_n=a.stack_n, qb_team_max=a.qb_team_max,
                 bringback_max=a.bringback_max, no_qb_rb=not a.allow_qb_rb, no_te_flex=a.no_te_flex)
    if a.final_chunks > 0:
        fin = list(allres.lineup[:a.final_n])
        mm_lu = allres.attrs["max_mean_lineup"].lineup
        if mm_lu not in fin:
            fin.append(mm_lu)
        for lu in O.single_swaps(pool, fin[0], rules):
            if lu not in fin:
                fin.append(lu)
        field = allres.attrs["field"]
        acc = None
        for c in range(a.final_chunks):
            print(f"final ranking: block {c + 1}/{a.final_chunks} ({len(fin)} lineups) ...", flush=True)
            _, sc, _ = M.simulate(lines, profiles, defense, sh, n_sims=a.sims, seed=a.seed + 5000 + 17 * c)
            rc = np.random.default_rng(a.seed + 7000 + c)
            sc = sc[pool.row.values]
            qmask = (pool.status.values == "Q") | (pool.dk_status.values == "Q")
            for i in np.where(qmask)[0]:
                sc[i, rc.random(sc.shape[1]) < a.q_miss] = 0.0
            r = O.score_chunk(pool, fin, sc, field, a.contest_size)
            acc = r if acc is None else {k: acc[k] + r[k] for k in acc}
        N = acc["n"]
        evm = acc["ev"] / N
        se = np.sqrt(np.maximum(acc["ev2"] / N - evm ** 2, 0) / N)
        X = np.zeros((len(fin), len(pool)))
        for i, lu in enumerate(fin):
            X[i, list(lu)] = 1
        dup = np.array([a.contest_size * np.prod(pool.own.values[list(lu)] / 100) for lu in fin])
        allres = pd.DataFrame({
            "lineup": fin, "mean": acc["sum"] / N, "win%": acc["win"] / N, "top1%": acc["top1"] / N,
            "cash%": acc["cash"] / N, "roi%": 100 * (evm / (1 + dup) - 1), "roi_se": 100 * se,
            "salary": X @ pool.Salary.values, "own_sum": X @ pool.own.values, "exp_dupes": dup,
        }).sort_values("roi%", ascending=False).reset_index(drop=True)
        top = allres.head(a.top)
        allres.attrs["max_mean_lineup"] = allres[allres.lineup == mm_lu].iloc[0]
        fr = allres.copy()
        fr["players"] = [" | ".join(pool.Name.values[list(lu)]) for lu in fr.lineup]
        fr.drop(columns="lineup").round(4).to_csv(os.path.join(a.out, "final_ranking.csv"), index=False)
        print(f"final ranking on {N:,} fresh sims; ROI standard error ~ +/-{allres.roi_se.iloc[0]:.0f} pts")

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
    print(f"Salary ${int(best.salary):,} | mean {best['mean']:.1f} | "
          f"ROI {best['roi%']:.0f}% | win {best['win%']*100:.2f}% | top-1% {best['top1%']*100:.2f}% | cash {best['cash%']*100:.1f}% | "
          f"own-sum {best.own_sum:.0f}% | exp dupes {best.exp_dupes:.2f}")
    mm = allres.attrs["max_mean_lineup"]
    print("\nHighest-projected valid lineup (for reference):")
    print(O.lineup_table(pool, mm.lineup).round(2).to_string(index=False))
    print(f"mean {mm['mean']:.1f} | ROI {mm['roi%']:.0f}% | top-1% {mm['top1%']*100:.2f}% | cash {mm['cash%']*100:.1f}%")
    print("\nTop lineups:")
    print(top.drop(columns="lineup").round(3).to_string())
    print(f"\nFiles written to {a.out}")


if __name__ == "__main__":
    main()
