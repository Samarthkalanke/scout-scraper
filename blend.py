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
