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

## 2026-11-15 — policy: bet threshold lowered to 52.5% (user decision)
- The old bar (break-even + 2.5% ≈ 54.9%) needed ~12 pts of disagreement at the current calibration slope, so it would almost never bet.
- New: probability ≥ 52.5% and above the price's break-even, plus ≥3 pts of edge for both spreads and totals.
- Backtest at this rule (calibration in-sample, so optimistic): spreads 220 bets, 55.0% vs open / 54.4% vs close; totals 886 bets, 53.6% vs open / 50.3% vs close. Expect about 2–3 bets a day, mostly totals. Totals are the weaker market: drop them if live CLV is negative over 150+ picks.

## 2026-11-16 — v1.2: injuries now move totals, not just spreads
- **Problem:** lineup adjustments were net-only (symmetric), so a missing scorer moved the spread but never the total. The market lowers totals when scorers sit, so the model saw fake over edges. Today all 3 official picks were overs in games with key scorers out (Curry, Murphy/Murray/Mathurin, Banchero).
- **Tried:** splitting box values into offense/defense parts. Totals MAE got worse (14.800 → 14.833 with real lineups) and the over-lean grew (59% → 70% of edges), so it's off (`lineup_affects_totals: false`).
- **Adopted:** each player's points per minute; tonight's lineup's expected scoring minus the team's usual lineup scoring, times `scoring_loss_weight`. Fitted with real lineups: the weight is 0.18 vs actual totals and 0.22 vs closing lines; set to 0.2. MAE vs closing totals 3.90 → 3.56, MAE vs actual totals unchanged (14.800 → 14.797).
- **Effect today:** GS @ NO model total 236.2 → 234.5; ORL @ BOS over dropped out of the picks.
