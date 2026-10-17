"""
check_subs.py - Does ESPN's play-by-play include substitutions? (one-time check)

Usage:
    python check_subs.py

Lineup plus-minus needs to know who is on the floor at every moment, which
means substitution plays ("X enters the game for Y"). This looks through every
saved game and reports how many have them, plus a few example lines, so we
know whether lineups are possible. Nothing is written.
"""

import glob
import json
from collections import Counter

SUB_WORDS = ("enters the game", "subbing in", "subbing out", "substitution", "goes to the bench")

files = sorted(glob.glob("data/raw/*.json"))
with_plays = with_subs = 0
examples = []
play_types = Counter()
box_labels = set()

for path in files:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    plays = data.get("plays", [])
    if plays:
        with_plays += 1
    found = 0
    for p in plays:
        text = p.get("text", "")
        kind = (p.get("type") or {}).get("text", "")
        play_types[kind] += 1
        if any(word in text.lower() for word in SUB_WORDS) or "sub" in kind.lower():
            found += 1
            if len(examples) < 6:
                examples.append(f"{path}: [{kind}] {text}")
    if found:
        with_subs += 1
    for team in data.get("boxscore", {}).get("players", []):
        for group in team.get("statistics", []):
            box_labels.update(group.get("labels", []))

print(f"Games saved:                  {len(files)}")
print(f"Games with any play-by-play:  {with_plays}")
print(f"Games with substitution plays: {with_subs}")
print("\nExamples:" if examples else "\nNo substitution plays found.")
for e in examples:
    print("  " + e)
print("\nMost common play types:")
for kind, n in play_types.most_common(25):
    print(f"  {n:>8}  {kind}")
print("\nBox score columns ESPN gives per player:", ", ".join(sorted(box_labels)))
