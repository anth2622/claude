"""Matchup + Vegas driven projection model and correlated Monte Carlo simulator.

Pipeline
--------
1. Vegas: spread + total -> implied team points for every game on the slate.
2. Usage: recency-weighted target share, carry share, efficiency (yards/target,
   catch rate, yards/carry) and TD-skill for every player, computed only from
   games played for his current team, regressed toward position priors.
   Players listed Out/Doubtful are removed and their volume is redistributed
   to active teammates in proportion to their roles.
3. Matchup: each defense's pass yds/att, rush yds/carry, receiving yds/target
   allowed by position group (WR/TE/RB), sack rate and INT rate, all regressed
   toward league average and applied multiplicatively.
4. Simulation (vectorised, S sims): correlated game/team scoring environment ->
   plays & pass rate (game script from spread + realised score) -> Dirichlet-
   multinomial target / carry distribution -> per-player yardage noise ->
   TDs allocated from team points -> DK scoring (incl. 3-pt bonuses), DST.
   QB, his receivers, the opposing offence and the opposing DST are all
   correlated through the shared team draws.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data", "cache")

# nflverse -> DraftKings team abbreviations
NFLVERSE_TO_DK = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS"}

POS_PRIOR = {
    # targets-share prior, ypt, catch rate, yds/carry
    "WR": dict(ypt=8.0, cr=0.64, ypc=6.0, rz_tgt=0.85, rz_car=1.0),
    "TE": dict(ypt=7.0, cr=0.71, ypc=4.0, rz_tgt=1.20, rz_car=1.0),
    "RB": dict(ypt=5.9, cr=0.77, ypc=4.3, rz_tgt=0.70, rz_car=1.15),
    "QB": dict(ypt=6.0, cr=0.60, ypc=5.2, rz_tgt=0.5, rz_car=0.95),
}
LEAGUE_TD_PER_PT = 0.108      # offensive TDs per point scored
LEAGUE_PASS_TD_FRAC = 0.62
LEAGUE_INT_RATE = 0.022
LEAGUE_SACK_RATE = 0.068


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def dk_team(t):
    return NFLVERSE_TO_DK.get(t, t)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_data(cache=CACHE):
    stats = pd.read_csv(os.path.join(cache, "stats.csv"), low_memory=False)
    stats = stats[stats.season_type == "REG"].copy()
    games = pd.read_csv(os.path.join(cache, "games.csv"), low_memory=False)
    inj = pd.read_csv(os.path.join(cache, "injuries.csv"), low_memory=False)
    for c in ("team", "opponent_team"):
        stats[c] = stats[c].map(dk_team)
    for c in ("away_team", "home_team"):
        games[c] = games[c].map(dk_team)
    inj["team"] = inj["team"].map(dk_team)
    return stats, games, inj


def slate_lines(games, season, week, teams=None):
    """Return per-team implied totals for the given week.

    nflverse spread_line is from the HOME team's perspective (positive = home favoured).
    """
    g = games[(games.season == season) & (games.week == week)].copy()
    rows = []
    for _, r in g.iterrows():
        tot, sp = float(r.total_line), float(r.spread_line)
        home_pts, away_pts = tot / 2 + sp / 2, tot / 2 - sp / 2
        gid = f"{r.away_team}@{r.home_team}"
        rows.append(dict(team=r.home_team, opp=r.away_team, implied=home_pts, spread=-sp, total=tot, game=gid, home=1))
        rows.append(dict(team=r.away_team, opp=r.home_team, implied=away_pts, spread=sp, total=tot, game=gid, home=0))
    df = pd.DataFrame(rows)
    if teams is not None:
        df = df[df.team.isin(teams)]
    return df.set_index("team")


def avg_spread_history(games, season, week):
    g = games[(games.season == season) & (games.week < week)]
    out = {}
    for _, r in g.iterrows():
        out.setdefault(r.home_team, []).append(-r.spread_line)
        out.setdefault(r.away_team, []).append(r.spread_line)
    return {k: float(np.mean(v)) for k, v in out.items()}


# --------------------------------------------------------------------------- #
# Team + player profiles
# --------------------------------------------------------------------------- #
@dataclass
class TeamProfile:
    team: str
    plays: float
    pass_rate: float
    pass_td_frac: float
    players: pd.DataFrame = field(default_factory=pd.DataFrame)  # active skill players
    qb: str | None = None
    qb_int_rate: float = LEAGUE_INT_RATE
    sack_allowed: float = LEAGUE_SACK_RATE


def _wavg(x, w):
    w = np.asarray(w, float)
    return float(np.sum(np.asarray(x, float) * w) / max(w.sum(), 1e-9))


DK_INACTIVE = ("OUT", "O", "D", "IR", "PUP", "NA", "SUS")


def build_profiles(stats, inj, week, decay=0.85, out_statuses=("Out", "Doubtful"), extra_out=(), dk=None):
    """Build team + player usage profiles using all games before `week`.

    If a DK salary frame is given, its Status column (OUT/D/IR...) marks players inactive,
    DK-active players are kept even after a long absence, and the starting QB is the
    highest-salaried active QB on DK.
    """
    dk_out, dk_active, dk_qb = set(), set(), {}
    if dk is not None:
        for r in dk.itertuples():
            k = (r.team, norm_name(r.Name))
            (dk_out if r.dk_status in DK_INACTIVE else dk_active).add(k)
        q = dk[(dk.pos == "QB") & ~dk.dk_status.isin(DK_INACTIVE)].sort_values("Salary", ascending=False)
        for t, gq in q.groupby("team"):
            top = gq[gq.Salary == gq.Salary.iloc[0]]
            dk_qb[t] = list(top.Name)
    s = stats[(stats.week < week) & stats.player_id.notna()].copy()
    s["w"] = decay ** (s.week.max() - s.week)
    s["dropbacks"] = s.attempts.fillna(0) + s.sacks_suffered.fillna(0)

    # injuries for this week
    wk_inj = inj[(inj.week == week)]
    out_set = set(
        (r.team, norm_name(r.full_name))
        for r in wk_inj.itertuples()
        if str(r.report_status) in out_statuses
    )
    q_set = set((r.team, norm_name(r.full_name)) for r in wk_inj.itertuples() if str(r.report_status) == "Questionable")
    extra_out = {norm_name(x) for x in extra_out}

    # team-game totals
    tg = s.groupby(["team", "week"]).agg(
        tgt=("targets", "sum"), car=("carries", "sum"), att=("attempts", "sum"), db=("dropbacks", "sum"),
        ptd=("passing_tds", "sum"), rtd=("rushing_tds", "sum"), ints=("passing_interceptions", "sum"),
        sacks=("sacks_suffered", "sum"), w=("w", "first"),
    ).reset_index()
    tg["plays"] = tg.db + tg.car

    # current team for each player = team in their latest game
    s = s.sort_values("week")
    latest = s.groupby("player_id").tail(1).set_index("player_id")
    s = s[s.team.values == s.player_id.map(latest["team"]).values]

    profiles = {}
    for team, tgt in tg.groupby("team"):
        w = tgt.w.values
        plays = _wavg(tgt.plays, w)
        pr = _wavg(tgt.db / tgt.plays, w)
        tds_p, tds_r = (tgt.ptd * w).sum(), (tgt.rtd * w).sum()
        ptf = (tds_p + 10 * LEAGUE_PASS_TD_FRAC) / (tds_p + tds_r + 10)
        sack_allowed = ((tgt.sacks * w).sum() + 150 * LEAGUE_SACK_RATE) / ((tgt.db * w).sum() + 150)

        ps = s[s.team == team].merge(
            tgt[["week", "tgt", "car"]].rename(columns={"tgt": "tgt_team", "car": "car_team"}), on="week")
        recs = []
        for pid, g in ps.groupby("player_id"):
            pos = g.position.iloc[-1]
            if pos not in POS_PRIOR:
                continue
            nm = g.player_display_name.iloc[-1]
            pw = g.w.values
            pri = POS_PRIOR[pos]
            k = 1.0  # one pseudo-game of prior
            tgt_share = ((g.targets.fillna(0) * pw).sum() + k * 0.03 * g.tgt_team.mean()) / ((g.tgt_team * pw).sum() + k * g.tgt_team.mean())
            car_share = ((g.carries.fillna(0) * pw).sum() + k * 0.02 * g.car_team.mean()) / ((g.car_team * pw).sum() + k * g.car_team.mean())
            T, R, C = g.targets.fillna(0).sum(), g.receptions.fillna(0).sum(), g.carries.fillna(0).sum()
            ry, uy = g.receiving_yards.fillna(0).sum(), g.rushing_yards.fillna(0).sum()
            ypt = (ry + 40 * pri["ypt"]) / (T + 40)
            cr = (R + 40 * pri["cr"]) / (T + 40)
            ypc = (uy + 60 * pri["ypc"]) / (C + 60)
            # TD skill relative to opportunity (regressed): receiving per target, rushing per carry
            rtd_rate_lg = 0.045 if pos != "RB" else 0.03
            rec_td_skill = (g.receiving_tds.fillna(0).sum() + 40 * rtd_rate_lg) / (T + 40) / rtd_rate_lg
            car_td_lg = 0.032
            car_td_skill = (g.rushing_tds.fillna(0).sum() + 60 * car_td_lg) / (C + 60) / car_td_lg
            att_w = (g.attempts.fillna(0) * pw).sum()
            ints = g.passing_interceptions.fillna(0).sum()
            atts = g.attempts.fillna(0).sum()
            int_rate = (ints + 150 * LEAGUE_INT_RATE) / (atts + 150)
            fum = g.fumbles_lost_total.fillna(0).sum() if "fumbles_lost_total" in g else 0
            touches = R + C + (atts if pos == "QB" else 0)
            fum_rate = (fum + 0.006 * 200) / (touches + 200)
            last_wk = int(g.week.max())
            recs.append(dict(
                player_id=pid, name=nm, key=norm_name(nm), pos=pos, team=team,
                tgt_share=tgt_share, car_share=car_share, ypt=ypt, cr=cr, ypc=ypc,
                rec_td_skill=rec_td_skill, car_td_skill=car_td_skill, att_w=att_w,
                int_rate=int_rate, fum_rate=fum_rate, last_wk=last_wk, games=len(g),
            ))
        pdf = pd.DataFrame(recs)
        if pdf.empty:
            continue
        team_last = int(tgt.week.max())
        # drop players not seen recently (released / long-term IR) unless they played in 2 of team's last 4
        pdf = pdf[(pdf.last_wk >= team_last - 4) | np.array([(team, k) in dk_active for k in pdf.key], dtype=bool)]
        pdf["status"] = [
            "OUT" if ((team, k) in out_set or k in extra_out or (team, k) in dk_out)
            else ("Q" if (team, k) in q_set else "")
            for k in pdf.key
        ]
        act = pdf[pdf.status != "OUT"].copy()
        # starting QB: highest recency-weighted attempts among active QBs
        if team in dk_qb:
            cands = [norm_name(n) for n in dk_qb[team]]
            act = act[(act.pos != "QB") | act.key.isin(cands)]
            if not (act.pos == "QB").any():   # DK starter has no stats with this team: generic QB
                nm = dk_qb[team][0]
                act = pd.concat([act, pd.DataFrame([dict(
                    player_id=f"gen_{team}", name=nm, key=norm_name(nm), pos="QB", team=team, tgt_share=0.0,
                    car_share=0.07, ypt=6.0, cr=0.6, ypc=4.5, rec_td_skill=1.0, car_td_skill=1.0, att_w=1.0,
                    int_rate=0.026, fum_rate=0.006, last_wk=team_last, games=0, status="")])], ignore_index=True)
        qbs = act[act.pos == "QB"].sort_values("att_w", ascending=False)
        qb = qbs.name.iloc[0] if len(qbs) else None
        int_rate = qbs.int_rate.iloc[0] if len(qbs) else LEAGUE_INT_RATE
        # backup QBs get no volume
        if len(qbs) > 1:
            act = act[~((act.pos == "QB") & (act.name != qb))]
        # QB targets zero
        act.loc[act.pos == "QB", "tgt_share"] = 0.0
        # redistribute volume of OUT players: renormalise shares to 1
        for col in ("tgt_share", "car_share"):
            tot = act[col].sum()
            if tot > 0:
                act[col] = act[col] / tot
        profiles[team] = TeamProfile(team, plays, pr, ptf, act.reset_index(drop=True), qb, int_rate, sack_allowed)
    return profiles


def build_defense(stats, week):
    """Defensive multipliers (>1 = defense allows more than league avg)."""
    s = stats[stats.week < week].copy()
    s["dropbacks"] = s.attempts.fillna(0) + s.sacks_suffered.fillna(0)
    s["pg"] = s.position.map(lambda p: p if p in ("WR", "TE", "RB") else "OTH")
    off = s.groupby(["opponent_team", "week"]).agg(
        att=("attempts", "sum"), pyds=("passing_yards", "sum"), car=("carries", "sum"), ryds=("rushing_yards", "sum"),
        sacks=("sacks_suffered", "sum"), db=("dropbacks", "sum"), ints=("passing_interceptions", "sum"),
        ptd=("passing_tds", "sum"), rtd=("rushing_tds", "sum"),
    ).groupby(level=0).sum()
    lg = off.sum()
    rec = s.groupby(["opponent_team", "pg"]).agg(t=("targets", "sum"), y=("receiving_yards", "sum")).unstack(fill_value=0)
    lg_rec = s.groupby("pg").agg(t=("targets", "sum"), y=("receiving_yards", "sum"))
    out = {}
    for d, r in off.iterrows():
        def reg(num, den, lnum, lden, k):
            lr = lnum / lden
            return ((num + k * lr) / (den + k)) / lr
        m = dict(
            pass_ypa=reg(r.pyds, r.att, lg.pyds, lg.att, 200),
            rush_ypc=reg(r.ryds, r.car, lg.ryds, lg.car, 150),
            sack=reg(r.sacks, r.db, lg.sacks, lg.db, 250),
            ints=reg(r.ints, r.att, lg.ints, lg.att, 300),
            ptd_frac=((r.ptd + 10 * LEAGUE_PASS_TD_FRAC) / (r.ptd + r.rtd + 10)) / LEAGUE_PASS_TD_FRAC,
        )
        for pg in ("WR", "TE", "RB"):
            try:
                t, y = rec.loc[d, ("t", pg)], rec.loc[d, ("y", pg)]
            except KeyError:
                t, y = 0, 0
            m[f"ypt_{pg}"] = reg(y, t, lg_rec.loc[pg, "y"], lg_rec.loc[pg, "t"], 80)
        out[d] = m
    return out


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
def _lognorm(rng, sigma, size):
    sigma = np.asarray(sigma, float)
    return np.exp(rng.normal(0, 1, size) * sigma - 0.5 * sigma ** 2)


# Empirical (2026): offensive DK points per team-game = 38.4 + 1.98 * team points scored
DK_PER_PT = (38.4, 1.98)


def simulate(lines, profiles, defense, spread_hist, n_sims=10000, seed=7, vegas_weight=0.8, calib_iters=2):
    """Calibrated simulation: anchor each team's offensive DK output to its Vegas
    implied total (weight `vegas_weight`), keeping the usage/matchup model's
    distribution of that output among players."""
    env = {t: 1.0 for t in lines.index}
    for _ in range(calib_iters):
        pl, _, _ = _simulate(lines, profiles, defense, spread_hist, 3000, seed + 99, env)
        model_tot = pl[pl.pos != "DST"].groupby("team")["mean"].sum()
        for t, r in lines.iterrows():
            if t not in model_tot:
                continue
            target = vegas_weight * (DK_PER_PT[0] + DK_PER_PT[1] * r.implied) + (1 - vegas_weight) * model_tot[t] / env[t] ** 0.55
            env[t] = float(np.clip(env[t] * (target / model_tot[t]) ** (1 / 0.55), 0.55, 1.6))
    return _simulate(lines, profiles, defense, spread_hist, n_sims, seed, env)


def _simulate(lines, profiles, defense, spread_hist, n_sims, seed, env):
    rng = np.random.default_rng(seed)
    S = n_sims
    games = lines.groupby("game")
    team_pts = {}
    for gid, g in games:
        zg = rng.normal(0, 1, S)
        for team, r in g.iterrows():
            zt = rng.normal(0, 1, S)
            sg, st = 0.15, 0.37
            team_pts[team] = r.implied * np.exp(sg * zg + st * zt - 0.5 * (sg ** 2 + st ** 2))

    rows, sims = [], []
    qb_ints, team_db = {}, {}
    for team, r in lines.iterrows():
        prof = profiles.get(team)
        if prof is None:
            continue
        opp = r.opp
        d = defense.get(opp, {})
        P, Popp = team_pts[team], team_pts[opp]
        eff_pts = (P / r.implied)
        # --- volume & game script ---
        hist = spread_hist.get(team, 0.0)
        script_pr = prof.pass_rate + 0.009 * (r.spread - hist)          # pregame script
        live_pr = script_pr + 0.004 * np.clip(Popp - P, -21, 21)          # realised script
        live_pr = np.clip(live_pr, 0.40, 0.75)
        plays = np.maximum(45, rng.normal(prof.plays + 0.12 * (P - prof.plays * 0.36), 5.0, S))
        dropbacks = plays * live_pr
        sack_rate = LEAGUE_SACK_RATE * (prof.sack_allowed / LEAGUE_SACK_RATE) ** 0.6 * d.get("sack", 1.0) ** 0.6
        sacks = rng.binomial(np.round(dropbacks).astype(int), min(sack_rate, 0.15))
        atts = np.round(dropbacks).astype(int) - sacks
        rushes = np.round(plays * (1 - live_pr)).astype(int)
        team_db[team] = (sacks, atts)

        pl = prof.players
        n = len(pl)
        # efficiency: correlated with realised points; matchup multipliers
        pass_eff = env[team] * eff_pts ** 0.45 * _lognorm(rng, 0.12, S)
        rush_eff = env[team] * eff_pts ** 0.25 * _lognorm(rng, 0.15, S)

        # --- targets (Dirichlet-multinomial) ---
        tgt_sh = pl.tgt_share.values.copy()
        throws = np.round(atts * 0.93).astype(int)   # remainder: throwaways / spikes
        recv = tgt_sh > 0
        targets = np.zeros((n, S), int)
        if recv.sum():
            alpha = tgt_sh[recv] * 45 + 1e-3
            p = rng.dirichlet(alpha, S)
            targets[recv] = rng.multinomial(throws, p).T
        car_sh = pl.car_share.values.copy()
        carry = car_sh > 0
        carries = np.zeros((n, S), int)
        if carry.sum():
            alpha = car_sh[carry] * 35 + 1e-3
            p = rng.dirichlet(alpha, S)
            carries[carry] = rng.multinomial(rushes, p).T

        rec = rng.binomial(targets, np.clip(pl.cr.values, 0.3, 0.95)[:, None])
        ypr = (pl.ypt.values / pl.cr.values)
        pg_mult = np.array([d.get(f"ypt_{p}", 1.0) if p in ("WR", "TE", "RB") else 1.0 for p in pl.pos]) ** 0.6
        sig = 0.55 / np.sqrt(np.maximum(rec, 1))
        rec_yds = rec * ypr[:, None] * pg_mult[:, None] * pass_eff[None, :] * _lognorm(rng, sig, (n, S)) * d.get("pass_ypa", 1.0) ** 0.4
        sig_r = 0.65 / np.sqrt(np.maximum(carries, 1))
        rush_yds = carries * pl.ypc.values[:, None] * rush_eff[None, :] * _lognorm(rng, sig_r, (n, S)) * d.get("rush_ypc", 1.0) ** 0.6
        # throwaway targets that went to nobody -> no yards. QB yards = sum of receptions
        pass_yds = rec_yds.sum(0)

        # --- touchdowns ---
        td_lambda = LEAGUE_TD_PER_PT * P
        tds = rng.poisson(td_lambda)
        ptf = np.clip(prof.pass_td_frac * d.get("ptd_frac", 1.0) ** 0.5 - 0.006 * (-r.spread), 0.35, 0.85)
        ptd = rng.binomial(tds, ptf)
        rtd = tds - ptd
        posk = pl.pos.values
        w_rec = (targets + 0.6 * tgt_sh[:, None] * 30) * np.array([POS_PRIOR[p]["rz_tgt"] for p in posk])[:, None] * pl.rec_td_skill.values[:, None] ** 0.5
        w_car = (carries + 0.6 * car_sh[:, None] * 25) * np.array([POS_PRIOR[p]["rz_car"] for p in posk])[:, None] * pl.car_td_skill.values[:, None] ** 0.6
        rec_td = _alloc(rng, ptd, w_rec)
        rush_td = _alloc(rng, rtd, w_car)

        fum = rng.poisson((rec + carries) * pl.fum_rate.values[:, None])
        pts = (rec * 1.0 + rec_yds * 0.1 + rush_yds * 0.1 + 6 * (rec_td + rush_td) - fum
               + 3 * (rec_yds >= 100) + 3 * (rush_yds >= 100))

        # QB passing
        ints = rng.poisson(atts * prof.qb_int_rate * d.get("ints", 1.0) ** 0.6)
        qb_ints[team] = ints
        is_qb = (pl.name.values == prof.qb)
        if is_qb.any():
            i = int(np.argmax(is_qb))
            qb_fum = rng.poisson(0.18 * np.ones(S))
            pts[i] = pts[i] + pass_yds * 0.04 + 4 * ptd - ints - qb_fum + 3 * (pass_yds >= 300)
        for i, rr in pl.iterrows():
            rows.append(dict(name=rr["name"], key=rr.key, pos=rr.pos, team=team, opp=opp, status=rr.status,
                             tgt_share=rr.tgt_share, car_share=rr.car_share))
        sims.append(pts)

    # --- DST ---
    for team, r in lines.iterrows():
        opp = r.opp
        if opp not in team_db or team not in team_pts:
            continue
        sacks, _ = team_db[opp]
        ints = qb_ints[opp]
        fr = rng.poisson(0.55, S)
        dtd = rng.binomial(ints, 0.10) + rng.binomial(fr, 0.12) + rng.poisson(0.035, S)
        saf = rng.poisson(0.02, S)
        blk = rng.poisson(0.03, S)
        pa = np.round(team_pts[opp])
        pa_pts = np.select([pa == 0, pa <= 6, pa <= 13, pa <= 20, pa <= 27, pa <= 34], [10, 7, 4, 1, 0, -1], -4)
        pts = sacks + 2 * ints + 2 * fr + 6 * dtd + 2 * saf + 2 * blk + pa_pts
        rows.append(dict(name=team, key=f"dst_{team}", pos="DST", team=team, opp=opp, status="", tgt_share=0, car_share=0))
        sims.append(pts[None, :])

    players = pd.DataFrame(rows)
    simm = np.vstack(sims).astype(np.float32)
    players["mean"] = simm.mean(1)
    players["median"] = np.median(simm, 1)
    players["p85"] = np.percentile(simm, 85, axis=1)
    players["p95"] = np.percentile(simm, 95, axis=1)
    players["sd"] = simm.std(1)
    players.attrs["env"] = env
    return players, simm, team_pts


def _alloc(rng, counts, weights):
    """Allocate integer counts[S] across rows of weights[n, S]."""
    n, S = weights.shape
    out = np.zeros((n, S), int)
    w = weights / np.maximum(weights.sum(0, keepdims=True), 1e-9)
    mx = int(counts.max()) if counts.size else 0
    if mx == 0:
        return out
    out = rng.multinomial(counts, w.T).T
    return out
