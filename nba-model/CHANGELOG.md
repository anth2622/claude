# Model changelog

## 2026-11-14 — v1
- Team ratings: ridge on points/100 with 75-day half-life, ridge λ=4 (grid-searched over half-life 30–90, λ 3–12 on walk-forward MAE).
- Player values: box-score regression only. Blending in on/off plus-minus (weights 0.3, 0.6) made walk-forward MAE worse, so it's off.
- Replacement level −2.5/100, lineup weight 0.8 (best in grid).
- Calibration fit on opening lines (closest available proxy for the morning line); live morning-line results weighted 3×.
- Policy: prob ≥ break-even + 0.025, spread edge ≥ 2 pts, total edge ≥ 3 pts, max 4 picks/day, 1 unit flat.
