"""
update.py - One command to bring everything up to date and build the reports you need.

Usage:
    python update.py                 download new games, rebuild, and make reports for every
                                     opponent UCR plays in the next 7 days
    python update.py denver          same, but make the report for one team
    python update.py --days 14       look 14 days ahead instead of 7
    python update.py --all           make a report for every opponent
    python update.py --no-download   skip downloading (rebuild and report only, works offline)

What it runs, in order:
    1. fetch_season.py      UCR's games this season           (only new games are downloaded)
    2. fetch_opponents.py   every opponent's games this season
    3. build_db.py          rebuild the database from all downloaded games
    4. label_rosters.py     re-match rosters to stats (picks up new games for every player)
    5. make_report.py       one report (web page + PDF) per opponent
    6. build_site.py        rebuild the website folder (site/) from every report

A couple of days before each game, run:  python update.py
"""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

UCR_ID = "27"
SEASON = "2027"
DATA_DIR = Path("data")
OPP_FILE = DATA_DIR / f"opponents_{UCR_ID}_{SEASON}.json"
ROSTER_FILE = DATA_DIR / f"rosters_{SEASON}.csv"


def run(step, *args):
    """Run one of the project's scripts and stop if it fails."""
    print(f"\n=== {step} ===", flush=True)
    result = subprocess.run([sys.executable, *args])
    if result.returncode != 0:
        sys.exit(f"\nStopped: '{' '.join(args)}' failed. Fix the error above, then run update.py again.")


def teams_with_rosters():
    import csv
    if not ROSTER_FILE.exists():
        return set()
    with open(ROSTER_FILE, encoding="utf-8") as f:
        return {row["team_id"] for row in csv.DictReader(f)}


def main():
    args = sys.argv[1:]
    download = "--no-download" not in args
    make_all = "--all" in args
    days = 7
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            sys.exit("Use --days with a number, like: python update.py --days 14")
    words, skip_next = [], False
    for a in args:
        if skip_next:
            skip_next = False
        elif a == "--days":
            skip_next = True
        elif not a.startswith("--"):
            words.append(a)
    team_query = " ".join(words)

    if not OPP_FILE.exists():
        sys.exit(f"Missing {OPP_FILE}. Run: python fetch_opponents.py {UCR_ID} {SEASON} 2026")

    # 1-2. Download this season's new games
    if download:
        run("Downloading UCR's new games", "fetch_season.py", UCR_ID, SEASON)
        run("Downloading opponents' new games", "fetch_opponents.py", UCR_ID, SEASON, SEASON)
    else:
        print("Skipping downloads (--no-download).")

    # 3-4. Rebuild
    run("Rebuilding the database", "build_db.py")
    run("Matching rosters to stats", "label_rosters.py", UCR_ID, SEASON)

    # 5. Reports
    opponents = json.loads(OPP_FILE.read_text(encoding="utf-8"))
    has_roster = teams_with_rosters()

    if team_query:
        targets = [team_query]
        print(f"\nMaking the report for '{team_query}'.")
    else:
        today = date.today()
        if make_all:
            chosen = list(opponents)
        else:
            window_end = today + timedelta(days=days)
            chosen = [tid for tid, info in opponents.items()
                      if any(today <= date.fromisoformat(d) <= window_end for d in info["dates"])]
        upcoming = sorted(chosen, key=lambda tid: min(d for d in opponents[tid]["dates"]
                                                       if make_all or d >= today.isoformat()))
        targets = []
        for tid in upcoming:
            name = opponents[tid]["name"]
            if tid not in has_roster:
                print(f"\nSkipping {name}: no 2026-27 roster yet. Add it to rosters_{SEASON}_official.csv.")
                continue
            targets.append(tid)
        if not targets and not make_all:
            future = sorted((d, info["name"]) for info in opponents.values()
                            for d in info["dates"] if d >= today.isoformat())
            nxt = f" Next game: {future[0][1]} on {future[0][0]}." if future else ""
            print(f"\nNo games with a roster in the next {days} days.{nxt}")
            print("Use 'python update.py TEAM' for one team, or --days 14 to look further ahead.")

    made = []
    for target in targets:
        run(f"Report: {opponents.get(target, {}).get('name', target)}", "make_report.py", target)
        made.append(target)

    # 6. Website: rebuild the site folder from every finished report
    run("Building the website", "build_site.py")

    print("\n=== Done ===")
    if made:
        print("Reports are in the reports folder. Open one with, for example:")
        print("    start reports\\denver-pioneers.pdf")
    print("Website preview:  start site\\index.html")


if __name__ == "__main__":
    main()
