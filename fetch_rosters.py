"""
fetch_rosters.py - Get this season's roster for UCR and every opponent, and label
each player as returning, transfer, or new.

Usage:
    python fetch_rosters.py 27 2027

    27   = UC Riverside's ESPN team ID
    2027 = this season (2026-27). "Last season" is taken as 2026 (2025-26).

Needs:
    data/opponents_27_2027.json   (made by fetch_opponents.py)
    data/scout.db                 (made by build_db.py)

Writes:
    data/rosters/<team id>.json   raw roster from ESPN
    data/rosters_2027.csv         one row per player, open it in Excel

Labels:
    Returning   played for this team last season
    Transfer    played for a DIFFERENT team last season, in games we have downloaded
                (stats may be partial if their old team isn't one we downloaded)
    No data     no games last season in our database: freshman, JUCO/D2 transfer,
                or a D1 transfer whose old team we haven't downloaded
"""

import csv
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests

from fetch_season import BASE, PAUSE_SECONDS

DATA_DIR = Path("data")
ROSTER_DIR = DATA_DIR / "rosters"
DB_FILE = DATA_DIR / "scout.db"


def fetch_roster(team_id):
    response = requests.get(f"{BASE}/teams/{team_id}/roster", timeout=20)
    response.raise_for_status()
    return response.json()


def roster_players(data):
    """ESPN sometimes lists athletes directly, sometimes in groups. Handle both."""
    players = []
    for entry in data.get("athletes", []):
        if "items" in entry:
            players.extend(entry["items"])
        else:
            players.append(entry)
    return players


def last_season_lines(db, athlete_id, season):
    """Return [(team_id, team_name, games, minutes, points)] for a player's season."""
    return db.execute(
        """SELECT ps.team_id, t.name,
                  SUM(CASE WHEN ps.stat = 'GP'  THEN ps.value END),
                  SUM(CASE WHEN ps.stat = 'MIN' THEN ps.value END),
                  SUM(CASE WHEN ps.stat = 'PTS' THEN ps.value END)
           FROM player_stats ps
           JOIN games g USING (game_id)
           JOIN teams t ON t.team_id = ps.team_id
           WHERE ps.athlete_id = ? AND g.season = ?
           GROUP BY ps.team_id
           ORDER BY 3 DESC""",
        (athlete_id, season)).fetchall()


def team_minutes(db, team_id, season):
    row = db.execute(
        """SELECT SUM(ps.value) FROM player_stats ps JOIN games g USING (game_id)
           WHERE ps.team_id = ? AND ps.stat = 'MIN' AND g.season = ?""",
        (team_id, season)).fetchone()
    return row[0] or 0


def main():
    if len(sys.argv) < 3:
        print("Usage: python fetch_rosters.py TEAM_ID SEASON   (example: 27 2027)")
        sys.exit(1)
    team_id, season = sys.argv[1], int(sys.argv[2])
    last_season = season - 1

    opp_file = DATA_DIR / f"opponents_{team_id}_{season}.json"
    if not opp_file.exists() or not DB_FILE.exists():
        print(f"Need {opp_file} and {DB_FILE}. Run fetch_opponents.py and build_db.py first.")
        sys.exit(1)

    teams = {team_id: "UC Riverside Highlanders"} if team_id == "27" else {team_id: team_id}
    teams.update({tid: info["name"] for tid, info in
                  json.loads(opp_file.read_text(encoding="utf-8")).items()})

    ROSTER_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_FILE)
    rows = []

    print(f"{'Team':<36}{'Roster':>7}{'Return':>8}{'Transf':>8}{'NoData':>8}"
          f"{'Min. returning':>16}")
    for tid, name in teams.items():
        try:
            data = fetch_roster(tid)
        except requests.RequestException as err:
            print(f"{name:<36} ERROR ({err})")
            continue
        (ROSTER_DIR / f"{tid}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        time.sleep(PAUSE_SECONDS)

        players = roster_players(data)
        counts = {"Returning": 0, "Transfer": 0, "No data": 0}
        returning_minutes = 0

        for p in players:
            athlete_id = str(p.get("id", ""))
            lines = last_season_lines(db, athlete_id, last_season)
            own = [ln for ln in lines if ln[0] == tid]
            other = [ln for ln in lines if ln[0] != tid]

            if own:
                status, source = "Returning", own[0]
                returning_minutes += own[0][3] or 0
            elif other:
                status, source = "Transfer", other[0]
            else:
                status, source = "No data", (None, "", 0, 0, 0)
            counts[status] += 1

            games = source[2] or 0
            rows.append({
                "team_id": tid,
                "team": name,
                "athlete_id": athlete_id,
                "player": p.get("displayName", "?"),
                "jersey": p.get("jersey", ""),
                "position": (p.get("position") or {}).get("abbreviation", ""),
                "height": p.get("displayHeight", ""),
                "class": (p.get("experience") or {}).get("displayValue", ""),
                "status": status,
                "last_season_team": source[1],
                "last_season_games": int(games),
                "last_season_ppg": round((source[4] or 0) / games, 1) if games else "",
                "last_season_mpg": round((source[3] or 0) / games, 1) if games else "",
            })

        total = team_minutes(db, tid, last_season)
        continuity = f"{100 * returning_minutes / total:.0f}%" if total else "-"
        print(f"{name:<36}{len(players):>7}{counts['Returning']:>8}"
              f"{counts['Transfer']:>8}{counts['No data']:>8}{continuity:>16}")

    out_file = DATA_DIR / f"rosters_{season}.csv"
    if rows:
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nSaved {len(rows)} players to {out_file}")

    # Print one team in full so it can be checked against the official roster
    print(f"\n=== {teams[team_id]} roster, for checking ===")
    for r in rows:
        if r["team_id"] != team_id:
            continue
        extra = f"  ({r['last_season_team']}, {r['last_season_ppg']} ppg)" if r["last_season_games"] else ""
        print(f"  #{r['jersey']:<3} {r['player']:<26} {r['class']:<10} {r['status']}{extra}")
    db.close()


if __name__ == "__main__":
    main()
