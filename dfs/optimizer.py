"""DraftKings NFL Classic GPP optimizer driven by Monte Carlo simulations.

Lineup rules (Classic): QB, RB, RB, WR, WR, WR, TE, FLEX, DST, $50,000 cap.
GPP rules enforced:
  * QB + >= 2 of his own WR/TE, + >= 1 bring-back from the opponent, no RB from QB's team
  * FLEX must be WR or TE (no RB in FLEX)
  * no offensive player facing your DST
  * ownership leverage (projected ownership penalised; dupes penalised)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import highspy

from model import norm_name

SALARY_CAP = 50000
SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]


# --------------------------------------------------------------------------- #
# DK salary file
# --------------------------------------------------------------------------- #
def read_dk(path):
    dk = pd.read_csv(path, encoding="utf-8-sig")
    dk.columns = [c.strip() for c in dk.columns]
    gi = dk["Game Info"].astype(str).str.split(" ").str[0]
    dk["game"] = gi
    dk["team"] = dk.TeamAbbrev.str.strip()
    away = gi.str.split("@").str[0]
    home = gi.str.split("@").str[1]
    dk["opp"] = np.where(dk.team == away, home, away)
    dk["pos"] = dk.Position.str.strip()
    dk["dk_status"] = dk["Status"].fillna("").astype(str).str.upper() if "Status" in dk else ""
    dk["key"] = np.where(dk.pos == "DST", "dst_" + dk.team, dk.Name.map(norm_name))
    return dk


def merge_pool(dk, players, sims, exclude=(), min_mean=1.0):
    """Join DK salaries to simulated players. Returns pool df + its sim matrix."""
    players = players.copy()
    players["row"] = np.arange(len(players))
    m = dk.merge(players[["key", "team", "row", "mean", "median", "p85", "p95", "sd", "status"]],
                 on=["key", "team"], how="left")
    # fall back to name-only match (players traded since last game)
    miss = m.row.isna()
    if miss.any():
        alt = players.drop_duplicates("key").set_index("key")
        for c in ("row", "mean", "median", "p85", "p95", "sd", "status"):
            m.loc[miss, c] = m.loc[miss, "key"].map(alt[c])
    unmatched = m[m.row.isna() & (m.pos != "DST")]
    m = m[m.row.notna()].copy()
    m["row"] = m.row.astype(int)
    excl = {norm_name(x) for x in exclude}
    m = m[~m.key.isin(excl) & (m.status != "OUT") & ~m.dk_status.isin(["OUT", "O", "D", "IR", "PUP", "NA", "SUS"])
          & (m["mean"] >= min_mean)]
    m = m.reset_index(drop=True)
    return m, sims[m.row.values], unmatched


# --------------------------------------------------------------------------- #
# Ownership model (used when no ownership projections are supplied)
# --------------------------------------------------------------------------- #
OWN_TOTAL = {"QB": 100, "RB": 215, "WR": 335, "TE": 115, "DST": 100}  # field-wide % per position


def estimate_ownership(pool, temp=None):
    """Heuristic projected ownership: softmax of value (pts/$1K) and raw points within position."""
    own = pd.Series(0.0, index=pool.index)
    temp = temp or {"QB": 1.25, "RB": 1.0, "WR": 0.9, "TE": 1.1, "DST": 0.9}
    for pos, g in pool.groupby("pos"):
        value = g["mean"] / (g.Salary / 1000)
        z = 1.6 * (value - value.mean()) / (value.std() + 1e-9) + 0.9 * (g["mean"] - g["mean"].mean()) / (g["mean"].std() + 1e-9)
        w = np.exp(z / temp.get(pos, 1.0))
        o = w / w.sum() * OWN_TOTAL.get(pos, 100)
        own[g.index] = np.clip(o, 0.1, 65)
    return own


# --------------------------------------------------------------------------- #
# ILP
# --------------------------------------------------------------------------- #
class LineupILP:
    """Binary ILP solved with HiGHS; objective is swapped between solves."""

    def __init__(self, pool, min_salary=48500, stack_n=2, bringback=1, no_rb_flex=True, no_opp_dst=True,
                 max_per_team=None, qb_team_max=None, bringback_max=None, no_qb_rb=True, no_te_flex=False):
        """no_qb_rb: no RB from the QB's own team.
        qb_team_max: max offensive players from the QB's team besides the QB.
        bringback_max: max offensive players from the QB's opponent."""
        self.pool = pool
        n = self.n = len(pool)
        h = self.h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("mip_rel_gap", 1e-4)
        inf = highspy.kHighsInf
        h.addVars(n, np.zeros(n), np.ones(n))
        h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * n))
        h.changeObjectiveSense(highspy.ObjSense.kMaximize)
        P, T, O, sal = pool.pos.values, pool.team.values, pool.opp.values, pool.Salary.values.astype(float)

        def row(idx, coefs, lo, hi):
            idx = np.asarray(idx, dtype=np.int32)
            h.addRow(lo, hi, len(idx), idx, np.asarray(coefs, float))

        allidx = np.arange(n)
        row(allidx, sal, min_salary, SALARY_CAP)
        row(allidx, np.ones(n), 9, 9)
        by = lambda pos: np.where(P == pos)[0]
        row(by("QB"), np.ones(len(by("QB"))), 1, 1)
        row(by("DST"), np.ones(len(by("DST"))), 1, 1)
        row(by("TE"), np.ones(len(by("TE"))), 1, 1 if no_te_flex else inf)
        row(by("WR"), np.ones(len(by("WR"))), 3, inf)
        row(by("RB"), np.ones(len(by("RB"))), 2, 2 if no_rb_flex else inf)
        for q in by("QB"):
            mates = np.where((T == T[q]) & np.isin(P, ["WR", "TE"]))[0]
            row(np.r_[mates, q], np.r_[np.ones(len(mates)), -stack_n], 0, inf)
            if bringback:
                opps = np.where((T == O[q]) & np.isin(P, ["WR", "TE", "RB"]))[0]
                row(np.r_[opps, q], np.r_[np.ones(len(opps)), -bringback], 0, inf)
            if no_qb_rb:
                rbs = np.where((T == T[q]) & (P == "RB"))[0]
                # sum(own RBs) + 2 x_q <= 2
                row(np.r_[rbs, q], np.r_[np.ones(len(rbs)), 2], -inf, 2)
            if qb_team_max is not None:
                team_all = np.where((T == T[q]) & (P != "DST") & (np.arange(n) != q))[0]
                # sum(team) <= qb_team_max + 8 * (1 - x_q)
                row(np.r_[team_all, q], np.r_[np.ones(len(team_all)), 8 - qb_team_max], -inf, 8)
            if bringback_max is not None:
                opp_all = np.where((T == O[q]) & (P != "DST"))[0]
                row(np.r_[opp_all, q], np.r_[np.ones(len(opp_all)), 8 - bringback_max], -inf, 8)
        if no_opp_dst:
            for d in by("DST"):
                offs = np.where((P != "DST") & (T == O[d]))[0]
                # sum(opposing offense) <= 8 * (1 - x_d)
                row(np.r_[offs, d], np.r_[np.ones(len(offs)), 8], -inf, 8)
        if max_per_team:
            for t in set(T):
                m = np.where((T == t) & (P != "DST"))[0]
                row(m, np.ones(len(m)), 0, max_per_team)

    def solve(self, obj):
        h = self.h
        h.changeColsCost(self.n, np.arange(self.n, dtype=np.int32), np.asarray(obj, float))
        h.run()
        if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            return None
        v = np.asarray(h.getSolution().col_value)
        return tuple(np.where(v > 0.5)[0].tolist())


