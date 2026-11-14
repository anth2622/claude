# Model changelog

## 2026-11-14 — v1
- Team ratings: ridge on points/100 with 75-day half-life, ridge λ=4 (grid-searched over half-life 30–90, λ 3–12 on walk-forward MAE).
- Player values: box-score regression only. Blending in on/off plus-minus (weights 0.3, 0.6) made walk-forward MAE worse, so it's off.
- Replacement level −2.5/100, lineup weight 0.8 (best in grid).
- Calibration fit on opening lines (closest available proxy for the morning line); live morning-line results weighted 3×.
- Policy: prob ≥ break-even + 0.025, spread edge ≥ 2 pts, total edge ≥ 3 pts, max 4 picks/day, 1 unit flat.

## 2026-11-14 — v1.1: totals over-bias fix (user flagged every game leaning over)
- **Evidence:** in Nov 2026 the model was above the closing total in 78% of games (+2.1 pts on average), while games landed 1.75 pts *below* the model. The cause was league pace falling (103.8 poss/g in Oct → 101.4 in Nov) while the 75-day team-rating window, plus last season's 102.1, lagged. Month-to-month model-minus-close drift had a 3.8 pt standard deviation.
- **Fix 1:** predicted totals are re-anchored to the league's recent scoring with a 7-day half-life (`league_half_life_days`). Grid over 7/10/14/21/35 days; 7 was best. Totals MAE 14.87 → 14.80; Nov over-lean 78% → 51% of games; monthly drift SD 3.8 → 2.6.
- **Fix 2:** calibration has no intercept. The old fit added a constant +0.029 logit toward overs (and +0.010 toward home) to every game, from historical base rates.
- **Check:** over picks and under picks now hit at similar rates (vs open, |edge| ≥ 3: overs 53.2% on n=746, unders 54.7% on n=517). Spread side split shows no margin bias (−0.01 pts).
