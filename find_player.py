"""
find_player.py - Search the database for players by name, or list a team's players.

Usage:
    python find_player.py stumer             (any player whose name contains "stumer")
    python find_player.py --team howard      (everyone who played for Howard)
    python find_player.py --team howard 2026 (only the 2025-26 season)

Accents and capital letters are ignored, so "stumer" finds "Stümer".
"""

import sqlite3
import sys
import unicodedata
from pathlib import Path

DB_FILE = Path("data/scout.db")


def plain(text):
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("Usage: python find_player.py NAME   or   python find_player.py --team TEAM [SEASON]")

    db = sqlite3.connect(DB_FILE)
    db.create_function("plain", 1, plain)

    rows_sql = """
        SELECT p.name, p.athlete_id, t.name, g.season,
               SUM(CASE WHEN ps.stat = 'GP'  THEN ps.value END),
               SUM(CASE WHEN ps.stat = 'MIN' THEN ps.value END),
               SUM(CASE WHEN ps.stat = 'PTS' THEN ps.value END)
        FROM players p
        LEFT JOIN player_stats ps ON ps.athlete_id = p.athlete_id
        LEFT JOIN games g ON g.game_id = ps.game_id
        LEFT JOIN teams t ON t.team_id = ps.team_id
        WHERE {where}
        GROUP BY p.athlete_id, t.team_id, g.season
        ORDER BY t.name, g.season, 6 DESC"""

    if args[0] == "--team":
        if len(args) < 2:
            sys.exit("Usage: python find_player.py --team TEAM [SEASON]")
        where = "plain(t.name) LIKE ?"
        params = [f"%{plain(args[1])}%"]
        if len(args) > 2:
            where += " AND g.season = ?"
            params.append(int(args[2]))
    else:
        where = "plain(p.name) LIKE ?"
        params = [f"%{plain(' '.join(args))}%"]

    rows = db.execute(rows_sql.format(where=where), params).fetchall()
    if not rows:
        print("No players found.")
        return

    print(f"{'Player':<30}{'ESPN ID':>10}  {'Team':<32}{'Season':>7}{'GP':>5}{'MPG':>6}{'PPG':>6}")
    for name, aid, team, season, gp, mins, pts in rows:
        gp = int(gp or 0)
        mpg = f"{mins / gp:.1f}" if gp and mins else "-"
        ppg = f"{pts / gp:.1f}" if gp and pts is not None else "-"
        print(f"{name:<30}{aid:>10}  {(team or 'no games'):<32}{season or '':>7}{gp:>5}{mpg:>6}{ppg:>6}")
    db.close()


if __name__ == "__main__":
    main()
