"""
fetch_season.py - Download every completed game in a team's season from ESPN.

Usage:
    python fetch_season.py 27 2026      (UC Riverside, 2025-26 season)
    python fetch_season.py 27           (defaults to the 2026 season)

Notes:
- ESPN names a season by the year it ENDS: 2025-26 is "2026", 2026-27 is "2027".
- Games already downloaded are skipped, so running this again only grabs new games.
  This is what lets the reports update automatically as the season goes on.
- Each game is saved as data/raw/<game id>.json
"""

import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
RAW_DIR = Path("data/raw")
SCHEDULE_DIR = Path("data/schedules")
PAUSE_SECONDS = 1.0  # be polite: wait between requests


def get_schedule(team_id, season):
    """Return {game_id: info} for the team's regular season and postseason."""
    games = {}
    for season_type in (2, 3):  # 2 = regular season, 3 = postseason
        response = requests.get(
            f"{BASE}/teams/{team_id}/schedule",
            params={"season": season, "seasontype": season_type},
            timeout=20,
        )
        time.sleep(PAUSE_SECONDS)
        if response.status_code != 200:
            continue
        for event in response.json().get("events", []):
            comp = event["competitions"][0]
            completed = comp.get("status", {}).get("type", {}).get("completed", False)
            teams = [c["team"]["displayName"] for c in comp.get("competitors", [])]
            games[event["id"]] = {
                "date": event.get("date", "")[:10],
                "matchup": " vs ".join(teams),
                "completed": completed,
            }
    return games


def fetch_game(game_id):
    response = requests.get(f"{BASE}/summary", params={"event": game_id}, timeout=20)
    response.raise_for_status()
    return response.json()


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_season.py TEAM_ID [SEASON]")
        sys.exit(1)

    team_id = sys.argv[1]
    season = sys.argv[2] if len(sys.argv) > 2 else "2026"

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Getting schedule for team {team_id}, season {season} ...")
    games = get_schedule(team_id, season)
    if not games:
        print("No games found. Check the team ID and season.")
        sys.exit(1)

    schedule_file = SCHEDULE_DIR / f"team_{team_id}_{season}.json"
    schedule_file.write_text(json.dumps(games, indent=2), encoding="utf-8")

    completed = {gid: g for gid, g in games.items() if g["completed"]}
    print(f"{len(games)} games on the schedule, {len(completed)} completed.\n")

    new, skipped, failed = 0, 0, 0
    for game_id, info in sorted(completed.items(), key=lambda kv: kv[1]["date"]):
        out_file = RAW_DIR / f"{game_id}.json"
        if out_file.exists():
            skipped += 1
            continue
        try:
            data = fetch_game(game_id)
            out_file.write_text(json.dumps(data), encoding="utf-8")
            new += 1
            print(f"  saved  {info['date']}  {info['matchup']}")
        except requests.RequestException as err:
            failed += 1
            print(f"  FAILED {info['date']}  {info['matchup']}  ({err})")
        time.sleep(PAUSE_SECONDS)

    print(f"\nDone: {new} new, {skipped} already saved, {failed} failed.")


if __name__ == "__main__":
    main()
