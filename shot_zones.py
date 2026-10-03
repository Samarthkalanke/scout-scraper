"""
shot_zones.py - Turn ESPN shot locations into shooting numbers by court zone.

Usage:
    python shot_zones.py 27        (UC Riverside, using every game in data/raw)

Shows two tables:
  1. The team's own shooting by zone (offense)
  2. What opponents shot against them by zone (defense)

How the zones work:
  ESPN gives each shot an (x, y) in feet. x = 25 is the middle of the court.
  The script first checks where the basket sits by comparing its calculated
  distances to the distances written in the play text ("26-foot three point
  jumper"), then sorts every shot into a zone. Threes are split into
  left corner, left wing, top of the key, right wing and right corner,
  from the shooter's point of view (see FLIP_SIDES below).
  2 vs. 3 comes from ESPN's pointsAttempted, not from geometry.
  Locations are charted by hand at the game, so treat zones as approximate.
"""

import json
import math
import re
import sys
from pathlib import Path

RAW_DIR = Path("data/raw")
ZONES = [
    "Rim (0-4 ft)",
    "Short 2 (4-10 ft)",
    "Midrange 2 (10+ ft)",
    "Left corner 3",
    "Left wing 3",
    "Top of key 3",
    "Right wing 3",
    "Right corner 3",
]
MIN_ATTEMPTS = 10  # flag zones with fewer attempts than this as small samples

# Left and right are from the SHOOTER's view, facing the basket.
# This assumes x below 25 is the shooter's left. If the labels come out
# mirrored when you compare against ESPN's shot chart, change this to True.
FLIP_SIDES = False

CORNER_DX = 20     # feet from the middle of the court: farther out = near the sideline
CORNER_MAX_Y = 9  # feet from the baseline: closer in = near the baseline
TOP_DX = 8         # feet from the middle of the court: closer in = straight down the middle


def load_shots():
    """Read every saved game and return a list of field-goal attempts."""
    shots = []
    for path in sorted(RAW_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            competitors = data["header"]["competitions"][0]["competitors"]
        except (KeyError, IndexError):
            continue
        game_teams = {c["team"]["id"] for c in competitors}

        for play in data.get("plays", []):
            if not play.get("shootingPlay"):
                continue
            text = play.get("text", "")
            if "free throw" in text.lower():
                continue
            coord = play.get("coordinate") or {}
            x, y = coord.get("x"), coord.get("y")
            # Skip missing or junk coordinates
            if x is None or y is None or not (0 <= x <= 50) or not (-5 <= y <= 94):
                continue

            is_three = play.get("pointsAttempted") == 3 or "three point" in text.lower()
            distance_match = re.search(r"(\d+)-foot", text)

            shots.append({
                "game": path.stem,
                "game_teams": game_teams,
                "team": (play.get("team") or {}).get("id"),
                "x": x,
                "y": y,
                "three": is_three,
                "made": bool(play.get("scoringPlay")),
                "stated_ft": int(distance_match.group(1)) if distance_match else None,
            })
    return shots


def find_basket_y(shots):
    """Find the basket's y position that best matches ESPN's written distances."""
    labeled = [s for s in shots if s["stated_ft"] is not None]
    if not labeled:
        return 0.0, None
    best_y, best_err = 0.0, float("inf")
    for tenth in range(0, 61):  # try 0.0 to 6.0 feet
        candidate = tenth / 10
        err = sum(
            abs(math.hypot(s["x"] - 25, s["y"] - candidate) - s["stated_ft"])
            for s in labeled
        ) / len(labeled)
        if err < best_err:
            best_y, best_err = candidate, err
    return best_y, best_err


def zone_for(shot, basket_y):
    distance = math.hypot(shot["x"] - 25, shot["y"] - basket_y)
    if shot["three"]:
        dx = shot["x"] - 25
        if abs(dx) < TOP_DX:
            return "Top of key 3"
        on_left = (dx < 0) != FLIP_SIDES
        side = "Left" if on_left else "Right"
        if abs(dx) >= CORNER_DX and shot["y"] <= CORNER_MAX_Y:
            return f"{side} corner 3"  # where the sideline meets the baseline
        return f"{side} wing 3"
    if distance <= 4:
        return "Rim (0-4 ft)"
    if distance <= 10:
        return "Short 2 (4-10 ft)"
    return "Midrange 2 (10+ ft)"


def print_table(title, shots, basket_y):
    counts = {z: [0, 0] for z in ZONES}  # zone -> [made, attempts]
    for s in shots:
        z = zone_for(s, basket_y)
        counts[z][1] += 1
        counts[z][0] += s["made"]

    total = sum(a for _, a in counts.values())
    print(f"\n{title}  ({total} field goal attempts)")
    print(f"  {'Zone':<22}{'FGM-FGA':>10}{'FG%':>8}{'Share':>8}")
    for z in ZONES:
        made, att = counts[z]
        pct = f"{100 * made / att:.1f}" if att else "-"
        share = f"{100 * att / total:.1f}%" if total else "-"
        flag = "  (small sample)" if 0 < att < MIN_ATTEMPTS else ""
        print(f"  {z:<22}{f'{made}-{att}':>10}{pct:>8}{share:>8}{flag}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python shot_zones.py TEAM_ID")
        sys.exit(1)
    team_id = sys.argv[1]

    shots = load_shots()
    if not shots:
        print(f"No shots found in {RAW_DIR}. Run fetch_season.py first.")
        sys.exit(1)

    basket_y, err = find_basket_y(shots)
    if err is not None:
        print(f"Basket calibrated at y = {basket_y} ft "
              f"(calculated distances are off from ESPN's by {err:.1f} ft on average)")

    team_games = {s["game"] for s in shots if team_id in s["game_teams"]}
    offense = [s for s in shots if s["team"] == team_id]
    defense = [s for s in shots
               if s["game"] in team_games and s["team"] != team_id]

    print(f"Games for team {team_id}: {len(team_games)}")
    print_table("OFFENSE - team's own shots", offense, basket_y)
    print_table("DEFENSE - opponents' shots against them", defense, basket_y)


if __name__ == "__main__":
    main()
