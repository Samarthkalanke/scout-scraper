"""
label_rosters.py - Match this season's official rosters to last season's stats.

Usage:
    python label_rosters.py 27 2027

Needs:
    rosters_2027_official.csv     official 2026-27 rosters, copied from each school's site
    data/opponents_27_2027.json   (made by fetch_opponents.py)
    data/scout.db                 (made by build_db.py)

Writes:
    data/rosters_2027.csv         one row per player with his label and last-season stats

Labels:
    Returning   played for this team last season
    Transfer    played for a different team last season, in games we've downloaded.
                "partial" means we only have some of his games (his old team
                isn't one we downloaded), so his numbers are incomplete.
    No data     no games last season in our database: freshman, JUCO/D2/overseas
                player, or a D1 transfer from a team we haven't downloaded.

Why match by name? The official rosters come from school websites, which don't
use ESPN's player IDs. Names are cleaned up first (accents, "Jr.", nicknames)
so that "Marqui Worthy Jr." on one site matches "Marqui Worthy" on ESPN.
"""

import csv
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "scout.db"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
FULL_SEASON_GAMES = 20  # fewer games than this for a transfer = "partial"


def clean_name(name):
    """'Samuel "Tobi" Ariyibi' -> 'samuel ariyibi', 'Marqui Worthy Jr.' -> 'marqui worthy'"""
    name = re.sub(r"\(.*?\)|\".*?\"", " ", name)  # drop (TT) and "Tobi" nicknames
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z ]", "", name.lower().replace("-", ""))
    return " ".join(w for w in name.split() if w not in SUFFIXES)


def season_lines(db, athlete_id, season):
    """Each team a player played for in a season: team, games, minutes, points."""
    return db.execute(
        """SELECT ps.team_id, t.name,
                  SUM(CASE WHEN ps.stat = 'GP'  THEN ps.value END),
                  SUM(CASE WHEN ps.stat = 'MIN' THEN ps.value END),
                  SUM(CASE WHEN ps.stat = 'PTS' THEN ps.value END)
           FROM player_stats ps
           JOIN games g USING (game_id)
           JOIN teams t ON t.team_id = ps.team_id
           WHERE ps.athlete_id = ? AND g.season = ?
           GROUP BY ps.team_id ORDER BY 3 DESC""",
        (athlete_id, season)).fetchall()


