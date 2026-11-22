"""Download public nflverse data (stats, injuries, snaps, schedule + Vegas lines)."""
import os, sys, urllib.request

SEASON = int(os.environ.get("NFL_SEASON", "2026"))
CACHE = os.path.join(os.path.dirname(__file__), "data", "cache")
URLS = {
    "games.csv": "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
    "stats.csv": f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{SEASON}.csv",
    "injuries.csv": f"https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{SEASON}.csv",
    "snaps.csv": f"https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{SEASON}.csv",
}

def main():
    os.makedirs(CACHE, exist_ok=True)
    for fn, url in URLS.items():
        dst = os.path.join(CACHE, fn)
        print(f"fetching {url}", file=sys.stderr)
        urllib.request.urlretrieve(url, dst)
    print("done", file=sys.stderr)

if __name__ == "__main__":
    main()
