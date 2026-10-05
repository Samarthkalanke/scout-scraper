"""
fetch_opponents.py - Find every opponent on a team's schedule and download their games.

Usage:
    python fetch_opponents.py 27 2027 2026
        Read UC Riverside's 2026-27 schedule, then download each opponent's
        2025-26 season.

    python fetch_opponents.py 27 2027 2026 2025
        Same, but download two past seasons for each opponent.

    python fetch_opponents.py 27 2027 2027
        Download opponents' games from the CURRENT season. Run this during
        the season: it only fetches games that are new since the last run.

Notes:
- ESPN names a season by the year it ENDS: 2025-26 is "2026".
- Games already in data/raw are skipped, so it is safe to stop (Ctrl+C) and rerun.
- Writes the opponent list to data/opponents_<team>_<season>.json
"""

import json
import sys
import time
from pathlib import Path

import requests

from fetch_season import BASE, PAUSE_SECONDS, RAW_DIR, fetch_game, get_schedule

DATA_DIR = Path("data")


def get_opponents(team_id, season):
    """Return {opponent_id: {"name": ..., "dates": [...]}} from the team's schedule."""
    opponents = {}
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
            date = event.get("date", "")[:10]
            for comp in event["competitions"][0].get("competitors", []):
                team = comp.get("team", {})
                opp_id = str(team.get("id", ""))
                if not opp_id or opp_id == str(team_id):
                    continue
                entry = opponents.setdefault(
                    opp_id, {"name": team.get("displayName", "?"), "dates": []}
                )
                entry["dates"].append(date)
    return opponents


def download_season(team_id, season):
    """Download a team's completed games for one season. Returns (new, skipped, failed)."""
    games = get_schedule(team_id, season)
    completed = {gid: g for gid, g in games.items() if g["completed"]}
    new, skipped, failed = 0, 0, 0
    for game_id in completed:
        out_file = RAW_DIR / f"{game_id}.json"
        if out_file.exists():
            skipped += 1
            continue
        try:
            data = fetch_game(game_id)
            out_file.write_text(json.dumps(data), encoding="utf-8")
            new += 1
        except requests.RequestException:
            failed += 1
        time.sleep(PAUSE_SECONDS)
    return len(games), new, skipped, failed


def main():
    if len(sys.argv) < 4:
        print("Usage: python fetch_opponents.py TEAM_ID SCHEDULE_SEASON DATA_SEASON [DATA_SEASON ...]")
        print("Example: python fetch_opponents.py 27 2027 2026")
        sys.exit(1)

    team_id = sys.argv[1]
    schedule_season = sys.argv[2]
    data_seasons = sys.argv[3:]

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading team {team_id}'s {schedule_season} schedule ...")
    opponents = get_opponents(team_id, schedule_season)
    if not opponents:
        print("No opponents found. Check the team ID and season.")
        sys.exit(1)

    out_file = DATA_DIR / f"opponents_{team_id}_{schedule_season}.json"
    out_file.write_text(json.dumps(opponents, indent=2), encoding="utf-8")
    print(f"{len(opponents)} opponents found. List saved to {out_file}\n")

    no_data = []
    for season in data_seasons:
        print(f"=== Season {season} ===")
        for opp_id, info in sorted(opponents.items(), key=lambda kv: kv[1]["dates"][0]):
            name = info["name"]
            try:
                total, new, skipped, failed = download_season(opp_id, season)
            except requests.RequestException as err:
                print(f"  {name:<36} ERROR ({err})")
                no_data.append((name, season))
                continue
            if total == 0:
                print(f"  {name:<36} no games on ESPN")
                no_data.append((name, season))
                continue
            line = f"  {name:<36} {new:>2} new, {skipped:>2} already saved"
            if failed:
                line += f", {failed} FAILED"
            print(line)
        print()

    total_files = len(list(RAW_DIR.glob("*.json")))
    print(f"Done. {total_files} games now saved in {RAW_DIR}.")
    if no_data:
        print("\nNo ESPN data for (these will need manual entry or can be skipped):")
        for name, season in no_data:
            print(f"  {name} ({season})")


if __name__ == "__main__":
    main()
