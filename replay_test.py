"""
replay_test.py - Find the best k values for blending, by replaying last season.

Usage:
    python replay_test.py

The idea:
    Early in a season, a team's numbers come from only a few games, so they're
    noisy. Last season's numbers are steady but may be out of date. A blend mixes them:

        blended = (n x this season + k x last season) / (n + k)

    n is games played so far. k is how many games' worth of weight last season
    gets. For shooting percentages, n and k are counted in attempts instead of games.

    To find a good k, this script pretends 2025-26 is happening live. For every
    team with a full 2024-25 and 2025-26 in the database, it stops the clock after
    1, 3, 5, 8 and 12 games, makes a blended guess with many different k values,
    and checks each guess against what the team actually did over the REST of
    2025-26. The k with the smallest average miss wins.

    It also tests a second version where k shrinks with roster turnover
    (k x share of last season's minutes that came back), to see whether that
    predicts better.

Needs: data/scout.db (from build_db.py), with 2024-25 and 2025-26 games downloaded.
Nothing is written; it only prints.
"""

import sqlite3
import sys
from pathlib import Path

DB_FILE = Path("data/scout.db")
TEST_SEASON = 2026       # the season we "replay" (2025-26)
PRIOR_SEASON = 2025      # the season before it (2024-25)
CHECKPOINTS = [1, 3, 5, 8, 12]   # games played when we stop the clock
MIN_GAMES = 20           # a team needs this many games in both seasons to be tested
MIN_REST = 10            # and this many games left after the checkpoint to check against

# k values to try. Per-game stats: k in games. Percentages: k in attempts.
GAME_KS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 60]
ATTEMPT_KS = [0, 25, 50, 75, 100, 150, 200, 300, 400, 500, 700, 1000, 1500, 2000, 3000]

# Short names for ESPN's team stat names
KEYS = {
    "fgm": "fieldGoalsMade", "fga": "fieldGoalsAttempted",
    "tpm": "threePointFieldGoalsMade", "tpa": "threePointFieldGoalsAttempted",
    "ftm": "freeThrowsMade", "fta": "freeThrowsAttempted",
    "reb": "totalRebounds", "oreb": "offensiveRebounds",
    "ast": "assists", "to": "turnovers",
}

# ("game", label, key)            averaged per game
GAME_STATS = [
    ("game", "Points scored", "pts"),
    ("game", "Points allowed", "opp_pts"),
    ("game", "Pace (possessions)", "pace"),
    ("game", "Rebounds", "reb"),
    ("game", "Rebound margin", "reb_margin"),
    ("game", "Offensive rebounds", "oreb"),
    ("game", "Assists", "ast"),
    ("game", "Turnovers", "to"),
    ("game", "Turnovers forced", "opp_to"),
    ("game", "3PT attempts", "tpa"),
    ("game", "FT attempts", "fta"),
]

# ("pct", label, made key, attempts key)   made / attempts
PCT_STATS = [
    ("pct", "FG %", "fgm", "fga"),
    ("pct", "2PT %", "twm", "twa"),
    ("pct", "3PT %", "tpm", "tpa"),
    ("pct", "FT %", "ftm", "fta"),
    ("pct", "Opp FG %", "opp_fgm", "opp_fga"),
    ("pct", "Opp 3PT %", "opp_tpm", "opp_tpa"),
]


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------- loading

def find_teams(db):
    """Teams with enough games in both seasons."""
    counts = {}
    for team_id, season, n in db.execute(
            """SELECT team_id, season, COUNT(*) FROM (
                   SELECT home_id AS team_id, season FROM games
                   UNION ALL
                   SELECT away_id, season FROM games)
               WHERE season IN (?, ?) GROUP BY team_id, season""",
            (PRIOR_SEASON, TEST_SEASON)):
        counts.setdefault(team_id, {})[season] = n
    return [t for t, c in counts.items()
            if c.get(PRIOR_SEASON, 0) >= MIN_GAMES and c.get(TEST_SEASON, 0) >= MIN_GAMES]