# --------------------------------------------------------------------------- #
# Field simulation
# --------------------------------------------------------------------------- #
def sample_field(pool, own, n=3000, n_opt=300, opt_weight=2, seed=11, log=print):
    """Build a realistic opponent field.

    * `n_opt` lineups from a field-style optimiser: noisy projections tilted toward chalk,
      no forced stacks, RB allowed in FLEX (what most entrants actually play).
    * the rest sampled in proportion to projected ownership, keeping only lineups that
      use >= $49,000 and project in the top 25% of sampled lineups (entrants are not random).
    """
    rng = np.random.default_rng(seed)
    P = pool.pos.values
    sal = pool.Salary.values
    mean = pool["mean"].values
    o = own.values
    ilp = LineupILP(pool, min_salary=49000, stack_n=0, bringback=0, no_rb_flex=False, no_opp_dst=False,
                    no_qb_rb=False)
    opt = []
    for _ in range(n_opt):
        obj = mean * rng.lognormal(0, 0.30, len(mean)) + 0.06 * o
        lu = ilp.solve(obj)
        if lu:
            v = np.zeros(len(pool), bool)
            v[list(lu)] = True
            opt.append(v)
    logw = np.log(np.maximum(o, 0.05))
    out = []
    tries = 0
    n_rand = max(n - opt_weight * len(opt), 0)
    while len(out) < n_rand and tries < 300:
        tries += 1
        B = 20000
        g = logw[None, :] + rng.gumbel(size=(B, len(pool)))
        pick = np.zeros((B, len(pool)), bool)
        for pos, k in (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1), ("DST", 1)):
            cols = np.where(P == pos)[0]
            top = np.argsort(-g[:, cols], axis=1)[:, :k]
            pick[np.arange(B)[:, None], cols[top]] = True
        flex_cols = np.where(np.isin(P, ["RB", "WR", "TE"]))[0]
        gf = np.where(pick[:, flex_cols], -np.inf, g[:, flex_cols])
        fsel = flex_cols[np.argmax(gf, 1)]
        pick[np.arange(B), fsel] = True
        s = pick @ sal
        ok = (s <= SALARY_CAP) & (s >= 49000)
        pk = pick[ok]
        if len(pk) == 0:
            continue
        pm = pk @ mean
        keep = pk[pm >= np.quantile(pm, 0.75)]
        out.extend(list(keep))
    field = np.array(out[:n_rand] + opt * opt_weight)
    return field


