"""Core model.

1. Team power ratings: weighted ridge regression of points-per-100-possessions on
   offense/defense team effects + home court + back-to-back effects, recency
   weighted (exponential half-life). Pace is a separate ridge model.
2. Player values: box-score regression (team net rating per 100 on per-100 team box
   rates) gives coefficients; a player's value is those coefficients applied to his
   recency-weighted per-100 box line, shrunk toward replacement level.
3. Lineup adjustment: tonight's expected roster strength (who is available, how many
   minutes they play) minus the roster strength the team's rating was earned with.
4. Output: expected home margin and game total, which `picks.py` compares to the
   market through a calibration fitted on walk-forward history.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from .store import PLAYER_FIELDS

PF = {f: i + 4 for i, f in enumerate(PLAYER_FIELDS)}  # index into a player row
BOX_FEATS = ["pts", "fga", "fta", "fg3a", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]

DEFAULT_PARAMS = {
    "half_life_days": 75.0,     # recency weighting for team ratings
    "ridge_lambda": 4.0,         # shrinkage of team effects (in game-weights)
    "pace_lambda": 4.0,
    "player_half_life_days": 90.0,
    "player_prior_minutes": 400.0,
    "replacement_value": -2.5,  # pts/100 relative to average, for a replacement player
    "lineup_weight": 0.8,       # how much of the computed lineup delta to apply
    "league_half_life_days": 7.0,  # >0: re-anchor league scoring level/pace to recent games
    "onoff_weight": 0.0,        # blend of shrunk on/off plus-minus into player value
    "onoff_prior_poss": 3000.0,
    "recent_games_for_minutes": 10,
}


def day_date(day: str) -> dt.date:
    return dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))


def possessions(b: dict) -> float:
    return b["fga"] - b["oreb"] + b["tov"] + 0.44 * b["fta"]


def game_poss(g: dict) -> float | None:
    hb, ab = g["home"].get("box"), g["away"].get("box")
    if not hb or not ab or not hb.get("fga") or not ab.get("fga"):
        return None
    return (possessions(hb) + possessions(ab)) / 2


# ----------------------------------------------------------------- rest helpers
def last_game_days(games: list[dict]) -> dict[str, list[dt.date]]:
    out: dict[str, list[dt.date]] = {}
    for g in games:
        d = day_date(g["day"])
        for side in ("home", "away"):
            out.setdefault(g[side]["id"], []).append(d)
    for v in out.values():
        v.sort()
    return out


def rest_flags(team_days: dict, team: str, day: dt.date) -> tuple[int, int]:
    """(back_to_back, days_since_last capped at 5) for a team playing on `day`."""
    prev = [d for d in team_days.get(team, []) if d < day]
    if not prev:
        return 0, 5
    gap = (day - prev[-1]).days
    return int(gap == 1), min(gap, 5)


# ----------------------------------------------------------------- box coefficients
def fit_box_coefs(games: list[dict]) -> np.ndarray:
    """Team net rating per 100 ~ intercept + per-100 team box rates (ridge)."""
    X, y = [], []
    for g in games:
        poss = game_poss(g)
        if not poss:
            continue
        for side, opp in (("home", "away"), ("away", "home")):
            rows = [p for p in g["players"] if p[0] == g[side]["id"]]
            if not rows:
                continue
            tot = np.array([sum(p[PF[f]] for p in rows) for f in BOX_FEATS]) * 100 / poss
            X.append(np.concatenate([[1.0], tot]))
            y.append(100 * (g[side]["score"] - g[opp]["score"]) / poss)
    X, y = np.array(X), np.array(y)
    lam = np.diag([0.0] + [1.0] * len(BOX_FEATS))
    return np.linalg.solve(X.T @ X + lam, X.T @ y)


# ----------------------------------------------------------------- state
@dataclass
class State:
    cutoff: dt.date
    teams: list[str]
    mu: float = 0.0
    off: dict = field(default_factory=dict)
    dfn: dict = field(default_factory=dict)
    hca: float = 0.0
    b2b_off: float = 0.0
    b2b_def: float = 0.0
    pace0: float = 0.0
    pace: dict = field(default_factory=dict)
    total_shift: float = 0.0
    player_value: dict = field(default_factory=dict)       # athlete_id -> pts/100
    player_name: dict = field(default_factory=dict)
    player_team: dict = field(default_factory=dict)        # most recent team
    player_minutes: dict = field(default_factory=dict)     # expected minutes when playing
    player_last: dict = field(default_factory=dict)        # last date played
    team_recent_players: dict = field(default_factory=dict)  # team -> set of athletes in last N games
    team_baseline: dict = field(default_factory=dict)      # weighted avg roster strength
    team_days: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


def fit_state(games: list[dict], cutoff: dt.date, params: dict | None = None,
              box_coefs: np.ndarray | None = None) -> State:
    """Fit everything from games strictly before `cutoff`."""
    P = {**DEFAULT_PARAMS, **(params or {})}
    hist = [g for g in games if day_date(g["day"]) < cutoff and game_poss(g)]
    teams = sorted({g["home"]["id"] for g in hist} | {g["away"]["id"] for g in hist})
    ti = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    st = State(cutoff=cutoff, teams=teams, params=P)
    st.team_days = last_game_days(hist)
    if box_coefs is None:
        box_coefs = fit_box_coefs(hist)

    # ---- team efficiency + pace ridge
    ncol = 1 + 2 * n + 3
    XtX = np.zeros((ncol, ncol)); Xty = np.zeros(ncol)
    pXtX = np.zeros((n + 1, n + 1)); pXty = np.zeros(n + 1)
    hl = P["half_life_days"]
    for g in hist:
        d = day_date(g["day"])
        w = 0.5 ** ((cutoff - d).days / hl)
        poss = game_poss(g)
        h, a = g["home"]["id"], g["away"]["id"]
        hb2b, _ = rest_flags(st.team_days, h, d)
        ab2b, _ = rest_flags(st.team_days, a, d)
        hfac = 0.0 if g.get("neutral") else 0.5
        for o, de, sign, ob2b, db2b, pts in ((h, a, 1, hb2b, ab2b, g["home"]["score"]),
                                             (a, h, -1, ab2b, hb2b, g["away"]["score"])):
            x = np.zeros(ncol)
            x[0] = 1; x[1 + ti[o]] = 1; x[1 + n + ti[de]] = -1
            x[1 + 2 * n] = sign * hfac; x[2 + 2 * n] = ob2b; x[3 + 2 * n] = db2b
            yv = 100 * pts / poss
            XtX += w * np.outer(x, x); Xty += w * x * yv
        px = np.zeros(n + 1); px[0] = 1; px[1 + ti[h]] = 1; px[1 + ti[a]] = 1
        pXtX += w * np.outer(px, px); pXty += w * px * poss
    pen = np.zeros(ncol); pen[1:1 + 2 * n] = P["ridge_lambda"]; pen[1 + 2 * n:] = 0.5
    b = np.linalg.solve(XtX + np.diag(pen), Xty)
    ppen = np.zeros(n + 1); ppen[1:] = P["pace_lambda"]
    pb = np.linalg.solve(pXtX + np.diag(ppen), pXty)
    st.mu = float(b[0]); st.hca = float(b[1 + 2 * n])
    st.b2b_off, st.b2b_def = float(b[2 + 2 * n]), float(b[3 + 2 * n])
    for t, i in ti.items():
        st.off[t] = float(b[1 + i]); st.dfn[t] = float(b[1 + n + i])
        st.pace[t] = float(pb[1 + i])
    st.pace0 = float(pb[0])

    # ---- league environment (scoring level, pace) drifts faster than team strength.
    # Measure the model's recent in-sample total-points miss with a short half-life
    # and correct predicted totals by it (applied in predict()).
    lhl = P.get("league_half_life_days", 0.0)
    st.total_shift = 0.0
    if lhl:
        wsum = miss = 0.0
        for g in hist:
            d = day_date(g["day"])
            w = 0.5 ** ((cutoff - d).days / lhl)
            if w < 1e-3:
                continue
            h, a = g["home"]["id"], g["away"]["id"]
            hb2b, _ = rest_flags(st.team_days, h, d)
            ab2b, _ = rest_flags(st.team_days, a, d)
            hf = 0.0 if g.get("neutral") else 0.5
            ph = st.mu + st.off[h] - st.dfn[a] + st.hca * hf + st.b2b_off * hb2b + st.b2b_def * ab2b
            pa = st.mu + st.off[a] - st.dfn[h] - st.hca * hf + st.b2b_off * ab2b + st.b2b_def * hb2b
            pt = (ph + pa) * (st.pace0 + st.pace[h] + st.pace[a]) / 100
            wsum += w; miss += w * (g["home"]["score"] + g["away"]["score"] - pt)
        if wsum:
            st.total_shift = miss / wsum

    # ---- player values
    phl = P["player_half_life_days"]
    agg: dict[str, np.ndarray] = {}
    pmin: dict[str, float] = {}
    pposs: dict[str, float] = {}
    by_team_games: dict[str, list] = {}
    onoff: dict[str, list] = {}  # aid -> [on_pm, on_poss, off_pm, off_poss]
    for g in hist:
        d = day_date(g["day"])
        w = 0.5 ** ((cutoff - d).days / phl)
        poss = game_poss(g)
        tmargin = {g["home"]["id"]: g["home"]["score"] - g["away"]["score"],
                   g["away"]["id"]: g["away"]["score"] - g["home"]["score"]}
        tmin = {}
        for p in g["players"]:
            tmin[p[0]] = tmin.get(p[0], 0) + p[PF["min"]]
        for p in g["players"]:
            aid = p[1]
            gmin = max(tmin.get(p[0], 240) / 5, p[PF["min"]], 1)
            on_frac = p[PF["min"]] / gmin
            acc = onoff.setdefault(aid, [0.0, 0.0, 0.0, 0.0])
            acc[0] += w * p[PF["pm"]]; acc[1] += w * poss * on_frac
            acc[2] += w * (tmargin.get(p[0], 0) - p[PF["pm"]]); acc[3] += w * poss * (1 - on_frac)
            stats = np.array([p[PF[f]] for f in BOX_FEATS])
            agg[aid] = agg.get(aid, 0) + w * stats
            pmin[aid] = pmin.get(aid, 0) + w * p[PF["min"]]
            pposs[aid] = pposs.get(aid, 0) + w * poss * p[PF["min"]] / 48
            st.player_name[aid] = p[2]
            if aid not in st.player_last or d >= st.player_last[aid]:
                st.player_last[aid] = d
                st.player_team[aid] = p[0]
        for side in ("home", "away"):
            by_team_games.setdefault(g[side]["id"], []).append(g)
    prior_m, repl = P["player_prior_minutes"], P["replacement_value"]
    for aid, s in agg.items():
        raw = box_coefs[0] / 5 + float(box_coefs[1:] @ (s * 100 / max(pposs[aid], 1e-6)))
        v = (raw * pmin[aid] + repl * prior_m) / (pmin[aid] + prior_m)
        on_pm, on_poss, off_pm, off_poss = onoff[aid]
        if P["onoff_weight"] and on_poss > 0 and off_poss > 0:
            oo = 100 * on_pm / on_poss - 100 * off_pm / off_poss
            v += P["onoff_weight"] * oo * on_poss / (on_poss + P["onoff_prior_poss"])
        st.player_value[aid] = v

    # ---- expected minutes, recent rosters, baseline roster strength per team
    N = int(P["recent_games_for_minutes"])
    for t, tg in by_team_games.items():
        tg.sort(key=lambda g: g["date"] or "")
        recent = tg[-N:]
        mins: dict[str, list] = {}
        for g in recent:
            for p in g["players"]:
                if p[0] == t:
                    mins.setdefault(p[1], []).append(p[PF["min"]])
        st.team_recent_players[t] = set(mins)
        for aid, ms in mins.items():
            if st.player_team.get(aid) == t:
                st.player_minutes[aid] = float(np.mean(ms))
        # baseline = rating-weighted average roster strength over the window
        num = den = 0.0
        for g in tg:
            w = 0.5 ** ((cutoff - day_date(g["day"])).days / hl)
            num += w * roster_strength(st, [(p[1], p[PF["min"]]) for p in g["players"] if p[0] == t])
            den += w
        st.team_baseline[t] = num / den if den else 0.0
    return st


def roster_strength(st: State, lineup: list[tuple[str, float]]) -> float:
    """Net pts/100 implied by (athlete, minutes) pairs; missing minutes at replacement."""
    repl = st.params["replacement_value"]
    tot_min = sum(m for _, m in lineup)
    scale = 240 / tot_min if tot_min > 240 else 1.0
    s = sum(st.player_value.get(a, repl) * m * scale for a, m in lineup) / 48
    s += repl * max(0.0, 240 - tot_min) / 48
    return s


# ----------------------------------------------------------------- availability
STATUS_PLAY_PROB = {"out": 0.0, "doubtful": 0.2, "questionable": 0.5,
                    "day-to-day": 0.65, "probable": 0.9, "suspension": 0.0}


def expected_lineup(st: State, team: str, day: dt.date,
                    statuses: dict[str, str] | None = None,
                    actual: set[str] | None = None) -> tuple[list[tuple[str, float]], list[dict]]:
    """Expected (athlete, minutes) for tonight + notes on absences.

    statuses: athlete_id -> injury status string (from ESPN injury report).
    actual:   athletes who actually played (backtest: perfect availability info).
    """
    statuses = statuses or {}
    lineup, notes = [], []
    for aid in st.team_recent_players.get(team, ()):
        if st.player_team.get(aid) != team:
            continue  # traded away
        m = st.player_minutes.get(aid, 0.0)
        if actual is not None:
            prob = 1.0 if aid in actual else 0.0
        else:
            stt = statuses.get(aid)
            if stt is not None:
                prob = STATUS_PLAY_PROB.get(stt.lower(), 0.5)
            else:
                # not on the report: plays unless he has been missing for a while
                idle = (day - st.player_last.get(aid, day)).days
                team_games_since = sum(1 for d in st.team_days.get(team, []) if d > st.player_last.get(aid, day))
                prob = 0.0 if team_games_since >= 3 else (1.0 if idle < 10 else 0.5)
        if prob < 1 and m >= 12:
            notes.append({"player": st.player_name.get(aid, aid), "id": aid, "mpg": round(m, 1),
                          "value": round(st.player_value.get(aid, 0), 2), "play_prob": prob,
                          "status": statuses.get(aid, "inactive (recent DNPs)" if actual is None else "did not play")})
        if prob > 0:
            lineup.append((aid, m * prob))
    return lineup, notes


# ----------------------------------------------------------------- prediction
def predict(st: State, home: str, away: str, day: dt.date, neutral: bool = False,
            statuses: dict | None = None, actual_home: set | None = None,
            actual_away: set | None = None) -> dict:
    hb2b, hrest = rest_flags(st.team_days, home, day)
    ab2b, arest = rest_flags(st.team_days, away, day)
    hfac = 0.0 if neutral else 0.5
    lw = st.params["lineup_weight"]
    adj = {}
    notes = {}
    for side, t, act in (("home", home, actual_home), ("away", away, actual_away)):
        lu, nt = expected_lineup(st, t, day, statuses, act)
        adj[side] = lw * (roster_strength(st, lu) - st.team_baseline.get(t, 0.0)) if lu else 0.0
        notes[side] = nt
    off_h, off_a = st.off.get(home, 0), st.off.get(away, 0)
    def_h, def_a = st.dfn.get(home, 0), st.dfn.get(away, 0)
    ppp_h = st.mu + off_h - def_a + st.hca * hfac + st.b2b_off * hb2b + st.b2b_def * ab2b + (adj["home"] - adj["away"]) / 2
    ppp_a = st.mu + off_a - def_h - st.hca * hfac + st.b2b_off * ab2b + st.b2b_def * hb2b + (adj["away"] - adj["home"]) / 2
    poss = st.pace0 + st.pace.get(home, 0) + st.pace.get(away, 0)
    shift = getattr(st, "total_shift", 0.0)
    return {
        "margin": (ppp_h - ppp_a) * poss / 100,  # home minus away
        "total": (ppp_h + ppp_a) * poss / 100 + shift,
        "home_pts": ppp_h * poss / 100 + shift / 2, "away_pts": ppp_a * poss / 100 + shift / 2,
        "poss": poss, "home_b2b": hb2b, "away_b2b": ab2b, "home_rest": hrest, "away_rest": arest,
        "lineup_adj_home": adj["home"], "lineup_adj_away": adj["away"],
        "absences": notes,
    }


def team_table(st: State, abbr: dict[str, str]) -> list[dict]:
    rows = []
    for t in st.teams:
        net = st.off[t] + st.dfn[t]
        rows.append({"team": abbr.get(t, t), "ortg": st.mu + st.off[t], "drtg": st.mu - st.dfn[t],
                     "net": net, "pace": st.pace0 + 2 * st.pace[t]})
    return sorted(rows, key=lambda r: -r["net"])
