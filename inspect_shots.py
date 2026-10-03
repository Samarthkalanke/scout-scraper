"""
inspect_shots.py - Look inside a saved ESPN game file and summarize the shot data.

Usage:
    python inspect_shots.py                     (uses raw_401823267.json)
    python inspect_shots.py raw_SOMEID.json
"""

import json
import sys

filename = sys.argv[1] if len(sys.argv) > 1 else "raw_401823267.json"

with open(filename, encoding="utf-8") as f:
    data = json.load(f)

plays = data.get("plays", [])
shots = [
    p for p in plays
    if p.get("shootingPlay") and "free throw" not in p.get("text", "").lower()
]

print(f"Total plays: {len(plays)}")
print(f"Field goal attempts (no free throws): {len(shots)}")

# How many shots have usable coordinates?
with_coords = [
    s for s in shots
    if s.get("coordinate") and 0 <= s["coordinate"].get("x", -1) <= 100
]
print(f"Shots with usable coordinates: {len(with_coords)}")

if with_coords:
    xs = [s["coordinate"]["x"] for s in with_coords]
    ys = [s["coordinate"]["y"] for s in with_coords]
    print(f"x range: {min(xs)} to {max(xs)}")
    print(f"y range: {min(ys)} to {max(ys)}")

# Shot types and how often each appears
types = {}
for s in shots:
    t = s.get("type", {}).get("text", "?")
    types[t] = types.get(t, 0) + 1
print("\nShot types:")
for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
    print(f"  {t}: {n}")


def show(label, keyword):
    for s in shots:
        if keyword in s.get("text", "").lower():
            print(f"\n=== Example {label} ===")
            print(json.dumps(s, indent=2))
            return
    print(f"\n(No {label} found)")


show("layup", "layup")
show("three-pointer", "three point")

# Who is which team (needed to match shots to teams)
print("\n=== Teams in this game ===")
for c in data["header"]["competitions"][0]["competitors"]:
    print(f"  id {c['team']['id']}: {c['team']['displayName']} ({c.get('homeAway')})")