# --------------------------------------------------------------------------- #
# Main optimisation routine
# --------------------------------------------------------------------------- #
def payout_table(n_entries, rake=0.15, paid_frac=0.22):
    """Approximate DK GPP payout curve (top-heavy), in units of entry fee, indexed by rank-1."""
    n_paid = max(int(n_entries * paid_frac), 1)
    r = np.arange(1, n_paid + 1)
    w = r ** -1.1
    pool = n_entries * (1 - rake)
    min_cash = 2.0
    pay = min_cash + (pool - min_cash * n_paid) * w / w.sum()
    out = np.zeros(n_entries)
    out[:n_paid] = pay
    return out


def validate(pool, lu, rules):
    """Return list of rule violations for a lineup (empty = valid)."""
    g = pool.loc[list(lu)]
    err = []
    c = g.pos.value_counts()
    if len(g) != 9: err.append("size")
    if g.Salary.sum() > SALARY_CAP: err.append("salary")
    if c.get("QB", 0) != 1 or c.get("DST", 0) != 1: err.append("qb/dst count")
    if c.get("WR", 0) < 3 or c.get("TE", 0) < 1: err.append("wr/te count")
    if rules.get("no_rb_flex", True) and c.get("RB", 0) != 2: err.append("RB in FLEX")
    if rules.get("no_te_flex", False) and c.get("TE", 0) != 1: err.append("TE in FLEX")
    qb = g[g.pos == "QB"].iloc[0]
    mates = g[(g.team == qb.team) & (g.pos != "QB") & (g.pos != "DST")]
    opps = g[(g.team == qb.opp) & (g.pos != "DST")]
    if (mates.pos.isin(["WR", "TE"])).sum() < rules.get("stack_n", 2): err.append("stack")
    if len(opps) < rules.get("bringback", 1): err.append("bring-back")
    if rules.get("no_qb_rb", True) and (mates.pos == "RB").any(): err.append("RB with own QB")
    if rules.get("qb_team_max") is not None and len(mates) > rules["qb_team_max"]: err.append("qb team max")
    if rules.get("bringback_max") is not None and len(opps) > rules["bringback_max"]: err.append("bring-back max")
    dst = g[g.pos == "DST"].iloc[0]
    if rules.get("no_opp_dst", True) and ((g.team == dst.opp) & (g.pos != "DST")).any(): err.append("vs own DST")
    return err


