"""Turn raw model-vs-market edges into honest cover probabilities.

A sharp bettor respects the market: the closing line is very efficient, so the model's
raw disagreement must be shrunk. We fit, by maximum likelihood on walk-forward history,

    P(home covers line L) = sigmoid(a_s * (pred_margin + L) + b_s)
    P(over total T)       = sigmoid(a_t * (pred_total  - T) + b_t)

A slope near 0 means "the model adds nothing beyond the line"; the fitted slope is the
model's earned trust. Live graded results are appended to the history with extra weight,
so the slope and intercept move as real feedback accumulates.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

from .store import DATA

STATE_PATH = os.path.join(DATA, "model_state.json")


def _logit_fit(x: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = 1.0,
               intercept: bool = False) -> tuple[float, float]:
    """Weighted logistic regression y ~ sigmoid(a*x + b), small ridge.

    No intercept by default: a historical over/home base rate is not a reason to lean
    every game one way (it bakes in a side bias), so only the model's edge moves P."""
    if not intercept:
        a = 0.0
        for _ in range(50):
            p = 1 / (1 + np.exp(-a * x))
            g = np.sum(w * (p - y) * x) + l2 * a
            h = np.sum(w * p * (1 - p) * x * x) + l2
            step = g / h
            a -= step
            if abs(step) < 1e-10:
                break
        return float(a), 0.0
    a = b = 0.0
    for _ in range(50):
        z = a * x + b
        p = 1 / (1 + np.exp(-z))
        g = np.array([np.sum(w * (p - y) * x) + l2 * a, np.sum(w * (p - y)) + l2 * b])
        s = w * p * (1 - p)
        H = np.array([[np.sum(s * x * x) + l2, np.sum(s * x)], [np.sum(s * x), np.sum(s) + l2]])
        step = np.linalg.solve(H, g)
        a, b = a - step[0], b - step[1]
        if np.max(np.abs(step)) < 1e-8:
            break
    return float(a), float(b)


def cover_outcome(margin: float, spread_home: float) -> float | None:
    v = margin + spread_home
    return None if v == 0 else float(v > 0)


def over_outcome(total: float, line: float) -> float | None:
    return None if total == line else float(total > line)


def edges(rows: list[dict], line: str = "open"):
    """Collect (spread_edge, cover) and (total_edge, over) pairs from backtest rows."""
    S, T = [], []
    for r in rows:
        sh, tl = r.get(f"{line}_spread_home"), r.get(f"{line}_total")
        margin = r["home_score"] - r["away_score"]
        if sh is not None:
            y = cover_outcome(margin, sh)
            if y is not None:
                S.append((r["pred_margin"] + sh, y, float(r.get("weight", 1.0))))
        if tl is not None:
            y = over_outcome(r["home_score"] + r["away_score"], tl)
            if y is not None:
                T.append((r["pred_total"] - tl, y, float(r.get("weight", 1.0))))
    return S, T


def fit(rows: list[dict], line: str = "open", clip: float = 12.0) -> dict:
    S, T = edges(rows, line)
    out = {}
    for name, data in (("spread", S), ("total", T)):
        if len(data) < 50:
            out[name] = {"a": 0.0, "b": 0.0, "n": len(data)}
            continue
        x = np.clip(np.array([d[0] for d in data]), -clip, clip)
        y = np.array([d[1] for d in data]); w = np.array([d[2] for d in data])
        a, b = _logit_fit(x, y, w, l2=5.0)
        out[name] = {"a": a, "b": b, "n": len(data)}
    return out


def prob(cal: dict, market: str, edge: float, clip: float = 12.0) -> float:
    c = cal[market]
    z = c["a"] * max(-clip, min(clip, edge)) + c["b"]
    return 1 / (1 + math.exp(-z))


def breakeven(american: float | None) -> float:
    o = -110.0 if american is None else float(american)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
