# NBA betting model

A daily, self-grading NBA spread and totals model. It runs each morning: grades
yesterday's picks, folds the results back into its calibration, and posts today's picks
with a running record.

- **Today's picks:** `reports/YYYY-MM-DD.md`
- **Running record and pick history:** [`REPORT.md`](REPORT.md)
- **Model changes and why:** [`CHANGELOG.md`](CHANGELOG.md)

## What it does (the sharp-bettor playbook)

| Sharp habit | How the model does it |
|---|---|
| Own power numbers | Possession-based offensive, defensive and pace ratings from a recency-weighted ridge regression (75-day half-life) over every game since Oct 2024. Home court and back-to-back effects are fitted, not assumed. |
| Price injuries before the market | Every player gets a value (box-score regression, shrunk toward replacement). Tonight's expected roster from ESPN's injury report is compared with the roster the team's rating was earned with. The difference moves the number. |
| Respect the market | The model never bets its raw number. A logistic calibration fitted on walk-forward history shrinks its disagreement with the line into an honest cover probability. |
| Only bet real edges | A bet needs the cover probability ≥ break-even + 2.5 points (≈54.9% at −110) **and** ≥2 points of spread disagreement (3 for totals). Max 4 bets a day; most days have 0–2. |
| Track closing-line value | Every pick records the line we got vs the closing line. Positive CLV is the earliest reliable sign of a real edge. |
| Learn from results | Every game's morning-line prediction is graded and fed back into the calibration with 3× the weight of backtest rows, so the model's trust in itself tracks live performance. |

## Honest expectations

Walk-forward backtest, Nov 2024 – Nov 2026, about 2,600 games, refit every day using only prior games:

- **Against closing lines, no edge** (≈49–50%). NBA closing lines are very efficient, and nobody beats them with public box scores alone.
- **Against opening lines:** spreads 55.1% on 365 out-of-sample bets after calibration; totals ≈52.5%.
- **With perfect knowledge of who plays:** 57% against openers at 4+ points of edge. That's the upper bound and shows where the edge lives: lineup news priced before the market catches up.

**55% over a long run is elite and not guaranteed.** Expect losing weeks. A 55% bettor still
goes 45–55 over a 100-bet stretch about one time in five. Judge the model on CLV first and hit rate only after 300+ bets.

## Data

ESPN's public APIs (no key): scoreboards, box scores, injury report, and DraftKings opening, current and closing lines.
Stored compactly in `data/games/*.jsonl.gz`.

## Commands

```
pip install -r requirements.txt
python -m nbamodel daily        # the morning run
python -m nbamodel review       # feedback-loop diagnostics
python -m nbamodel ratings      # current power ratings
python -m nbamodel backtest     # rebuild the walk-forward backtest
```

## Tuning rules (for the daily automated review)

`data/model_state.json` holds the calibration (refit every run automatically), plus optional
`policy` and `params` overrides that change only with evidence:

1. Never change thresholds or params on fewer than **100 graded live picks**, or 300 live graded games for calibration questions.
2. If live CLV over the last 100 picks is negative, **tighten** `min_prob_edge` by 0.005 (max 0.05). Don't loosen thresholds unless CLV is clearly positive (>+0.5 pts) over 150+ picks.
3. If one market (spread or total) is below 50% with negative CLV over 150+ picks, drop it from `policy.markets`.
4. Parameter changes (`params`) must beat the current setting in a full `python -m nbamodel backtest` comparison on MAE. Never chase recent results.
5. Log every change in `CHANGELOG.md` with the date, the evidence and the change.