def optimize(pool, sims, own=None, n_candidates=600, top_n=20, contest_size=1500, field_n=3000,
             own_penalty=0.03, seed=3, ilp_kwargs=None, log=print, eval_sims=None, n_final=150):
    rng = np.random.default_rng(seed)
    if own is None:
        own = estimate_ownership(pool)
    pool = pool.assign(own=own.values)
    field = sample_field(pool, pool.own, n=field_n, log=log).astype(np.float32)
    # ownership = exposure in the simulated field, so leverage, dupes and payouts are consistent
    pool["own_prior"] = pool.own
    pool["own"] = np.maximum(field.mean(0) * 100, 0.1)
    ilp = LineupILP(pool, **(ilp_kwargs or {}))
    mean = pool["mean"].values
    S = sims.shape[1]

    cands = set()
    mean_lu = ilp.solve(mean)                                  # true max-projection lineup under the rules
    for obj in (mean, pool.p85.values, pool.p95.values, mean - own_penalty * pool.own.values):
        lu = ilp.solve(obj)
        if lu:
            cands.add(lu)
    log(f"generating {n_candidates} candidate lineups from simulated outcomes ...")
    for k in range(n_candidates):
        j = rng.integers(S)
        lam = rng.uniform(0.3, 0.8)                         # weight on the simulated outcome
        pen = own_penalty * rng.uniform(0, 2) if k % 2 else 0.0
        obj = (1 - lam) * mean + lam * sims[:, j] - pen * pool.own.values
        lu = ilp.solve(obj)
        if lu:
            cands.add(lu)
    cands = list(cands)
    log(f"{len(cands)} unique candidates; simulating field of {field_n} opponent lineups ...")

    pay = payout_table(contest_size)
    o = pool.own.values / 100.0

    def evaluate(lineups, sims=sims, field_scores=None):
        if field_scores is None:
            field_scores = fs_main
        S = sims.shape[1]
        X = np.zeros((len(lineups), len(pool)), np.float32)
        for r, lu in enumerate(lineups):
            X[r, list(lu)] = 1
        cs = X @ sims                                          # [C, S]
        F = field_scores.shape[0]
        n = len(lineups)
        ev, win, top01, top1, cash = (np.zeros(n) for _ in range(5))
        paid_n = int((pay > 0).sum())
        for j in range(S):
            beaten_by = F - np.searchsorted(field_scores[:, j], cs[:, j], side="right")
            frac = beaten_by / F
            rank = np.minimum((frac * (contest_size - 1)).astype(int), contest_size - 1)
            ev += pay[rank]
            win += rank == 0
            top01 += frac < 0.001
            top1 += frac < 0.01
            cash += rank < paid_n
        dup = np.array([contest_size * np.prod(o[list(lu)]) for lu in lineups])
        r = pd.DataFrame({
            "lineup": list(lineups), "mean": cs.mean(1), "median": np.median(cs, 1),
            "p90": np.percentile(cs, 90, 1), "p99": np.percentile(cs, 99, 1),
            "win%": win / S, "top0.1%": top01 / S, "top1%": top1 / S, "cash%": cash / S,
            "salary": X @ pool.Salary.values, "own_sum": X @ pool.own.values, "exp_dupes": dup,
        })
        r["roi%"] = 100 * ((ev / S) / (1 + dup) - 1)
        return r

    fs_main = np.sort(field @ sims, axis=0)                    # [F, S] ascending per sim
    rules = dict(ilp_kwargs or {})
    res = evaluate(cands)
    res = res.sort_values(["roi%", "p99"], ascending=False).reset_index(drop=True)

    # ---- local search: try every valid single-player swap on the top lineups ----
    P = pool.pos.values
    sal = pool.Salary.values
    seen = set(res.lineup)
    for it in range(4):
        improved = False
        swaps, parents = [], []
        for lu in list(res.lineup[:12]):
            s0 = sal[list(lu)].sum()
            for i in lu:
                grp = ["WR", "TE"] if P[i] in ("WR", "TE") else [P[i]]
                for j in np.where(np.isin(P, grp))[0]:
                    if j in lu or s0 - sal[i] + sal[j] > SALARY_CAP:
                        continue
                    new = tuple(sorted(set(lu) - {i} | {j}))
                    if new in seen:
                        continue
                    seen.add(new)
                    if not validate(pool, new, rules) and sal[list(new)].sum() >= rules.get("min_salary", 48500):
                        swaps.append(new)
        if not swaps:
            break
        log(f"local search pass {it + 1}: evaluating {len(swaps)} single-player swaps ...")
        sr = evaluate(swaps)
        best_before = res["roi%"].iloc[0]
        res = pd.concat([res, sr]).sort_values(["roi%", "p99"], ascending=False).reset_index(drop=True)
        log(f"  best ROI {best_before:.1f}% -> {res['roi%'].iloc[0]:.1f}%")
        if res["roi%"].iloc[0] <= best_before + 1e-9 and it > 0:
            break

    if eval_sims is not None:
        # out-of-sample re-ranking: picking the max of thousands of noisy estimates overstates them
        fin = list(res.lineup[:n_final])
        log(f"re-scoring top {len(fin)} lineups on {eval_sims.shape[1]} independent simulations ...")
        fs_eval = np.sort(field @ eval_sims, axis=0)
        res_is = res.iloc[:n_final].set_index(res.lineup[:n_final])
        res = evaluate(fin, sims=eval_sims, field_scores=fs_eval)
        res["roi_insample%"] = res.lineup.map(res_is["roi%"]).values
        res = res.sort_values(["roi%", "p99"], ascending=False).reset_index(drop=True)

    bad = [(i, validate(pool, lu, rules)) for i, lu in enumerate(res.lineup)]
    bad = [b for b in bad if b[1]]
    if bad:
        log(f"WARNING: {len(bad)} lineups violate rules, removed: {bad[:5]}")
        res = res.drop(index=[b[0] for b in bad]).reset_index(drop=True)
    else:
        log(f"validator: all {len(res)} lineups satisfy every rule")
    res.attrs["field"] = field
    # also report the pure max-projection lineup
    res.attrs["max_mean_lineup"] = evaluate([mean_lu]).iloc[0]
    return pool, res.head(top_n), res