def season_games(db, team_id, season):
    """One dict per game, in date order, with the team's and its opponent's numbers."""
    games = db.execute(
        """SELECT game_id, home_id, home_score, away_score FROM games
           WHERE season = ? AND (home_id = ? OR away_id = ?)
           ORDER BY date, game_id""", (season, team_id, team_id)).fetchall()
    stats = {}
    for gid, tid, stat, value in db.execute(
            """SELECT ts.game_id, ts.team_id, ts.stat, ts.value FROM team_stats ts
               JOIN games g USING (game_id)
               WHERE g.season = ? AND (g.home_id = ? OR g.away_id = ?)""",
            (season, team_id, team_id)):
        side = "us" if tid == team_id else "them"
        stats.setdefault(gid, {}).setdefault(side, {})[stat] = value

    out = []
    for gid, home_id, hs, as_ in games:
        if hs is None or as_ is None:
            continue
        us_raw = stats.get(gid, {}).get("us", {})
        them_raw = stats.get(gid, {}).get("them", {})
        if not all(name in us_raw and name in them_raw for name in KEYS.values()):
            continue  # box score is missing a stat; leave the game out
        mine, theirs = (hs, as_) if home_id == team_id else (as_, hs)
        g = {"pts": mine, "opp_pts": theirs}
        g.update({short: us_raw[name] for short, name in KEYS.items()})
        g.update({"opp_" + short: them_raw[name] for short, name in KEYS.items()})
        g["twm"] = g["fgm"] - g["tpm"]
        g["twa"] = g["fga"] - g["tpa"]
        g["reb_margin"] = g["reb"] - g["opp_reb"]
        # Possessions: the standard box-score estimate, averaged over both teams
        ours = g["fga"] - g["oreb"] + g["to"] + 0.475 * g["fta"]
        theirs_poss = g["opp_fga"] - g["opp_oreb"] + g["opp_to"] + 0.475 * g["opp_fta"]
        g["pace"] = (ours + theirs_poss) / 2
        out.append(g)
    return out


def continuity(db, team_id):
    """Share of last season's minutes played by players who were back this season."""
    prior_minutes = db.execute(
        """SELECT ps.athlete_id, SUM(ps.value) FROM player_stats ps JOIN games g USING (game_id)
           WHERE ps.team_id = ? AND ps.stat = 'MIN' AND g.season = ?
           GROUP BY ps.athlete_id""", (team_id, PRIOR_SEASON)).fetchall()
    back = {a for (a,) in db.execute(
        """SELECT DISTINCT ps.athlete_id FROM player_stats ps JOIN games g USING (game_id)
           WHERE ps.team_id = ? AND g.season = ?""", (team_id, TEST_SEASON))}
    total = sum(m or 0 for _, m in prior_minutes)
    returning = sum(m or 0 for a, m in prior_minutes if a in back)
    return returning / total if total else 0.0


# ---------------------------------------------------------------- the test

def checkpoints(team, stat):
    """Each moment we stop the clock. Yields (n, guess function, last season, actual rest)."""
    prior, cur = team["prior"], team["cur"]
    for n in CHECKPOINTS:
        first, rest = cur[:n], cur[n:]
        if len(rest) < MIN_REST:
            continue
        if stat[0] == "game":
            key = stat[2]
            last = mean(g[key] for g in prior)
            so_far = sum(g[key] for g in first)
            actual = mean(g[key] for g in rest)

            def guess(k, so_far=so_far, last=last, n=n):
                return (so_far + k * last) / (n + k)
        else:
            made_key, att_key = stat[2], stat[3]
            prior_att = sum(g[att_key] for g in prior)
            rest_att = sum(g[att_key] for g in rest)
            if not prior_att or not rest_att:
                continue
            last = 100 * sum(g[made_key] for g in prior) / prior_att
            actual = 100 * sum(g[made_key] for g in rest) / rest_att
            made = sum(g[made_key] for g in first)
            att = sum(g[att_key] for g in first)

            def guess(k, made=made, att=att, last=last):
                if att + k == 0:
                    return last
                return (100 * made + k * last) / (att + k)
        yield n, guess, last, actual


def evaluate(teams, stat, ks, scale_by_continuity):
    """Every miss, grouped by k (and by checkpoint)."""
    by_k = {k: [] for k in ks}
    by_n = {n: {k: [] for k in ks} for n in CHECKPOINTS}
    this_only, last_only = [], []
    for team in teams:
        c = team["continuity"]
        for n, guess, last, actual in checkpoints(team, stat):
            for k in ks:
                k_used = k * c if scale_by_continuity else k
                miss = abs(guess(k_used) - actual)
                by_k[k].append(miss)
                by_n[n][k].append(miss)
            this_only.append(abs(guess(0) - actual))
            last_only.append(abs(last - actual))
    return by_k, by_n, this_only, last_only


