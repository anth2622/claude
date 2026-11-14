"""Core model.

1. Team power ratings: weighted ridge regression of points scored on offense/defense
   team effects + home field + FCS level + rest (bye / short week), recency weighted.
   The target blends actual points with "box-score points" (points implied by yards,
   plays, first downs and turnovers), which are less noisy than the scoreboard.
   Blowout margins are capped so running up the score against weak teams doesn't
   inflate a rating.
2. Season carryover: last season's games still count, discounted (`carryover`) and
   aged as if the offseason were only a few days long. Early-season ratings lean on
   last year and give way to this year's results as they arrive.
3. Quarterback adjustment: every passer gets a value (shrunk adjusted yards per
   dropback). Tonight's expected starter (the QB who took the most snaps last game)
   is compared with the QB play the team's rating was earned with. College injury
   reports aren't published, so this is the lineup signal available pre-game.
4. Output: expected home margin and game total, which the calibration turns into
   cover probabilities against the market.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from .store import FBS_CONFS

DEFAULT_PARAMS = {
    "half_life_days": 60.0,       # recency weighting within the season
    "carryover": 0.5,             # weight multiplier per season back
    "offseason_days": 230.0,      # offseason compressed out of the age of old games
    "ridge_lambda": 0.3,          # shrinkage of team strength (margin), in game-weights
    "ridge_lambda_total": 3.0,    # shrinkage of team scoring tendency (totals)
    "xpts_weight": 0.4,           # target = (1-w)*points + w*box-score points
    "margin_cap": 999.0,          # blowout margins beyond this count only this much (off: hurt MAE)
    "league_half_life_days": 0.0,  # >0: re-anchor scoring level to recent games (off: hurt MAE)
    "qb_weight": 1.5,             # points per unit of QB value vs baseline, starter inferred
    "qb_weight_known": 3.0,       # same, when the starter is known (manual override)
    "qb_prior_att": 150.0,        # shrinkage of QB value toward replacement (dropbacks)
    "qb_replacement": -1.0,       # replacement QB value (adj yds/dropback vs average)
}

BYE_DAYS = 12   # 12+ days since last game = coming off a bye
SHORT_DAYS = 5  # 5 or fewer = short week


def day_date(day: str) -> dt.date:
    return dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))


def is_fbs_conf(conf: str) -> bool:
    return conf in FBS_CONFS


def qb_line_value(p) -> tuple[float, float]:
    """(adjusted yards, dropbacks) for a passer row: team, aid, name, cmp, att, yds, td, int, rush_att, rush_yds."""
    att, yds, td, it, ra, ry = p[4], p[5], p[6], p[7], p[8], p[9]
    return yds + 20 * td - 45 * it + ry, att + ra


def starter_of(g: dict, team: str) -> str | None:
    best, aid = 0.0, None
    for p in g.get("passers", []):
        if p[0] == team and p[4] > best:
            best, aid = p[4], p[1]
    return aid


# ----------------------------------------------------------------- precomputed frame
class Frame:
    """All games as numpy arrays (one row per game), built once and sliced per fit."""

    def __init__(self, games: list[dict]):
        games = [g for g in games if g["home"].get("box") and g["away"].get("box")]
        self.games = games
        teams = sorted({g["home"]["id"] for g in games} | {g["away"]["id"] for g in games})
        self.teams = teams
        self.ti = {t: i for i, t in enumerate(teams)}
        n = len(games)
        self.ord = np.array([day_date(g["day"]).toordinal() for g in games])
        self.season = np.array([g["season"] for g in games])
        self.h = np.array([self.ti[g["home"]["id"]] for g in games], dtype=int)
        self.a = np.array([self.ti[g["away"]["id"]] for g in games], dtype=int)
        self.hs = np.array([g["home"]["score"] for g in games], float)
        self.as_ = np.array([g["away"]["score"] for g in games], float)
        self.neutral = np.array([bool(g.get("neutral")) for g in games])
        self.hfcs = np.array([not is_fbs_conf(g["home"]["conf"]) for g in games], float)
        self.afcs = np.array([not is_fbs_conf(g["away"]["conf"]) for g in games], float)
        bk = ["yards", "plays", "first_downs", "to"]
        self.hbox = np.array([[g["home"]["box"].get(k, 0.0) for k in bk] for g in games], float).reshape(n, len(bk))
        self.abox = np.array([[g["away"]["box"].get(k, 0.0) for k in bk] for g in games], float).reshape(n, len(bk))
        # rest: days since each team's previous game (within data), capped at 21
        last: dict[int, int] = {}
        self.hrest = np.zeros(n); self.arest = np.zeros(n)
        for i in np.argsort(self.ord, kind="stable"):
            for side, arr in ((self.h, self.hrest), (self.a, self.arest)):
                t = side[i]
                prev = last.get(t)
                arr[i] = 21 if prev is None else min(21, self.ord[i] - prev)
                last[t] = self.ord[i]
        self.idx_by_ord = {}
        for i, o in enumerate(self.ord):
            self.idx_by_ord.setdefault(int(o), []).append(i)


def rest_feats(rest: float) -> tuple[float, float]:
    return float(rest >= BYE_DAYS and rest < 21), float(rest <= SHORT_DAYS)


# ----------------------------------------------------------------- state
@dataclass
class State:
    cutoff: dt.date
    season: int
    teams: list[str]
    params: dict
    mu: float = 0.0
    off: dict = field(default_factory=dict)
    dfn: dict = field(default_factory=dict)
    hfa: float = 0.0
    fcs_off: float = 0.0
    fcs_def: float = 0.0
    bye: float = 0.0
    short: float = 0.0
    total_shift: float = 0.0
    team_last: dict = field(default_factory=dict)      # team -> last game date
    team_last_season: dict = field(default_factory=dict)
    team_fbs: dict = field(default_factory=dict)
    qb_value: dict = field(default_factory=dict)       # athlete -> adj yds/dropback vs avg
    qb_name: dict = field(default_factory=dict)
    team_last_qb: dict = field(default_factory=dict)   # team -> starter in most recent game
    team_qb_baseline: dict = field(default_factory=dict)
    qb_avg: float = 0.0


def season_for(d: dt.date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def weights(fr: Frame, mask: np.ndarray, cutoff: dt.date, P: dict, half_life: float | None = None) -> np.ndarray:
    cs = season_for(cutoff)
    gap = cs - fr.season[mask]
    age = (cutoff.toordinal() - fr.ord[mask]) - gap * P["offseason_days"]
    age = np.maximum(age, 0)
    hl = half_life or P["half_life_days"]
    return 0.5 ** (age / hl) * P["carryover"] ** gap


def fit_state(fr: Frame, cutoff: dt.date, params: dict | None = None) -> State:
    """Fit everything from games strictly before `cutoff`."""
    P = {**DEFAULT_PARAMS, **(params or {})}
    mask = fr.ord < cutoff.toordinal()
    mask &= fr.season >= season_for(cutoff) - 2
    idx = np.nonzero(mask)[0]
    w = weights(fr, mask, cutoff, P)
    keep = w > 1e-4
    idx, w = idx[keep], w[keep]
    nT = len(fr.teams)
    st = State(cutoff=cutoff, season=season_for(cutoff), teams=fr.teams, params=P)

    # ---- targets: blend of points and box-score points, blowouts capped
    hs, as_ = fr.hs[idx], fr.as_[idx]
    xb = np.vstack([fr.hbox[idx], fr.abox[idx]])
    yb = np.concatenate([hs, as_])
    Xb = np.column_stack([np.ones(len(xb)), xb])
    coef, *_ = np.linalg.lstsq(Xb * np.sqrt(np.concatenate([w, w]))[:, None],
                               yb * np.sqrt(np.concatenate([w, w])), rcond=None)
    xp = Xb @ coef
    xw = P["xpts_weight"]
    th = (1 - xw) * hs + xw * xp[:len(idx)]
    ta = (1 - xw) * as_ + xw * xp[len(idx):]
    m = th - ta
    cap = P["margin_cap"]
    excess = np.sign(m) * np.maximum(np.abs(m) - cap, 0)
    th, ta = th - excess / 2, ta + excess / 2

    # ---- design: [1, net(nT), scoring(nT), hfa, fcs_off, fcs_def, bye, short]
    # points(o vs d) = mu + (net_o - net_d)/2 + (scr_o + scr_d)/2 + ...
    # so margin depends only on net ratings and total only on scoring ratings, and
    # each gets its own ridge penalty (margins want far less shrinkage than totals).
    k = len(idx)
    ncol = 1 + 2 * nT + 5
    X = np.zeros((2 * k, ncol))
    r = np.arange(k)
    hfac = np.where(fr.neutral[idx], 0.0, 1.0)
    hb, hsr = np.array([rest_feats(x) for x in fr.hrest[idx]]).T.reshape(2, k)
    ab, asr = np.array([rest_feats(x) for x in fr.arest[idx]]).T.reshape(2, k)
    for rows, o, de, sgn, ofcs, dfcs, ob, osr in (
            (r, fr.h[idx], fr.a[idx], 0.5, fr.hfcs[idx], fr.afcs[idx], hb, hsr),
            (r + k, fr.a[idx], fr.h[idx], -0.5, fr.afcs[idx], fr.hfcs[idx], ab, asr)):
        X[rows, 0] = 1
        X[rows, 1 + o] += 0.5
        X[rows, 1 + de] -= 0.5
        X[rows, 1 + nT + o] += 0.5
        X[rows, 1 + nT + de] += 0.5
        X[rows, 1 + 2 * nT] = sgn * hfac
        X[rows, 2 + 2 * nT] = ofcs
        X[rows, 3 + 2 * nT] = dfcs
        X[rows, 4 + 2 * nT] = ob
        X[rows, 5 + 2 * nT] = osr
    y = np.concatenate([th, ta])
    ww = np.concatenate([w, w])
    XtX = X.T @ (X * ww[:, None])
    Xty = X.T @ (ww * y)
    pen = np.zeros(ncol)
    pen[1:1 + nT] = P["ridge_lambda"]
    pen[1 + nT:1 + 2 * nT] = P["ridge_lambda_total"]
    pen[1 + 2 * nT:] = 0.5
    b = np.linalg.solve(XtX + np.diag(pen), Xty)
    st.mu = float(b[0]); st.hfa = float(b[1 + 2 * nT])
    st.fcs_off, st.fcs_def = float(b[2 + 2 * nT]), float(b[3 + 2 * nT])
    st.bye, st.short = float(b[4 + 2 * nT]), float(b[5 + 2 * nT])
    for t, i in fr.ti.items():
        net, scr = float(b[1 + i]), float(b[1 + nT + i])
        st.off[t] = (net + scr) / 2   # points added to own scoring
        st.dfn[t] = (net - scr) / 2   # points taken away from opponent's scoring

    # ---- team schedule facts (from all prior games, not just the weighted window)
    allp = np.nonzero(fr.ord < cutoff.toordinal())[0]
    for i in allp:
        g = fr.games[i]
        d = day_date(g["day"])
        for side in ("home", "away"):
            t = g[side]["id"]
            st.team_fbs[t] = is_fbs_conf(g[side]["conf"])
            if t not in st.team_last or d >= st.team_last[t]:
                st.team_last[t] = d
                st.team_last_season[t] = g["season"]
                st.team_last_qb[t] = starter_of(g, t)

    # ---- league scoring drift (short half-life, in-season)
    lhl = P.get("league_half_life_days", 0.0)
    if lhl:
        lw = weights(fr, mask, cutoff, {**P, "carryover": 0.0}, lhl)[keep]  # this season only
        if lw.sum() > 0:
            pred_tot = np.array([sum(predict_pts(st, fr.teams[fr.h[i]], fr.teams[fr.a[i]], fr.neutral[i],
                                                 fr.hrest[i], fr.arest[i])) for i in idx])
            st.total_shift = float(np.sum(lw * (hs + as_ - pred_tot)) / lw.sum())

    # ---- quarterback values
    qhl = P["half_life_days"]
    qw = weights(fr, mask, cutoff, P, qhl)[keep]
    agg: dict[str, list] = {}
    team_qb_games: dict[str, list] = {}
    for wi, i in zip(qw, idx):
        g = fr.games[i]
        for p in g.get("passers", []):
            v, n = qb_line_value(p)
            a = agg.setdefault(p[1], [0.0, 0.0])
            a[0] += wi * v; a[1] += wi * n
            st.qb_name[p[1]] = p[2]
        for side in ("home", "away"):
            t = g[side]["id"]
            team_qb_games.setdefault(t, []).append((wi, [p for p in g.get("passers", []) if p[0] == t]))
    tot_v = sum(a[0] for a in agg.values()); tot_n = sum(a[1] for a in agg.values())
    st.qb_avg = tot_v / tot_n if tot_n else 6.0
    prior, repl = P["qb_prior_att"], P["qb_replacement"]
    for aid, (v, n) in agg.items():
        raw = v / n - st.qb_avg if n > 0 else repl
        st.qb_value[aid] = (raw * n + repl * prior) / (n + prior)
    for t, lst in team_qb_games.items():
        num = den = 0.0
        for wi, ps in lst:
            dropbacks = sum(qb_line_value(p)[1] for p in ps)
            if dropbacks <= 0:
                continue
            gv = sum(st.qb_value.get(p[1], repl) * qb_line_value(p)[1] for p in ps) / dropbacks
            num += wi * gv; den += wi
        st.team_qb_baseline[t] = num / den if den else 0.0
    return st


def qb_adjust(st: State, team: str, starter: str | None = None, known: bool = False) -> tuple[float, dict | None]:
    """Points adjustment for tonight's expected starter vs the team's rating baseline.

    known=False: infer the starter from the team's most recent game (pre-game info).
    known=True:  `starter` is who actually started (backtest look-ahead upper bound)."""
    k = st.params["qb_weight_known" if known else "qb_weight"]
    if not k:
        return 0.0, None
    if not known:
        if st.team_last_season.get(team) != st.season:
            return 0.0, None  # first game of the season: last year's QB may be gone
        starter = st.team_last_qb.get(team)
    if starter is None:
        return 0.0, None
    v = st.qb_value.get(starter, st.params["qb_replacement"])
    base = st.team_qb_baseline.get(team, 0.0)
    adj = k * (v - base)
    return adj, {"qb": st.qb_name.get(starter, starter), "value": round(v, 2), "baseline": round(base, 2),
                 "adj_pts": round(adj, 1)}


def predict_pts(st: State, home: str, away: str, neutral: bool, hrest: float, arest: float,
                qh: float = 0.0, qa: float = 0.0) -> tuple[float, float]:
    hfac = 0.0 if neutral else 0.5
    hfcs = 0.0 if st.team_fbs.get(home, False) else 1.0
    afcs = 0.0 if st.team_fbs.get(away, False) else 1.0
    hb, hsr = rest_feats(hrest)
    ab, asr = rest_feats(arest)
    ph = (st.mu + st.off.get(home, 0) - st.dfn.get(away, 0) + st.hfa * hfac + st.fcs_off * hfcs
          + st.fcs_def * afcs + st.bye * hb + st.short * hsr + qh)
    pa = (st.mu + st.off.get(away, 0) - st.dfn.get(home, 0) - st.hfa * hfac + st.fcs_off * afcs
          + st.fcs_def * hfcs + st.bye * ab + st.short * asr + qa)
    return ph, pa


def rest_days(st: State, team: str, day: dt.date) -> float:
    last = st.team_last.get(team)
    return 21.0 if last is None else float(min(21, (day - last).days))


def predict(st: State, home: str, away: str, day: dt.date, neutral: bool = False,
            starter_home: str | None = None, starter_away: str | None = None) -> dict:
    """starter_home/away: a known starting QB (announced, or the backtest's actual starter);
    None = infer from the team's last game."""
    hr, ar = rest_days(st, home, day), rest_days(st, away, day)
    qh, nh = qb_adjust(st, home, starter_home, starter_home is not None)
    qa, na = qb_adjust(st, away, starter_away, starter_away is not None)
    ph, pa = predict_pts(st, home, away, neutral, hr, ar, qh, qa)
    shift = st.total_shift
    return {
        "margin": ph - pa, "total": ph + pa + shift,
        "home_pts": ph + shift / 2, "away_pts": pa + shift / 2,
        "home_rest": hr, "away_rest": ar, "qb_adj_home": qh, "qb_adj_away": qa,
        "qb_notes": {"home": nh, "away": na},
    }


def team_table(st: State, abbr: dict[str, str], fbs_only: bool = True) -> list[dict]:
    rows = []
    for t in st.teams:
        if t not in st.team_last or (fbs_only and not st.team_fbs.get(t)):
            continue
        rows.append({"team": abbr.get(t, t), "off": st.mu + st.off[t], "def": st.mu - st.dfn[t],
                     "net": st.off[t] + st.dfn[t]})
    return sorted(rows, key=lambda r: -r["net"])
