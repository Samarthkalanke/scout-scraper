"""
build_db.py - Load every downloaded game into one SQLite database.

Usage:
    python build_db.py           (builds data/scout.db, then prints a check for UCR)
    python build_db.py 2463      (same, but prints the check for another team ID)

The database is rebuilt from scratch on every run, from the files in data/raw.
That keeps it simple and safe: download new games, rerun this, and the
database is up to date. Nothing is ever double-counted.

Tables:
    teams         one row per team
    players       one row per player
    games         one row per game (date, season, teams, final score)
    team_stats    one row per game + team + stat      (e.g. totalRebounds = 37)
    player_stats  one row per game + player + stat    (e.g. PTS = 36)
    shots         one row per field goal attempt, with court location
"""

import json
import sqlite3
import sys
from pathlib import Path

RAW_DIR = Path("data/raw")
DB_FILE = Path("data/scout.db")

SCHEMA = """
CREATE TABLE teams (team_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE players (athlete_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE games (
    game_id TEXT PRIMARY KEY, date TEXT, season INTEGER,
    home_id TEXT, away_id TEXT, home_score INTEGER, away_score INTEGER,
    neutral_site INTEGER
);
CREATE TABLE team_stats (
    game_id TEXT, team_id TEXT, stat TEXT, value REAL,
    PRIMARY KEY (game_id, team_id, stat)
);
CREATE TABLE player_stats (
    game_id TEXT, team_id TEXT, athlete_id TEXT, stat TEXT, value REAL,
    PRIMARY KEY (game_id, athlete_id, stat)
);
CREATE TABLE shots (
    game_id TEXT, team_id TEXT, athlete_id TEXT, period INTEGER, clock TEXT,
    x REAL, y REAL, is_three INTEGER, made INTEGER, description TEXT
);
CREATE INDEX idx_team_stats ON team_stats (team_id, stat);
CREATE INDEX idx_player_stats ON player_stats (athlete_id, stat);
CREATE INDEX idx_shots ON shots (team_id);
"""


def to_number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def split_stat(name, value):
    """Turn one ESPN stat into (name, number) pairs.

    "totalRebounds", "37"                              -> [("totalRebounds", 37)]
    "fieldGoalsMade-fieldGoalsAttempted", "26-69"      -> [("fieldGoalsMade", 26),
                                                           ("fieldGoalsAttempted", 69)]
    "FG", "12-25"                                      -> [("FGM", 12), ("FGA", 25)]
    """
    value = str(value)
    if "-" in value and not value.startswith("-"):
        made, _, attempted = value.partition("-")
        if "-" in name:
            name_made, _, name_att = name.partition("-")
        else:
            name_made, name_att = name + "M", name + "A"
        pairs = [(name_made, to_number(made)), (name_att, to_number(attempted))]
    else:
        pairs = [(name, to_number(value))]
    return [(n, v) for n, v in pairs if v is not None]


def season_of(data, date):
    year = (data.get("header", {}).get("season") or {}).get("year")
    if year:
        return int(year)
    # Fallback: a season is named by the year it ends (Nov 2025 -> 2026)
    y, m = int(date[:4]), int(date[5:7])
    return y + 1 if m >= 7 else y


def load_game(db, game_id, data):
    comp = data["header"]["competitions"][0]
    date = comp.get("date", "")[:10]
    sides = {}
    for c in comp["competitors"]:
        team = c["team"]
        db.execute("INSERT OR IGNORE INTO teams VALUES (?, ?)",
                   (team["id"], team.get("displayName", "?")))
        sides[c.get("homeAway")] = (team["id"], to_number(c.get("score")))
    home = sides.get("home", (None, None))
    away = sides.get("away", (None, None))
    db.execute(
        "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (game_id, date, season_of(data, date), home[0], away[0], home[1], away[1],
         int(bool(comp.get("neutralSite")))),
    )

    box = data.get("boxscore", {})

    for team in box.get("teams", []):
        team_id = team["team"]["id"]
        for stat in team.get("statistics", []):
            for name, value in split_stat(stat.get("name", "?"), stat.get("displayValue")):
                db.execute("INSERT OR REPLACE INTO team_stats VALUES (?, ?, ?, ?)",
                           (game_id, team_id, name, value))

    for team in box.get("players", []):
        team_id = team["team"]["id"]
        for group in team.get("statistics", []):
            labels = group.get("labels", [])
            for player in group.get("athletes", []):
                athlete = player.get("athlete", {})
                athlete_id = athlete.get("id")
                stats = player.get("stats", [])
                if not athlete_id:
                    continue
                db.execute("INSERT OR IGNORE INTO players VALUES (?, ?)",
                           (athlete_id, athlete.get("displayName", "?")))
                if not stats:  # did not play
                    continue
                db.execute("INSERT OR REPLACE INTO player_stats VALUES (?, ?, ?, ?, ?)",
                           (game_id, team_id, athlete_id, "GP", 1))
                db.execute("INSERT OR REPLACE INTO player_stats VALUES (?, ?, ?, ?, ?)",
                           (game_id, team_id, athlete_id, "GS", int(bool(player.get("starter")))))
                for label, raw in zip(labels, stats):
                    for name, value in split_stat(label, raw):
                        db.execute(
                            "INSERT OR REPLACE INTO player_stats VALUES (?, ?, ?, ?, ?)",
                            (game_id, team_id, athlete_id, name, value))

    for play in data.get("plays", []):
        if not play.get("shootingPlay"):
            continue
        text = play.get("text", "")
        if "free throw" in text.lower():
            continue
        coord = play.get("coordinate") or {}
        x, y = coord.get("x"), coord.get("y")
        if x is None or y is None or not (0 <= x <= 50) or not (-5 <= y <= 94):
            continue
        participants = play.get("participants") or [{}]
        shooter = (participants[0].get("athlete") or {}).get("id")
        is_three = play.get("pointsAttempted") == 3 or "three point" in text.lower()
        db.execute(
            "INSERT INTO shots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (game_id, (play.get("team") or {}).get("id"), shooter,
             (play.get("period") or {}).get("number"),
             (play.get("clock") or {}).get("displayValue"),
             x, y, int(is_three), int(bool(play.get("scoringPlay"))), text),
        )


