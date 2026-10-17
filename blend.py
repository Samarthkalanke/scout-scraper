"""
blend.py - Mix this season's numbers with last season's.

Early in a season, a team's numbers come from only a few games, so one hot or
cold night swings them a lot. Last season's numbers are steadier but may be out
of date. A blend gives last season the weight of k games (or k shot attempts):

    per-game stat:   (this season's total + k x last season's average) / (games + k)
    percentage:      (this season's makes + k x last season's rate) / (attempts + k)

As games pile up, this season's share grows and last season fades out on its own.

The k values come from replay_test.py (Oct 16, 2026): it replayed 2025-26 for 21
teams and picked the k that best predicted the rest of each team's season.
Small k = this season takes over fast (style stats like pace and 3PT attempts).
Big k = last season holds on (noisy stats like rebounding and opponent FG%).

To retune, rerun replay_test.py and change the numbers below. Every script that
blends imports them from here.

Try it:  python blend.py     (prints a worked example)
"""

# k counted in GAMES (per-game stats)
K_GAMES = {
    "three_a": 3,     # 3PT attempts
    "pace": 5,
    "ast": 5,
    "fta": 6,         # free throw attempts
    "pts": 8,
    "opp_pts": 8,
    "opp_to": 10,     # turnovers forced
    "to": 15,
    "oreb": 15,
    "reb_margin": 15,
    "reb": 30,
    "opp_reb": 30,    # not tested directly; uses the rebounds value
}

# k counted in ATTEMPTS (percentages)
K_ATTEMPTS = {
    "ft": 150,
    "three": 300,
    "opp_three": 300,
    "two": 500,
    "fg": 700,
    "opp_fg": 2000,
}


def per_game(this_total, games, last_avg, k):
    """Blended per-game average. With no last-season number, it's just this season's average."""
    if last_avg is None:
        return this_total / games if games else None
    return (this_total + k * last_avg) / (games + k)


def percentage(made, attempts, last_pct, k):
    """Blended percentage (0-100). last_pct is also 0-100."""
    if last_pct is None:
        return 100 * made / attempts if attempts else None
    return (100 * made + k * last_pct) / (attempts + k)


def this_season_weight(amount, k):
    """How much of the blend comes from this season (0 to 1). amount = games or attempts."""
    return amount / (amount + k) if amount + k else 1.0


# ---------------------------------------------------------------- win/loss trends

# Last season's games, all together, count like this many games of this season.
# Each of last season's games gets weight K_TRENDS / (games last season); each of
# this season's games gets weight 1. Close to the team k values for points (8)
# and turnovers forced (10). Not tested separately.
K_TRENDS = 10


# ---------------------------------------------------------------- players

# k in GAMES for one player's per-game stats. The replay test tuned team stats,
# not players, so these borrow the team values. Two rules keep them sensible:
#   - shooting k is in games too, turned into attempts with the player's own
#     attempts per game last season (a 3-a-game shooter and a 7-a-game shooter
#     both get about 13 games of history), matching the team values
#     (300 3PA / ~23 a game = 13 games; 150 FTA / ~20 a game = 8 games)
#   - k never exceeds the games he actually played last season, so 4 games of
#     history can't outweigh 10 games this season
PLAYER_K = {
    "MIN": 5,     # role can change fast on a new team
    "PTS": 8,
    "AST": 5,
    "REB": 15,
    "OREB": 15,
    "STL": 10,
    "BLK": 10,
    "TO": 15,
    "PF": 10,
    "2PA": 5,     # attempts per game: style, shows up fast
    "3PTA": 3,
    "FTA": 6,
}
PLAYER_SHOOTING_K_GAMES = {   # made / attempted key, k in games
    ("2PM", "2PA"): 13,
    ("3PTM", "3PTA"): 13,
    ("FTM", "FTA"): 8,
}


def _with_twos(t):
    t = dict(t)
    t["2PM"] = t.get("FGM", 0) - t.get("3PTM", 0)
    t["2PA"] = t.get("FGA", 0) - t.get("3PTA", 0)
    return t


def player(last, cur):
    """Blend one player's season totals. Returns (totals, this season's weight on scoring).

    The returned totals look like a normal season line, so the report can use them
    as-is: GP is all his games in both seasons, and every other number is the
    blended per-game value times GP. Percentages come out blended too.
    """
    last_gp, cur_gp = last.get("GP", 0), cur.get("GP", 0)
    if not cur_gp:
        return dict(last), 0.0
    if not last_gp:
        return dict(cur), 1.0
    last, cur = _with_twos(last), _with_twos(cur)
    games = last_gp + cur_gp
    out = {"GP": games}
    last_avg = lambda key: last.get(key, 0) / last_gp

    for key, k in PLAYER_K.items():
        avg = per_game(cur.get(key, 0), cur_gp, last_avg(key), min(k, last_gp))
        out[key] = avg * games

    for (made_key, att_key), k_games in PLAYER_SHOOTING_K_GAMES.items():
        k_att = min(k_games, last_gp) * last_avg(att_key)
        last_att = last.get(att_key, 0)
        last_pct = 100 * last.get(made_key, 0) / last_att if last_att else None
        p = percentage(cur.get(made_key, 0), cur.get(att_key, 0), last_pct, k_att)
        out[made_key] = (p or 0) / 100 * out[att_key]

    out["FGA"] = out["2PA"] + out["3PTA"]
    out["FGM"] = out["2PM"] + out["3PTM"]
    return out, this_season_weight(cur_gp, min(PLAYER_K["PTS"], last_gp))


if __name__ == "__main__":
    print("Worked example: a team 3 games into the season.\n")
    pts = per_game(246, 3, 72.0, K_GAMES["pts"])
    print("Points: 246 in 3 games (82.0 a game); last season 72.0; k = 8 games")
    print(f"  blended = (246 + 8 x 72.0) / (3 + 8) = {pts:.1f}")
    print(f"  this season's weight: {100 * this_season_weight(3, 8):.0f}%\n")
    three = percentage(12, 30, 33.0, K_ATTEMPTS["three"])
    print("3PT%: 12 of 30 (40.0%); last season 33.0%; k = 300 attempts")
    print(f"  blended = (100 x 12 + 300 x 33.0) / (30 + 300) = {three:.1f}%")
    print(f"  this season's weight: {100 * this_season_weight(30, 300):.0f}%")
