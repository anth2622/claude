"""Pull tonight's NBA games (Vegas lines), rosters, injuries and season game logs from ESPN."""
import json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

OUT = os.path.join(os.path.dirname(__file__), "data")
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa
            if i == tries - 1:
                raise
            time.sleep(1 + i)


def main(date):
    os.makedirs(OUT, exist_ok=True)
    sb = get(f"{SITE}/scoreboard?dates={date}")
    games, teams = [], set()
    for e in sb["events"]:
        c = e["competitions"][0]
        home = next(t for t in c["competitors"] if t["homeAway"] == "home")["team"]["abbreviation"]
        away = next(t for t in c["competitors"] if t["homeAway"] == "away")["team"]["abbreviation"]
        o = (c.get("odds") or [{}])[0]
        games.append(dict(id=e["id"], date=e["date"], home=home, away=away,
                          details=o.get("details"), total=o.get("overUnder"), spread=o.get("spread")))
        teams |= {home, away}
    rosters = {t: get(f"{SITE}/teams/{t.lower()}/roster") for t in teams}
    players = []
    for t, r in rosters.items():
        for a in r["athletes"]:
            inj = a.get("injuries") or []
            players.append(dict(id=a["id"], name=a["displayName"], team=t, pos=a.get("position", {}).get("abbreviation"),
                                injury=(inj[0].get("status") if inj else ""), injury_detail=(inj[0] if inj else None)))
    with ThreadPoolExecutor(8) as ex:
        logs = list(ex.map(lambda p: get(f"{WEB}/athletes/{p['id']}/gamelog"), players))
    for p, lg in zip(players, logs):
        p["gamelog"] = lg
    try:
        inj = get(f"{SITE}/injuries")
    except Exception:  # noqa
        inj = None
    json.dump(dict(games=games, players=players, injuries=inj), open(os.path.join(OUT, f"espn_{date}.json"), "w"))
    print(f"{len(games)} games, {len(players)} players saved", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
