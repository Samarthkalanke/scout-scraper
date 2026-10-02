"""
fetch_boxscore.py - Week 1 test: pull one game's box score from ESPN and print it.

Usage:
    python fetch_boxscore.py              (uses the default test game)
    python fetch_boxscore.py 401823267    (any ESPN men's college basketball game ID)

Note: this uses ESPN's public but undocumented JSON endpoint. It could change
without warning, which is why later weeks add validation checks.
"""

import json
import sys

import requests

# Default test game: UC Riverside at CSUN, Feb 7, 2026
DEFAULT_GAME_ID = "401823267"

SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/summary"
)


def fetch_game(game_id):
    """Request the game summary from ESPN and return it as a Python dict."""
    response = requests.get(SUMMARY_URL, params={"event": game_id}, timeout=20)
    response.raise_for_status()  # stops with an error if ESPN didn't return 200 OK
    return response.json()


def print_final_score(data):
    competitors = data["header"]["competitions"][0]["competitors"]
    print("\n=== FINAL SCORE ===")
    for team in competitors:
        name = team["team"]["displayName"]
        side = team.get("homeAway", "")
        print(f"  {name} ({side}): {team.get('score', '?')}")


def print_team_stats(data):
    print("\n=== TEAM STATS ===")
    for team in data["boxscore"]["teams"]:
        print(f"\n{team['team']['displayName']}")
        for stat in team.get("statistics", []):
            label = stat.get("label", stat.get("name"))
            print(f"  {label:<28} {stat.get('displayValue')}")


def print_player_stats(data):
    print("\n=== PLAYER STATS ===")
    for team in data["boxscore"].get("players", []):
        print(f"\n{team['team']['displayName']}")
        for group in team.get("statistics", []):
            labels = group.get("labels", [])
            print("  " + "PLAYER".ljust(24) + " ".join(l.rjust(6) for l in labels))
            for player in group.get("athletes", []):
                name = player["athlete"]["displayName"]
                stats = player.get("stats", [])
                if not stats:  # player didn't play
                    print(f"  {name:<24} DNP")
                    continue
                print(f"  {name:<24}" + " ".join(s.rjust(6) for s in stats))


def main():
    game_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GAME_ID
    print(f"Fetching ESPN game {game_id} ...")

    data = fetch_game(game_id)

    # Save the raw response so you can see everything ESPN provides
    raw_file = f"raw_{game_id}.json"
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved raw data to {raw_file}")

    print_final_score(data)
    print_team_stats(data)
    print_player_stats(data)


if __name__ == "__main__":
    main()
