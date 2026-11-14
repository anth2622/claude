# Model changelog

## 2026-11-14 — v1
Ported from the NBA model. Parameters chosen on walk-forward MAE (Oct 2024 – Nov 2026, 2,244 games):

- **Ratings:** ridge on points, split into a strength rating (margin, λ=0.3) and a scoring rating (total, λ=3). One shared penalty couldn't serve both: margins wanted λ≈0.3, totals λ≈3.
- **Target:** 0.4 × box-score points + 0.6 × real points (grid 0–0.9).
- **Recency:** 60-day half-life in season, last season at 0.5 weight, 230-day offseason compressed out (grid 25–300 / 0.3–1.0 / 150–260).
- **No blowout cap.** A 28-point cap left large favorites 8 points short of the market (margin MAE 14.1 → 12.7 without it).
- **No league scoring re-anchor** (the NBA v1.1 fix): it made college totals MAE worse at every half-life tried (7–28 days).
- **QB adjustment:** 1.5 pts per adj yd/dropback, starter inferred from the last game (MAE 12.710 → 12.707, so marginal). 3.0 when the starter is entered by hand. With known starters MAE drops to 12.57.
- **No injury report:** ESPN's college injury feed is empty, so the NBA model's roster pricing has no college equivalent beyond the QB.
- **Calibration:** opening lines, no intercept, live results weighted 3×.
- **Policy:** totals need prob ≥ break-even + 0.025 and 4+ pts of edge; total line under 65 (high totals 15–24 vs openers in the backtest); spreads only with QB news entered (51% out of sample without it); both teams need 3+ games in the data; max 5 picks/day; 1 unit flat.
