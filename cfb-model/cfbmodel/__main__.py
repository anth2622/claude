"""CLI.

  python -m cfbmodel daily [YYYY-MM-DD]   full morning run (update, grade, recalibrate, picks, reports)
  python -m cfbmodel grade                grade finished games only
  python -m cfbmodel backtest             rebuild the full walk-forward backtest from scratch
  python -m cfbmodel ratings              print current FBS power ratings
  python -m cfbmodel review               feedback-loop diagnostics (live hit rate, CLV, calibration)
"""
import datetime as dt
import sys

from . import backtest, calibrate, live, model, review, store


def main(argv):
    cmd = argv[0] if argv else "daily"
    if cmd == "daily":
        day = dt.date.fromisoformat(argv[1]) if len(argv) > 1 else None
        print(live.daily(day))
    elif cmd == "grade":
        live.grade()
    elif cmd == "backtest":
        games = store.load_games()
        rows = backtest.run(games, live.BACKTEST_START, live.today_et() - dt.timedelta(days=1),
                            calibrate.load_state().get("params"))
        backtest.save(rows)
        live.recalibrate()
    elif cmd == "ratings":
        games = store.load_games()
        abbr = {g[s]["id"]: g[s]["abbr"] for g in games for s in ("home", "away")}
        st = model.fit_state(model.Frame(games), live.today_et(), calibrate.load_state().get("params"))
        print(f"{'#':>3} {'team':6} {'Net':>6} {'Off':>6} {'Def':>6}   (points vs an average FBS team, neutral field)")
        for i, r in enumerate(model.team_table(st, abbr), 1):
            print(f"{i:3d} {r['team']:6} {r['net']:+6.1f} {r['off']:6.1f} {r['def']:6.1f}")
    elif cmd == "review":
        print(review.run())
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
