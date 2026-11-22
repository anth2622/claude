# DraftKings NFL GPP Optimizer (Classic)

A projection model plus Monte Carlo simulator and lineup optimizer for DraftKings NFL **Classic** tournaments.

## How projections are built (DK's AvgPointsPerGame is ignored)

1. **Vegas**: the spread and total give each team's implied points. Each team's simulated offensive DK output is
   anchored to it, using this season's empirical relationship (DK pts ≈ 38.4 + 1.98 × team points).
2. **Usage**: target share, carry share, yards per target, catch rate, yards per carry and TD rate per opportunity.
   They are weighted toward recent games (0.85 decay per week) and computed only from games for the player's current team.
   All are regressed toward positional priors.
3. **Injuries**: Out and Doubtful players are removed and their targets and carries go to active teammates.
   Questionable players get a 12% chance of a zero in each sim.
4. **Matchup**: each defense's pass yds/att, rush yds/carry, receiving yds/target allowed to WR, TE and RB separately,
   sack rate, INT rate and pass/rush TD split. All are regressed toward league average.
5. **Game script**: pass rate shifts with the spread before kickoff and with the score inside each sim.

## Simulation (correlated)

The game environment, team scoring, plays and pass rate are drawn first. Targets and carries are then split with a
Dirichlet-multinomial. Next come per-player yardage noise, team TDs drawn from team points and split by red-zone-weighted
opportunity, and full DK scoring including the 3-point bonuses at 100 and 300 yards. DST points come from simulated sacks,
INTs (the opposing QB's simulated INTs), fumbles, return TDs and points allowed. A QB, his receivers, the opposing
offense and the opposing DST all move together.

## Lineup selection

* Candidates are optimal lineups (HiGHS ILP) for hundreds of simulated slate outcomes, blended with the mean
  projection and adjusted for ownership leverage.
* Constraints: QB + 2 of his WR/TE + 1 bring-back, no RB in FLEX, no offensive player facing your DST,
  salary between $48,500 and $50,000.
* The opponent field is made of optimizer-built and ownership-sampled lineups. Every candidate is scored in every sim
  against that field.
* Lineups are ranked by top-0.1% rate (weighted heavily) plus top-1% rate, divided by (1 + expected duplicates).

## Usage

```bash
pip install numpy pandas highspy
python dfs/fetch_data.py                       # refresh nflverse stats/injuries/lines
python dfs/run.py --dk DKSalaries.csv --week 11 --sims 20000 --top 20
# options: --exclude "Name A,Name B"  --ownership own.csv  --contest-size 150000  --min-salary 49000
```

Outputs go to `dfs/output/`: `projections.csv`, `top_lineups.csv`, `dk_upload.csv` (bulk upload).

Data: [nflverse](https://github.com/nflverse) (play stats, official injury reports, schedule with consensus lines).
