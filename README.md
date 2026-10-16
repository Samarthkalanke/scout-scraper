# scout-scraper

Automated opponent scouting reports for UC Riverside men's basketball.

The system downloads box scores and play-by-play from ESPN for UCR, every opponent on
the 2026-27 schedule, and the previous schools of every transfer. It stores everything in
one database, matches each opponent's current roster to last season's stats, and builds a
scouting report (web page + PDF) for any opponent. Reports update automatically as new
games are played.

## Before each game

A couple of days before a game, run:

```
python update.py
```

This downloads any new games, rebuilds the database, re-matches the rosters, and makes a
report and PDF for every opponent UCR plays in the next 7 days. Reports land in the
`reports` folder.

Other ways to run it:

| Command | What it does |
|---|---|
| `python update.py denver` | Makes the report for one team |
| `python update.py --days 14` | Looks two weeks ahead instead of one |
| `python update.py --all` | Makes a report for every opponent |
| `python update.py --no-download` | Skips the downloads, if you're offline or just changed the report code |

Open a report with, for example: `start reports\denver-pioneers.pdf`

## What's in a report

1. **At a glance:** last season's record, how much of the team is back, and this season's record once games are played
2. **Top 3 players:** stat line, shooting splits, how each one scores, his shot profile, and rule-based labels
3. **How they score:** share of points from 2s, 3s and free throws, assisted makes, 3-point and free throw rates
4. **Shot profile:** shots by zone compared with the D1 average
5. **What decides their games:** their stats in wins vs. losses, with the three clearest differences written out
6. **Full detail:** the whole roster with per-game stats and shooting splits, the roster compared with UCR's, detailed shot zones, and recent meetings with UCR

## How it fits together

```
ESPN  ->  download games  ->  database  ->  match rosters  ->  report
```

### The scripts, in the order they run

| File | What it does |
|---|---|
| `update.py` | Runs the whole pipeline in one command (see above) |
| `fetch_season.py` | Downloads every game in one team's season, one file per game. Skips games it already has |
| `fetch_opponents.py` | Reads UCR's schedule, finds every opponent, and downloads each one's season |
| `fetch_transfer_teams.py` | Finds the old team of every transfer and downloads that team's season |
| `build_db.py` | Loads every downloaded game into the database `data/scout.db`. Rebuilds from scratch each run and prints a check against UCR's official numbers |
| `label_rosters.py` | Matches each player on the official rosters to last season's stats and labels him Returning, Transfer, or No data |
| `make_report.py` | Builds the scouting report for one opponent as a web page and a PDF |

### Supporting files

| File | What it does |
|---|---|
| `shot_zones.py` | Sorts shots into 8 court zones. Used by the report; can also run alone |
| `rosters_2027_official.csv` | Official 2026-27 rosters copied from each school's athletics site. The only hand-entered file |
| `find_player.py` | Search tool: `python find_player.py mbengue` or `python find_player.py --team howard 2026` |
| `check_sides.py` | One-time test that confirmed left and right on the shot chart |
| `fetch_boxscore.py`, `inspect_shots.py` | Early test scripts, kept for reference |

### Folders (not on GitHub; the scripts rebuild them)

| Folder | Contents |
|---|---|
| `data/raw` | One file per game, exactly as ESPN sent it |
| `data/scout.db` | The SQLite database: games, players, team stats, player stats, shots |
| `data/schedules` | Each team's schedule |
| `data/opponents_27_2027.json` | UCR's opponents with ESPN IDs and game dates |
| `data/rosters_2027.csv` | Every rostered player with his label and last-season numbers (opens in Excel) |
| `reports` | Finished reports, one web page and one PDF per opponent |

## Setting up from scratch

```
pip install requests
python fetch_season.py 27 2026
python fetch_opponents.py 27 2027 2026
python build_db.py
python label_rosters.py 27 2027
python fetch_transfer_teams.py 2027
python build_db.py
python label_rosters.py 27 2027
python update.py --all
```

ESPN team IDs: UCR is 27. Seasons are named by the year they end, so 2025-26 is `2026`.

## Keeping it current

- **New rosters:** when CSUN and Cal State Fullerton post 2026-27 rosters, add them to
  `rosters_2027_official.csv` and run `python update.py`.
- **A player shows "No data":** if his roster page lists a D1 school, check him with
  `find_player.py`. If he shows up there with games, it's a name mismatch worth fixing;
  otherwise he didn't play D1 last season.

## Notes

- Data comes from ESPN's public but undocumented endpoints, which could change.
- Every number is checked against official stats: UCR's season totals match gohighlanders.com exactly.
- Shot locations are charted by hand at each game, so zones are approximate.
- Box scores can't measure individual defense or how roles change on a new team; film fills those gaps.
