"""CLI.

  python -m nbamodel daily [YYYY-MM-DD]   full morning run (update, grade, recalibrate, picks, reports)
  python -m nbamodel grade                grade finished games only
  python -m nbamodel backtest             rebuild the full walk-forward backtest from scratch
  python -m nbamodel ratings              print current team power ratings
  python -m nbamodel review               feedback-loop diagnostics (live hit rate, CLV, calibration)
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
        rows = backtest.run(games, dt.date(2024, 11, 20), live.today_et() - dt.timedelta(days=1),
                            calibrate.load_state().get("params"))
        backtest.save(rows)
        live.recalibrate()
    elif cmd == "ratings":
        games = store.load_games()
        abbr = {g["home"]["id"]: g["home"]["abbr"] for g in games}
        st = model.fit_state(games, live.today_et(), calibrate.load_state().get("params"))
        print(f"{'team':5} {'ORtg':>6} {'DRtg':>6} {'Net':>6} {'Pace':>6}")
        for r in model.team_table(st, abbr):
            print(f"{r['team']:5} {r['ortg']:6.1f} {r['drtg']:6.1f} {r['net']:+6.1f} {r['pace']:6.1f}")
    elif cmd == "review":
        print(review.run())
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