def lineup_table(pool, lu):
    """Order a lineup into DK slots."""
    g = pool.loc[list(lu)].copy()
    order = []
    used = set()
    for slot in SLOTS:
        if slot == "FLEX":
            cand = g[g.pos.isin(["WR", "TE", "RB"]) & ~g.index.isin(used)]
        else:
            cand = g[(g.pos == slot) & ~g.index.isin(used)]
        cand = cand.sort_values("Salary", ascending=False)
        i = cand.index[0]
        used.add(i)
        order.append((slot, i))
    t = pd.DataFrame([dict(slot=s, **pool.loc[i, ["Name", "ID", "team", "opp", "Salary", "mean", "p95", "own"]].to_dict())
                      for s, i in order])
    return t


def dk_upload_rows(pool, lineups):
    rows = []
    for lu in lineups:
        t = lineup_table(pool, lu)
        rows.append([int(v) for v in t.ID])
    return pd.DataFrame(rows, columns=SLOTS)


def score_chunk(pool, lineups, sims, field, contest_size):
    """Accumulate payout statistics of `lineups` over one block of simulations."""
    pay = payout_table(contest_size)
    paid_n = int((pay > 0).sum())
    X = np.zeros((len(lineups), len(pool)), np.float32)
    for r, lu in enumerate(lineups):
        X[r, list(lu)] = 1
    cs = X @ sims
    fs = np.sort(field.astype(np.float32) @ sims, axis=0)
    F, S = fs.shape
    n = len(lineups)
    out = {k: np.zeros(n) for k in ("ev", "ev2", "win", "top1", "cash")}
    for j in range(S):
        beaten_by = F - np.searchsorted(fs[:, j], cs[:, j], side="right")
        rank = np.minimum((beaten_by / F * (contest_size - 1)).astype(int), contest_size - 1)
        p = pay[rank]
        out["ev"] += p
        out["ev2"] += p * p
        out["win"] += rank == 0
        out["top1"] += beaten_by / F < 0.01
        out["cash"] += rank < paid_n
    out["sum"] = cs.sum(1)
    out["n"] = S
    return out


def single_swaps(pool, lu, rules):
    P, sal = pool.pos.values, pool.Salary.values
    s0 = sal[list(lu)].sum()
    out = []
    for i in lu:
        grp = ["WR", "TE"] if P[i] in ("WR", "TE") else [P[i]]
        for j in np.where(np.isin(P, grp))[0]:
            if j in lu or s0 - sal[i] + sal[j] > SALARY_CAP:
                continue
            new = tuple(sorted(set(lu) - {i} | {j}))
            if sal[list(new)].sum() >= rules.get("min_salary", 48500) and not validate(pool, new, rules):
                out.append(new)
    return out
