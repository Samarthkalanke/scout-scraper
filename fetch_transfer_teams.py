"""
fetch_transfer_teams.py - Download last season for every team a transfer came from.

Usage:
    python fetch_transfer_teams.py 2027

    2027 = this season. Downloads each old team's 2025-26 season.

Needs:
    data/rosters_2027.csv   (made by label_rosters.py)

How it finds the old teams:
  1. "Transfer ... partial" players: we already know their old team's ESPN ID.
  2. "No data" players with a previous school listed: the most recent school is
     looked up in ESPN's list of Division I teams. Schools that aren't D1
     (junior colleges, D2, overseas clubs) won't be found, and that's expected.

Afterwards, run:
    python build_db.py
    python label_rosters.py 27 2027
"""

import csv
import re
import sys
import time
from pathlib import Path

import requests

from fetch_opponents import download_season
from fetch_season import BASE, PAUSE_SECONDS

DATA_DIR = Path("data")

# School names that roster pages write differently from ESPN
ALIASES = {
    "pitt": "pittsburgh",
    "cal": "california",
    "cbu": "california baptist",
    "cal baptist": "california baptist",
    "unc charlotte": "charlotte",
    "csun": "cal state northridge",
    "uc davis": "uc davis",
    "hawaii": "hawaii",
    "ole miss": "ole miss",
    "fiu": "florida international",
    "florida international": "florida international",
    "arkansas little rock": "little rock",
    "ut martin": "ut martin",
    "kansas city": "kansas city",
    "lsu": "lsu",
}


def clean(text):
    text = text.lower().replace("&", "and").replace("st.", "state")
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^a-z ]", "", text)
    text = re.sub(r"\buniversity of\b|\buniversity\b|\bcollege\b", "", text)
    return " ".join(text.split())


def espn_d1_teams():
    """Return {cleaned name: (team_id, display name)} for every D1 team on ESPN."""
    response = requests.get(f"{BASE}/teams", params={"limit": 1000}, timeout=20)
    response.raise_for_status()
    data = response.json()
    teams = {}
    for league in data.get("sports", [{}])[0].get("leagues", []):
        for entry in league.get("teams", []):
            t = entry.get("team", entry)
            for field in ("location", "displayName", "shortDisplayName", "abbreviation", "nickname"):
                value = t.get(field)
                if value:
                    teams.setdefault(clean(value), (str(t["id"]), t.get("displayName", "?")))
    return teams


def most_recent_school(previous_schools):
    """'Temple/Cal/Washington State' -> 'Washington State' (schools are listed oldest first)."""
    parts = [p.strip() for p in re.split(r"/", previous_schools) if p.strip()]
    return parts[-1] if parts else ""


def main():
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2027
    last_season = season - 1
    roster_file = DATA_DIR / f"rosters_{season}.csv"
    if not roster_file.exists():
        print(f"Missing {roster_file}. Run label_rosters.py first.")
        sys.exit(1)

    with open(roster_file, encoding="utf-8") as f:
        players = list(csv.DictReader(f))

    to_fetch = {}  # team_id -> (team name, [players])

    for p in players:
        if p["status"] == "Transfer" and "partial" in p["note"] and p["last_season_team_id"]:
            to_fetch.setdefault(p["last_season_team_id"], (p["last_season_team"], []))[1].append(p["player"])

    print("Loading ESPN's list of Division I teams ...")
    d1 = espn_d1_teams()
    time.sleep(PAUSE_SECONDS)
    print(f"{len(d1)} names loaded.\n")

    not_d1 = []
    for p in players:
        if p["status"] != "No data" or not p["previous_schools"]:
            continue
        school = most_recent_school(p["previous_schools"])
        key = clean(school)
        key = ALIASES.get(key, key)
        match = d1.get(key)
        if match:
            to_fetch.setdefault(match[0], (match[1], []))[1].append(p["player"])
        else:
            not_d1.append(f"{p['player']} ({p['team']}): {school}")

    print(f"Downloading {last_season} seasons for {len(to_fetch)} teams:\n")
    for team_id, (name, names) in sorted(to_fetch.items(), key=lambda kv: kv[1][0]):
        try:
            total, new, skipped, failed = download_season(team_id, last_season)
        except requests.RequestException as err:
            print(f"  {name:<34} ERROR ({err})")
            continue
        extra = f", {failed} FAILED" if failed else ""
        print(f"  {name:<34} {new:>2} new, {skipped:>2} already saved{extra}   for: {', '.join(names)}")

    if not_d1:
        print(f"\nNot found among D1 teams ({len(not_d1)}). Mostly JUCO, D2, NAIA or overseas,")
        print("which ESPN doesn't cover. Check for any real D1 school written differently:")
        for line in not_d1:
            print(f"  {line}")

    print("\nNext: python build_db.py   then   python label_rosters.py 27", season)


if __name__ == "__main__":
    main()
