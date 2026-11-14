# College football betting model

A daily, self-grading college football (FBS) spread and totals model, built the same way as
the NBA model (`nba-model/` on branch `claude/elegant-thompson-nsu4g5`). Each morning it grades
yesterday's picks, folds the results back into its calibration, and posts today's picks with a
running record.

- **Today's picks:** `reports/YYYY-MM-DD.md`
- **Running record and pick history:** [`REPORT.md`](REPORT.md)
- **Model changes and why:** [`CHANGELOG.md`](CHANGELOG.md)

## What it does

| Sharp habit | How the model does it |
|---|---|
| Own power numbers | Every FBS team, plus every FCS team that has played one, gets a strength rating (drives the spread) and a scoring rating (drives the total) from a recency-weighted ridge regression over every game since Aug 2024. The target blends real points with box-score points (yards, plays, first downs, turnovers), which are less noisy. Home field, FCS level, coming off a bye and short weeks are fitted, not assumed. |
| Carry last season, then let it go | Last season's games count at half weight, then fade as this season's results come in. |
| Price QB news before the market | Every passer gets a value (shrunk adjusted yards per dropback). The expected starter is compared with the QB play the team's rating was earned with. College teams publish no injury report, so the starter is inferred from last week's box score, and **you can enter starter news by hand** (below). |
| Respect the market | The model never bets its raw number. A logistic calibration fitted on walk-forward history shrinks its disagreement with the line into an honest cover probability. |
| Only bet real edges | Totals: probability ≥ break-even + 2.5 points **and** ≥4 points of disagreement, total under 65. Spreads: only when QB news is entered for that game. Max 5 bets a day. |
| Track closing-line value | Every pick records the line we got vs the closing line. |
| Learn from results | Every game's morning prediction is graded and fed back into the calibration at 3× the weight of backtest rows. |

## Honest expectations

Walk-forward backtest, Oct 2024 – Nov 2026, 2,244 games, refit every game day using only prior games.
Calibration fitted on 2024 only and tested on 2025–26:

- **Totals vs opening lines:** 55.1% at 3+ points of edge (606 bets), 57.4% at 4+ (385). Overs and unders hit at similar rates.
- **Spreads vs opening lines: 51%. No edge.** Public box scores alone don't beat college spreads.
- **Against closing lines, no edge in either market** (≈49–51%). Bet early in the week, at the opener or close to it, or don't bet.
- **With the actual starting QB known:** spreads improve to 53–54% against openers. That's where a spread edge lives, which is why spreads are only bet when you enter QB news.
- Most of the totals edge is openers set too far from the middle (very low totals go over, high ones under). On totals of 65+ that reverses (15–24 in the backtest), so those aren't bet.
- The market is still better than the model: margin error 12.7 points vs 12.0 for the opening line.

**55% over a long run is elite and not guaranteed.** Expect losing weeks. Judge the model on CLV
first and hit rate only after 300+ bets.

## Entering QB news

Edit `data/qb_overrides.json` with the game date, team abbreviation (as ESPN shows it) and the
starter's name, then rerun `daily`:

```json
{"2026-11-21": {"TEAM": "Starter Name", "OTHER": "Backup Name"}}
```

A name with no passing history is priced as a replacement-level QB.

## Data

ESPN's public APIs (no key): scoreboards, box scores, and DraftKings opening and closing lines.
Stored compactly in `data/games/*.jsonl.gz`.

## Commands

```
pip install -r requirements.txt
python -m cfbmodel daily        # the morning run
python -m cfbmodel review       # feedback-loop diagnostics
python -m cfbmodel ratings      # current FBS power ratings
python -m cfbmodel backtest     # rebuild the walk-forward backtest
```

## Tuning rules (for the daily automated review)

`data/model_state.json` holds the calibration (refit every run automatically), plus optional
`policy` and `params` overrides that change only with evidence:

1. Never change thresholds or params on fewer than **100 graded live picks**, or 300 live graded games for calibration questions.
2. If live CLV over the last 100 picks is negative, **tighten** `min_prob_edge` by 0.005 (max 0.05). Don't loosen thresholds unless CLV is clearly positive (>+0.5 pts) over 150+ picks.
3. If one market (spread or total) is below 50% with negative CLV over 150+ picks, drop it from `policy.markets`.
4. Parameter changes (`params`) must beat the current setting in a full `python -m cfbmodel backtest` comparison on MAE. Never chase recent results.
5. Log every change in `CHANGELOG.md` with the date, the evidence and the change.