def print_check(db, team_id):
    row = db.execute("SELECT name FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    if not row:
        print(f"\nTeam {team_id} is not in the database.")
        return
    print(f"\n=== Check: {row[0]} ===")

    seasons = db.execute(
        "SELECT season, COUNT(*) FROM games WHERE home_id = ? OR away_id = ? "
        "GROUP BY season ORDER BY season", (team_id, team_id)).fetchall()
    for season, games in seasons:
        pts, opp = db.execute(
            """SELECT AVG(CASE WHEN home_id = :t THEN home_score ELSE away_score END),
                      AVG(CASE WHEN home_id = :t THEN away_score ELSE home_score END)
               FROM games WHERE season = :s AND (home_id = :t OR away_id = :t)""",
            {"t": team_id, "s": season}).fetchone()
        print(f"\nSeason {season}: {games} games")
        print(f"  Points per game:   {pts:.1f}   (opponents {opp:.1f})")

        totals = dict(db.execute(
            """SELECT stat, SUM(value) FROM team_stats
               JOIN games USING (game_id)
               WHERE team_id = ? AND season = ? GROUP BY stat""",
            (team_id, season)).fetchall())

        def pct(made, att):
            if totals.get(made) is None or not totals.get(att):
                return None
            return 100 * totals[made] / totals[att]

        lines = [
            ("Field goal %", pct("fieldGoalsMade", "fieldGoalsAttempted")),
            ("3-point %", pct("threePointFieldGoalsMade", "threePointFieldGoalsAttempted")),
            ("Free throw %", pct("freeThrowsMade", "freeThrowsAttempted")),
        ]
        for label, stat in [("Rebounds per game", "totalRebounds"),
                            ("Assists per game", "assists"),
                            ("Turnovers per game", "turnovers")]:
            lines.append((label, totals[stat] / games if stat in totals else None))

        missing = False
        for label, value in lines:
            if value is None:
                missing = True
                print(f"  {label + ':':<20}(stat not found)")
            else:
                print(f"  {label + ':':<20}{value:.1f}")
        if missing:
            print("  Stat names ESPN provided:", ", ".join(sorted(totals)))


def main():
    files = sorted(RAW_DIR.glob("*.json"))
    if not files:
        print(f"No games found in {RAW_DIR}. Run fetch_season.py first.")
        sys.exit(1)

    if DB_FILE.exists():
        DB_FILE.unlink()
    db = sqlite3.connect(DB_FILE)
    db.executescript(SCHEMA)

    loaded, skipped = 0, []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            with db:  # each game is saved completely or not at all
                load_game(db, path.stem, data)
            loaded += 1
        except (KeyError, IndexError, TypeError, ValueError) as err:
            skipped.append((path.name, repr(err)))

    print(f"Built {DB_FILE}")
    for table in ("games", "teams", "players", "team_stats", "player_stats", "shots"):
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<13} {count:>8,} rows")

    no_box = db.execute(
        "SELECT COUNT(*) FROM games WHERE game_id NOT IN (SELECT game_id FROM team_stats)"
    ).fetchone()[0]
    no_shots = db.execute(
        "SELECT COUNT(*) FROM games WHERE game_id NOT IN (SELECT game_id FROM shots)"
    ).fetchone()[0]
    print(f"\nGames with no box score: {no_box}")
    print(f"Games with no shot locations: {no_shots}")

    if skipped:
        print(f"\nSkipped {len(skipped)} files that could not be read:")
        for name, err in skipped[:10]:
            print(f"  {name}: {err}")

    labels = [r[0] for r in db.execute("SELECT DISTINCT stat FROM player_stats ORDER BY stat")]
    print("\nPlayer stats available:", ", ".join(labels))

    print_check(db, sys.argv[1] if len(sys.argv) > 1 else "27")
    db.close()


if __name__ == "__main__":
    main()