def main():
    if len(sys.argv) < 3:
        print("Usage: python label_rosters.py TEAM_ID SEASON   (example: 27 2027)")
        sys.exit(1)
    team_id, season = sys.argv[1], int(sys.argv[2])
    last_season = season - 1

    roster_file = Path(f"rosters_{season}_official.csv")
    opp_file = DATA_DIR / f"opponents_{team_id}_{season}.json"
    for needed in (roster_file, opp_file, DB_FILE):
        if not needed.exists():
            print(f"Missing {needed}.")
            sys.exit(1)

    # Team names -> ESPN team IDs
    team_ids = {"UC Riverside Highlanders": "27"} if team_id == "27" else {}
    team_ids.update({info["name"]: tid for tid, info in
                     json.loads(opp_file.read_text(encoding="utf-8")).items()})

    db = sqlite3.connect(DB_FILE)

    # Every player ESPN knows about, by cleaned name
    by_name = defaultdict(list)
    for athlete_id, name in db.execute("SELECT athlete_id, name FROM players"):
        by_name[clean_name(name)].append(athlete_id)

    # Last season's players for each team, by last name (backup matching)
    def team_last_season_players(tid):
        rows = db.execute(
            """SELECT DISTINCT p.athlete_id, p.name FROM player_stats ps
               JOIN games g USING (game_id) JOIN players p USING (athlete_id)
               WHERE ps.team_id = ? AND g.season = ?""", (tid, last_season)).fetchall()
        return [(aid, clean_name(n)) for aid, n in rows]

    with open(roster_file, encoding="utf-8") as f:
        roster = list(csv.DictReader(f))

    teams = defaultdict(list)
    for row in roster:
        teams[row["team"]].append(row)

    out_rows = []
    print(f"{'Team':<36}{'Roster':>7}{'Return':>8}{'Transf':>8}{'NoData':>8}{'Min. returning':>16}")

    for team, players in teams.items():
        tid = team_ids.get(team)
        if not tid:
            print(f"{team:<36} (team name not found in {opp_file.name})")
            continue
        last_year = team_last_season_players(tid)
        counts = defaultdict(int)
        returning_minutes = 0

        for p in players:
            cleaned = clean_name(p["name"])
            candidates = by_name.get(cleaned, [])
            note = ""

            # Backup: same last name + same first initial on this team last season
            if not candidates and cleaned:
                parts = cleaned.split()
                candidates = [aid for aid, n in last_year
                              if n.split()[-1] == parts[-1] and n[0] == parts[0][0]]
                if candidates:
                    note = "matched by last name"

            best = None  # (athlete_id, lines)
            for aid in candidates:
                lines = season_lines(db, aid, last_season)
                if any(ln[0] == tid for ln in lines):
                    best = (aid, lines)
                    break
                if lines and best is None:
                    best = (aid, lines)
            if len([a for a in candidates if season_lines(db, a, last_season)]) > 1:
                note = "more than one player with this name, check"

            athlete_id, lines = best if best else ("", [])
            own = [ln for ln in lines if ln[0] == tid]
            other = [ln for ln in lines if ln[0] != tid]
            if own:
                status, src = "Returning", own[0]
                returning_minutes += own[0][3] or 0
            elif other:
                status, src = "Transfer", other[0]
                if (src[2] or 0) < FULL_SEASON_GAMES:
                    note = (note + "; " if note else "") + "partial: old team not downloaded"
            else:
                status, src = "No data", (None, "", 0, 0, 0)
            counts[status] += 1

            games = int(src[2] or 0)
            out_rows.append({
                "team_id": tid, "team": team, "jersey": p["jersey"], "player": p["name"],
                "position": p["position"], "class": p["class"], "height": p["height"],
                "previous_schools": p["previous_schools"], "status": status,
                "espn_athlete_id": athlete_id, "last_season_team": src[1],
                "last_season_team_id": src[0] or "", "last_season_games": games,
                "last_season_ppg": round((src[4] or 0) / games, 1) if games else "",
                "last_season_mpg": round((src[3] or 0) / games, 1) if games else "",
                "note": note,
            })

        total = db.execute(
            """SELECT SUM(ps.value) FROM player_stats ps JOIN games g USING (game_id)
               WHERE ps.team_id = ? AND ps.stat = 'MIN' AND g.season = ?""",
            (tid, last_season)).fetchone()[0] or 0
        cont = f"{100 * returning_minutes / total:.0f}%" if total else "-"
        print(f"{team:<36}{len(players):>7}{counts['Returning']:>8}"
              f"{counts['Transfer']:>8}{counts['No data']:>8}{cont:>16}")

    missing = [name for name in team_ids if name not in teams]
    if missing:
        print("\nNo official roster yet (rerun once posted):", ", ".join(missing))

    out_file = DATA_DIR / f"rosters_{season}.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"\nSaved {len(out_rows)} players to {out_file}")

    partial = sorted({(r["last_season_team"], r["last_season_team_id"]) for r in out_rows
                      if "partial" in r["note"]})
    if partial:
        print(f"\nTransfers with partial stats come from {len(partial)} teams we haven't downloaded.")

    first = next(iter(teams))
    print(f"\n=== {first}, for checking ===")
    for r in out_rows:
        if r["team"] != first:
            continue
        extra = (f"  {r['last_season_team']}: {r['last_season_games']} gp, "
                 f"{r['last_season_ppg']} ppg") if r["last_season_games"] else ""
        flag = f"  [{r['note']}]" if r["note"] else ""
        print(f"  #{r['jersey']:<3} {r['player']:<26} {r['status']:<10}{extra}{flag}")
    db.close()


if __name__ == "__main__":
    main()
