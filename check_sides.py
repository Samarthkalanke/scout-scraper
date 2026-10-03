"""
check_sides.py - List every corner three in one game with the side shot_zones.py gives it.
Use it to check left vs. right against ESPN's shot chart for the same game.

Usage:
    python check_sides.py              (UCR at CSUN, Feb 7, 2026)
    python check_sides.py GAME_ID
"""

import json
import sys
from pathlib import Path

from shot_zones import RAW_DIR, find_basket_y, load_shots, zone_for

game_id = sys.argv[1] if len(sys.argv) > 1 else "401823267"
path = RAW_DIR / f"{game_id}.json"
if not path.exists():
    print(f"{path} not found. Run fetch_season.py first.")
    sys.exit(1)

basket_y, _ = find_basket_y(load_shots())

data = json.loads(path.read_text(encoding="utf-8"))
names = {
    c["team"]["id"]: c["team"]["displayName"]
    for c in data["header"]["competitions"][0]["competitors"]
}

print(f"Corner threes in game {game_id}:\n")
for play in data.get("plays", []):
    if not play.get("shootingPlay") or play.get("pointsAttempted") != 3:
        continue
    coord = play.get("coordinate") or {}
    shot = {
        "x": coord.get("x"),
        "y": coord.get("y"),
        "three": True,
    }
    if shot["x"] is None or shot["y"] is None:
        continue
    zone = zone_for(shot, basket_y)
    if "corner" not in zone:
        continue
    team = names.get((play.get("team") or {}).get("id"), "?")
    half = play.get("period", {}).get("displayValue", "")
    clock = play.get("clock", {}).get("displayValue", "")
    print(f"  {zone:<16} {half} {clock:>5}  {team}: {play.get('text', '')}")