def best_k(by_k):
    """(average miss, k) for the k with the smallest average miss."""
    scored = [(mean(misses), k) for k, misses in by_k.items() if misses]
    return min(scored) if scored else (None, None)


def report(title, note, stats, teams, ks):
    print(f"\n{title}")
    print(f"  {note}")
    print(f"  {'Stat':<20}{'Best k':>8}{'Miss':>7}{'This season':>13}{'Last season':>13}"
          f"{'k x returning':>15}{'Miss':>7}")
    print(f"  {'':<20}{'':>8}{'':>7}{'only':>13}{'only':>13}{'best k':>15}{'':>7}")
    per_checkpoint = []
    for stat in stats:
        by_k, by_n, this_only, last_only = evaluate(teams, stat, ks, False)
        miss, k = best_k(by_k)
        if miss is None:
            print(f"  {stat[1]:<20}  (no data)")
            continue
        c_miss, c_k = best_k(evaluate(teams, stat, ks, True)[0])
        top = "+" if k == ks[-1] else ""
        c_top = "+" if c_k == ks[-1] else ""
        print(f"  {stat[1]:<20}{str(k) + top:>8}{miss:>7.2f}{mean(this_only):>13.2f}"
              f"{mean(last_only):>13.2f}{str(c_k) + c_top:>15}{c_miss:>7.2f}")
        per_checkpoint.append((stat[1], [best_k(by_n[n])[1] for n in CHECKPOINTS]))

    print("\n  Best k at each checkpoint (fixed k):")
    print(f"  {'Stat':<20}" + "".join(f"{'after ' + str(n):>10}" for n in CHECKPOINTS))
    for label, row in per_checkpoint:
        print(f"  {label:<20}" + "".join(f"{'-' if v is None else str(v):>10}" for v in row))


def main():
    if not DB_FILE.exists():
        sys.exit(f"Missing {DB_FILE}. Run build_db.py first.")
    db = sqlite3.connect(DB_FILE)

    teams = []
    for team_id in find_teams(db):
        prior = season_games(db, team_id, PRIOR_SEASON)
        cur = season_games(db, team_id, TEST_SEASON)
        if len(prior) < MIN_GAMES or len(cur) < MIN_GAMES:
            continue
        row = db.execute("SELECT name FROM teams WHERE team_id = ?", (team_id,)).fetchone()
        teams.append({"id": team_id, "name": row[0] if row else team_id,
                      "prior": prior, "cur": cur, "continuity": continuity(db, team_id)})
    db.close()

    if not teams:
        sys.exit(f"No team has {MIN_GAMES}+ games in both 2024-25 and 2025-26 in the database. "
                 "Check that the 2024-25 seasons were downloaded and build_db.py was rerun.")

    conts = [t["continuity"] for t in teams]
    print(f"Teams tested: {len(teams)} (each with {MIN_GAMES}+ games in both 2024-25 and 2025-26)")
    print("  " + ", ".join(sorted(t["name"] for t in teams)))
    print(f"Minutes returning: average {100 * mean(conts):.0f}%, "
          f"range {100 * min(conts):.0f}% to {100 * max(conts):.0f}%")
    print(f"Checkpoints: after {', '.join(map(str, CHECKPOINTS))} games, "
          "each predicting the rest of 2025-26")
    print("Miss = average distance between the guess and what actually happened. Lower is better.")
    print("'+' after a k means the biggest k tried won: last season alone was hard to beat.")
    print("'k x returning' scales k by minutes returning (a team with half its minutes back gets half the k).")

    report("PER-GAME STATS (k counted in games; miss in that stat's units)",
           "e.g. a miss of 3.10 on Points scored = off by 3.1 points per game",
           GAME_STATS, teams, GAME_KS)
    report("SHOOTING PERCENTAGES (k counted in attempts; miss in percentage points)",
           "e.g. a miss of 2.40 on 3PT % = off by 2.4 points of 3P%",
           PCT_STATS, teams, ATTEMPT_KS)


if __name__ == "__main__":
    main()
