"""DraftKings NFL Classic GPP optimizer driven by Monte Carlo simulations.

Lineup rules (Classic): QB, RB, RB, WR, WR, WR, TE, FLEX, DST, $50,000 cap.
GPP rules enforced:
  * QB + >= 2 of his own WR/TE, + >= 1 bring-back from the opponent
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
                 max_per_team=None, qb_team_max=None, bringback_max=None):
        """qb_team_max: max offensive players from the QB's team besides the QB.
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
        row(by("TE"), np.ones(len(by("TE"))), 1, inf)
        row(by("WR"), np.ones(len(by("WR"))), 3, inf)
        row(by("RB"), np.ones(len(by("RB"))), 2, 2 if no_rb_flex else inf)
        for q in by("QB"):
            mates = np.where((T == T[q]) & np.isin(P, ["WR", "TE"]))[0]
            row(np.r_[mates, q], np.r_[np.ones(len(mates)), -stack_n], 0, inf)
            if bringback:
                opps = np.where((T == O[q]) & np.isin(P, ["WR", "TE", "RB"]))[0]
                row(np.r_[opps, q], np.r_[np.ones(len(opps)), -bringback], 0, inf)
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
def sample_field(pool, own, n=3000, n_opt=300, seed=11, log=print):
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
    ilp = LineupILP(pool, min_salary=49000, stack_n=0, bringback=0, no_rb_flex=False, no_opp_dst=False)
    opt = []
    for _ in range(n_opt):
        obj = mean * rng.lognormal(0, 0.22, len(mean)) + 0.06 * o
        lu = ilp.solve(obj)
        if lu:
            v = np.zeros(len(pool), bool)
            v[list(lu)] = True
            opt.append(v)
    logw = np.log(np.maximum(o, 0.05))
    out = []
    tries = 0
    n_rand = max(n - 4 * len(opt), 0)
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
    field = np.array(out[:n_rand] + opt * 4)
    return field


# --------------------------------------------------------------------------- #
# Main optimisation routine
# --------------------------------------------------------------------------- #
def optimize(pool, sims, own=None, n_candidates=400, top_n=20, contest_size=100000, field_n=3000,
             own_penalty=0.10, seed=3, ilp_kwargs=None, log=print):
    rng = np.random.default_rng(seed)
    if own is None:
        own = estimate_ownership(pool)
    pool = pool.assign(own=own.values)
    ilp = LineupILP(pool, **(ilp_kwargs or {}))
    mean = pool["mean"].values
    S = sims.shape[1]

    cands = set()
    base = ilp.solve(mean)
    if base:
        cands.add(base)
    ceil = ilp.solve(pool.p85.values - own_penalty * pool.own.values)
    if ceil:
        cands.add(ceil)
    log(f"generating {n_candidates} candidate lineups from simulated outcomes ...")
    for k in range(n_candidates):
        j = rng.integers(S)
        # one simulated slate outcome, shrunk toward mean, with ownership leverage
        obj = 0.5 * mean + 0.5 * sims[:, j] - own_penalty * pool.own.values * rng.uniform(0.5, 1.5)
        lu = ilp.solve(obj)
        if lu:
            cands.add(lu)
    cands = list(cands)
    log(f"{len(cands)} unique candidates; simulating field of {field_n} opponent lineups ...")

    X = np.zeros((len(cands), len(pool)), np.float32)
    for r, lu in enumerate(cands):
        X[r, list(lu)] = 1
    cand_scores = X @ sims                                     # [C, S]
    field = sample_field(pool, pool.own, n=field_n, log=log).astype(np.float32)
    field_scores = field @ sims                                # [F, S]
    q999 = np.quantile(field_scores, 0.999, axis=0)
    q99 = np.quantile(field_scores, 0.99, axis=0)
    q80 = np.quantile(field_scores, 0.80, axis=0)
    fmax = field_scores.max(0)

    o = pool.own.values / 100.0
    dup = np.array([contest_size * np.prod(o[list(lu)]) for lu in cands])
    res = pd.DataFrame({
        "lineup": cands,
        "mean": cand_scores.mean(1),
        "median": np.median(cand_scores, 1),
        "p90": np.percentile(cand_scores, 90, 1),
        "p99": np.percentile(cand_scores, 99, 1),
        "top0.1%": (cand_scores > q999).mean(1),
        "top1%": (cand_scores > q99).mean(1),
        "cash%": (cand_scores > q80).mean(1),
        "beat_field_max%": (cand_scores > fmax).mean(1),
        "salary": X @ pool.Salary.values,
        "own_sum": X @ pool.own.values,
        "exp_dupes": dup,
    })
    # GPP score: top-0.1% rate (dominant) + top-1% rate, split by expected duplicates
    res["gpp_score"] = (res["top0.1%"] * 10 + res["top1%"]) / (1 + res.exp_dupes)
    res = res.sort_values(["gpp_score", "p99"], ascending=False).reset_index(drop=True)
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
