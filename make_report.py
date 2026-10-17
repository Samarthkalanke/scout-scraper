"""
make_report.py - Build a scouting report for one opponent, straight from the database.

Usage:
    python make_report.py denver
    python make_report.py "san jose"
    python make_report.py 2172           (ESPN team ID also works)

Writes reports/<team>.html and reports/<team>.pdf (the PDF is printed with
Microsoft Edge or Chrome in the background, landscape, ready to send):
    start reports\\denver-pioneers.pdf

Needs (all made by earlier scripts):
    data/scout.db               build_db.py
    data/rosters_2027.csv       label_rosters.py
    data/opponents_27_2027.json fetch_opponents.py
    blend.py                    k values for mixing this season with last season

Every number is calculated fresh from the database each time this runs, so after
new games are downloaded and build_db.py is rerun, the report updates itself.
"""

import csv
import html
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

import blend
import shot_zones

UCR_ID = "27"
SEASON = 2027                 # 2026-27
LAST_SEASON = SEASON - 1      # 2025-26
DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "scout.db"
ROSTER_FILE = DATA_DIR / f"rosters_{SEASON}.csv"
OPP_FILE = DATA_DIR / f"opponents_{UCR_ID}_{SEASON}.json"
OUT_DIR = Path("reports")

SMALL_GAMES = 10   # a player with fewer games than this gets a small-sample flag
SMALL_SHOTS = shot_zones.MIN_ATTEMPTS
TEAM_GAME_MINUTES = 200  # 5 players x 40 minutes


# ---------------------------------------------------------------- data helpers

def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def pct(made, att):
    return 100 * made / att if att else None


def player_season_totals(db, athlete_ids, season):
    """{athlete_id: {stat: total}} for a season, across every team they played for."""
    ids = [a for a in athlete_ids if a]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    totals = {}
    for aid, stat, value in db.execute(
            f"""SELECT ps.athlete_id, ps.stat, SUM(ps.value) FROM player_stats ps
                JOIN games g USING (game_id)
                WHERE g.season = ? AND ps.athlete_id IN ({marks})
                GROUP BY ps.athlete_id, ps.stat""", [season, *ids]):
        totals.setdefault(aid, {})[stat] = value or 0
    return totals


def combine(totals_list):
    out = {}
    for t in totals_list:
        for k, v in t.items():
            out[k] = out.get(k, 0) + v
    return out


def team_season(db, team_id, season):
    """A team's actual results for a season, from the team box scores."""
    games = db.execute(
        """SELECT game_id, home_id, home_score, away_score FROM games
           WHERE season = ? AND (home_id = ? OR away_id = ?)""",
        (season, team_id, team_id)).fetchall()
    if not games:
        return None
    wins = pts = opp = 0
    for _, home_id, hs, as_ in games:
        mine, theirs = (hs, as_) if home_id == team_id else (as_, hs)
        pts += mine or 0
        opp += theirs or 0
        wins += (mine or 0) > (theirs or 0)

    def stat_totals(own):
        op = "=" if own else "!="
        return dict(db.execute(
            f"""SELECT ts.stat, SUM(ts.value) FROM team_stats ts JOIN games g USING (game_id)
                WHERE g.season = ? AND (g.home_id = ? OR g.away_id = ?) AND ts.team_id {op} ?
                GROUP BY ts.stat""", (season, team_id, team_id, team_id)).fetchall())

    us, them = stat_totals(True), stat_totals(False)
    n = len(games)
    g = lambda d, k: d.get(k, 0)
    return {
        "games": n, "wins": wins, "losses": n - wins,
        "ppg": pts / n, "opp_ppg": opp / n,
        "fg": pct(g(us, "fieldGoalsMade"), g(us, "fieldGoalsAttempted")),
        "three": pct(g(us, "threePointFieldGoalsMade"), g(us, "threePointFieldGoalsAttempted")),
        "two": pct(g(us, "fieldGoalsMade") - g(us, "threePointFieldGoalsMade"),
                   g(us, "fieldGoalsAttempted") - g(us, "threePointFieldGoalsAttempted")),
        "ft": pct(g(us, "freeThrowsMade"), g(us, "freeThrowsAttempted")),
        "opp_fg": pct(g(them, "fieldGoalsMade"), g(them, "fieldGoalsAttempted")),
        "opp_three": pct(g(them, "threePointFieldGoalsMade"), g(them, "threePointFieldGoalsAttempted")),
        "reb": g(us, "totalRebounds") / n, "opp_reb": g(them, "totalRebounds") / n,
        "to": g(us, "turnovers") / n, "opp_to": g(them, "turnovers") / n,
        "ast": g(us, "assists") / n,
        # Raw counts, used for blending: percentages blend by attempts, per-game stats by totals
        "made_att": {
            "fg": (g(us, "fieldGoalsMade"), g(us, "fieldGoalsAttempted")),
            "three": (g(us, "threePointFieldGoalsMade"), g(us, "threePointFieldGoalsAttempted")),
            "opp_fg": (g(them, "fieldGoalsMade"), g(them, "fieldGoalsAttempted")),
            "opp_three": (g(them, "threePointFieldGoalsMade"), g(them, "threePointFieldGoalsAttempted")),
        },
        "totals": {
            "pts": pts, "opp_pts": opp,
            "reb": g(us, "totalRebounds"), "opp_reb": g(them, "totalRebounds"),
            "to": g(us, "turnovers"), "opp_to": g(them, "turnovers"),
        },
    }


def roster_profile(totals):
    """What a roster's players did last season, scaled to one 200-minute team game."""
    mins = totals.get("MIN", 0)
    if not mins:
        return None
    per_game = lambda k: totals.get(k, 0) * TEAM_GAME_MINUTES / mins
    fga = totals.get("FGA", 0)
    return {
        "minutes": mins,
        "pts": per_game("PTS"), "reb": per_game("REB"), "oreb": per_game("OREB"),
        "ast": per_game("AST"), "to": per_game("TO"), "stl": per_game("STL"),
        "blk": per_game("BLK"),
        "fg": pct(totals.get("FGM", 0), fga),
        "three": pct(totals.get("3PTM", 0), totals.get("3PTA", 0)),
        "two": pct(totals.get("FGM", 0) - totals.get("3PTM", 0), fga - totals.get("3PTA", 0)),
        "ft": pct(totals.get("FTM", 0), totals.get("FTA", 0)),
        "three_rate": pct(totals.get("3PTA", 0), fga),
        "ft_rate": pct(totals.get("FTA", 0), fga),
    }


# ---------------------------------------------------------------- blending this season with last

# key, label, "game" or "pct", where last season's number comes from
#   "roster" = what the CURRENT players did last season (offense: it follows the players)
#   "team"   = last season's actual team (defense: it follows the coach and system)
# Rebounds use the team because team box scores count "team rebounds" that player
# stats don't, so the roster's number would read 2-3 low.
SEASON_ROWS = [
    ("pts", "Points scored", "game", "roster"),
    ("opp_pts", "Points allowed", "game", "team"),
    ("fg", "FG %", "pct", "roster"),
    ("opp_fg", "Opponent FG %", "pct", "team"),
    ("three", "3PT %", "pct", "roster"),
    ("opp_three", "Opponent 3PT %", "pct", "team"),
    ("reb", "Rebounds", "game", "team"),
    ("opp_reb", "Opponent rebounds", "game", "team"),
    ("to", "Turnovers", "game", "roster"),
    ("opp_to", "Turnovers forced", "game", "team"),
]
TEAM_KEY = {"pts": "ppg", "opp_pts": "opp_ppg"}  # last_team names that differ


def blended_rows(this_team, profile, last_team):
    """One row per stat: the blended estimate, this season's raw number, and last season's."""
    n = this_team["games"]
    rows = []
    for key, label, kind, source in SEASON_ROWS:
        last, basis = None, "none"
        team_key = TEAM_KEY.get(key, key)
        if source == "roster" and profile and profile.get(key) is not None:
            last, basis = profile[key], "current roster"
        elif last_team and last_team.get(team_key) is not None:
            last, basis = last_team[team_key], "last season's team"

        if kind == "game":
            k = blend.K_GAMES[key]
            total = this_team["totals"][key]
            raw = total / n
            estimate = blend.per_game(total, n, last, k)
            weight = blend.this_season_weight(n, k)
        else:
            k = blend.K_ATTEMPTS[key]
            made, att = this_team["made_att"][key]
            raw = pct(made, att)
            estimate = blend.percentage(made, att, last, k)
            weight = blend.this_season_weight(att, k)
        if last is None:
            weight = 1.0
        rows.append({"label": label, "suffix": "%" if kind == "pct" else "",
                     "estimate": estimate, "raw": raw, "last": last,
                     "weight": weight, "basis": basis})
    return rows


def zone_table(db, athlete_ids, season, basket_y):
    ids = [a for a in athlete_ids if a]
    counts = {z: [0, 0] for z in shot_zones.ZONES}
    if ids:
        marks = ",".join("?" * len(ids))
        for x, y, three, made in db.execute(
                f"""SELECT s.x, s.y, s.is_three, s.made FROM shots s JOIN games g USING (game_id)
                    WHERE g.season = ? AND s.athlete_id IN ({marks})""", [season, *ids]):
            z = shot_zones.zone_for({"x": x, "y": y, "three": bool(three)}, basket_y)
            counts[z][1] += 1
            counts[z][0] += made
    total = sum(a for _, a in counts.values())
    return counts, total


def calibrate_basket(db):
    sample = []
    for x, y, three, desc in db.execute(
            "SELECT x, y, is_three, description FROM shots WHERE description LIKE '%-foot%' LIMIT 20000"):
        m = re.search(r"(\d+)-foot", desc or "")
        if m:
            sample.append({"x": x, "y": y, "stated_ft": int(m.group(1))})
    y, _ = shot_zones.find_basket_y(sample)
    return y


# ---------------------------------------------------------------- coach's page helpers

# The 8 detailed zones, grouped into 5 for the summary page
COARSE = {
    "Rim (0-4 ft)": "At the rim",
    "Short 2 (4-10 ft)": "Short 2 (4-10 ft)",
    "Midrange 2 (10+ ft)": "Midrange (10+ ft)",
    "Left corner 3": "Corner 3",
    "Right corner 3": "Corner 3",
    "Left wing 3": "Above-break 3",
    "Top of key 3": "Above-break 3",
    "Right wing 3": "Above-break 3",
}
COARSE_ORDER = ["At the rim", "Short 2 (4-10 ft)", "Midrange (10+ ft)", "Corner 3", "Above-break 3"]


def coarse(counts):
    out = {z: [0, 0] for z in COARSE_ORDER}
    for zone, (made, att) in counts.items():
        out[COARSE[zone]][0] += made
        out[COARSE[zone]][1] += att
    return out


def all_zones(db, season, basket_y):
    """Every shot in the database for a season: the 'D1 average' to compare against."""
    counts = {z: [0, 0] for z in shot_zones.ZONES}
    for x, y, three, made in db.execute(
            """SELECT s.x, s.y, s.is_three, s.made FROM shots s JOIN games g USING (game_id)
               WHERE g.season = ?""", (season,)):
        z = shot_zones.zone_for({"x": x, "y": y, "three": bool(three)}, basket_y)
        counts[z][1] += 1
        counts[z][0] += made
    return counts


def assisted_share(db, athlete_ids, season):
    """Share of made field goals that were assisted (the play text names the passer)."""
    ids = [a for a in athlete_ids if a]
    if not ids:
        return None
    marks = ",".join("?" * len(ids))
    made, assisted = db.execute(
        f"""SELECT COUNT(*), SUM(CASE WHEN s.description LIKE '%assist%' THEN 1 ELSE 0 END)
            FROM shots s JOIN games g USING (game_id)
            WHERE g.season = ? AND s.made = 1 AND s.athlete_id IN ({marks})""", [season, *ids]).fetchone()
    return pct(assisted or 0, made)


def scoring_mix(t):
    """Share of a player's (or roster's) points from 2s, 3s and free throws."""
    pts2 = 2 * (t.get("FGM", 0) - t.get("3PTM", 0))
    pts3 = 3 * t.get("3PTM", 0)
    ptsft = t.get("FTM", 0)
    total = pts2 + pts3 + ptsft
    if not total:
        return None
    return {"two": 100 * pts2 / total, "three": 100 * pts3 / total, "ft": 100 * ptsft / total}


def player_tags(t, zones_coarse):
    """Simple, rule-based labels. Each one is a fixed threshold, not a judgment call."""
    gp = t.get("GP", 0)
    if not gp:
        return []
    pg = lambda k: t.get(k, 0) / gp
    tags = []
    att3 = t.get("3PTA", 0)
    if pg("3PTA") >= 4 and pct(t.get("3PTM", 0), att3) >= 35:
        tags.append("Volume 3PT shooter")
    elif att3 >= 40 and pct(t.get("3PTM", 0), att3) >= 38:
        tags.append("Efficient 3PT shooter")
    total_shots = sum(a for _, a in zones_coarse.values())
    rim = zones_coarse["At the rim"][1]
    if total_shots >= 50 and rim / total_shots >= 0.35:
        tags.append("Attacks the rim")
    mid = zones_coarse["Midrange (10+ ft)"][1]
    if total_shots >= 50 and mid / total_shots >= 0.25:
        tags.append("Midrange scorer")
    if t.get("FGA", 0) >= 50 and pct(t.get("FTA", 0), t.get("FGA", 0)) >= 40:
        tags.append("Gets to the line")
    if pg("AST") >= 4:
        tags.append("Primary playmaker")
    if pg("REB") >= 7:
        tags.append("Strong rebounder")
    if pg("OREB") >= 2.5:
        tags.append("Crashes the offensive glass")
    if pg("BLK") >= 1.5:
        tags.append("Shot blocker")
    if pg("STL") >= 1.5:
        tags.append("Active hands")
    if pg("TO") >= 3:
        tags.append("Turnover prone")
    if t.get("FTA", 0) >= 30 and pct(t.get("FTM", 0), t.get("FTA", 0)) < 65:
        tags.append("Poor FT shooter")
    return tags


TREND_STATS = [
    # key, label, decimals, suffix, higher is better for them?
    ("pts", "Points scored", 1, "", True),
    ("opp_pts", "Points allowed", 1, "", False),
    ("fg", "FG %", 1, "%", True),
    ("three", "3PT %", 1, "%", True),
    ("three_a", "3PT attempts", 1, "", None),
    ("fta", "Free throw attempts", 1, "", True),
    ("reb_margin", "Rebound margin", 1, "", True),
    ("oreb", "Offensive rebounds", 1, "", True),
    ("ast", "Assists", 1, "", True),
    ("to", "Turnovers", 1, "", False),
    ("opp_to", "Turnovers forced", 1, "", True),
    ("opp_fg", "Opponent FG %", 1, "%", False),
    ("opp_three", "Opponent 3PT %", 1, "%", False),
]


def game_lines(db, team_id, season):
    """One row per game: did they win, and their key numbers that night."""
    games = db.execute(
        """SELECT game_id, home_id, home_score, away_score FROM games
           WHERE season = ? AND (home_id = ? OR away_id = ?)""", (season, team_id, team_id)).fetchall()
    stats = {}
    for gid, tid, stat, value in db.execute(
            """SELECT ts.game_id, ts.team_id, ts.stat, ts.value FROM team_stats ts JOIN games g USING (game_id)
               WHERE g.season = ? AND (g.home_id = ? OR g.away_id = ?)""", (season, team_id, team_id)):
        stats.setdefault(gid, {}).setdefault("us" if tid == team_id else "them", {})[stat] = value
    lines = []
    for gid, home_id, hs, as_ in games:
        mine, theirs = (hs, as_) if home_id == team_id else (as_, hs)
        us = stats.get(gid, {}).get("us", {})
        them = stats.get(gid, {}).get("them", {})
        if not us or mine is None or theirs is None:
            continue
        g = lambda d, k: d.get(k)
        row = {
            "won": mine > theirs, "pts": mine, "opp_pts": theirs,
            "fg": pct(us.get("fieldGoalsMade", 0), us.get("fieldGoalsAttempted", 0)),
            "three": pct(us.get("threePointFieldGoalsMade", 0), us.get("threePointFieldGoalsAttempted", 0)),
            "three_a": g(us, "threePointFieldGoalsAttempted"),
            "fta": g(us, "freeThrowsAttempted"),
            "reb_margin": (us["totalRebounds"] - them["totalRebounds"])
                          if "totalRebounds" in us and "totalRebounds" in them else None,
            "oreb": g(us, "offensiveRebounds"),
            "ast": g(us, "assists"), "to": g(us, "turnovers"), "opp_to": g(them, "turnovers"),
            "opp_fg": pct(them.get("fieldGoalsMade", 0), them.get("fieldGoalsAttempted", 0)),
            "opp_three": pct(them.get("threePointFieldGoalsMade", 0), them.get("threePointFieldGoalsAttempted", 0)),
        }
        lines.append(row)
    return lines


def win_loss_trends(lines):
    """Average of each stat in wins vs losses, ranked by how clearly it separates them."""
    wins = [ln for ln in lines if ln["won"]]
    losses = [ln for ln in lines if not ln["won"]]
    out = []
    for key, label, d, suf, better in TREND_STATS:
        w_vals = [ln[key] for ln in wins if ln.get(key) is not None]
        l_vals = [ln[key] for ln in losses if ln.get(key) is not None]
        all_vals = w_vals + l_vals
        if len(w_vals) < 2 or len(l_vals) < 2:
            continue
        mw, ml = sum(w_vals) / len(w_vals), sum(l_vals) / len(l_vals)
        mean = sum(all_vals) / len(all_vals)
        sd = (sum((v - mean) ** 2 for v in all_vals) / (len(all_vals) - 1)) ** 0.5
        effect = (mw - ml) / sd if sd else 0
        out.append({"key": key, "label": label, "d": d, "suf": suf, "better": better,
                    "win": mw, "loss": ml, "effect": effect,
                    # points scored/allowed always differ between wins and losses, so they're
                    # shown for context but not ranked as a "trend"
                    "context": key in ("pts", "opp_pts")})
    out.sort(key=lambda r: (not r["context"], -abs(r["effect"])))
    return out, len(wins), len(losses)


# ---------------------------------------------------------------- html helpers

def esc(v):
    return html.escape(str(v))


def fmt(v, digits=1, suffix=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{v:.{digits}f}{suffix}"


def per_game(totals, key):
    gp = totals.get("GP", 0)
    return totals.get(key, 0) / gp if gp else None


CSS = """
:root{--ink:#14181f;--ink-soft:#4a5164;--paper:#f5f6f8;--surface:#fff;--line:#e2e4ea;--blue:#1e4dd8;--blue-soft:#eaefff;--good:#157a4a;--good-soft:#e5f5ec;--warn:#b3541e;--warn-soft:#fbebdd;--muted-soft:#eef0f3;--shadow:0 1px 2px rgba(20,24,31,.04),0 8px 24px -12px rgba(20,24,31,.12)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eef0f4;--ink-soft:#a7adbd;--paper:#0e1116;--surface:#171b22;--line:#2a2f3a;--blue:#6f92ff;--blue-soft:#1b2647;--good:#4fd393;--good-soft:#113322;--warn:#f2a06a;--warn-soft:#3a2413;--muted-soft:#232833;--shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px -12px rgba(0,0,0,.5)}}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:"Source Sans 3",ui-sans-serif,system-ui,sans-serif;margin:0;padding:28px 16px 48px;display:flex;justify-content:center}
main{width:100%;max-width:1140px;display:flex;flex-direction:column;gap:28px}
h1,h2,h3{font-family:"Oswald",ui-sans-serif,sans-serif;margin:0}
header.page{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px;border-bottom:3px solid var(--ink);padding-bottom:14px}
.eyebrow{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--blue);font-weight:600;margin:0 0 4px}
header.page h1{font-size:clamp(26px,4vw,34px);font-weight:700}
.meta{text-align:right;font-size:13px;color:var(--ink-soft);line-height:1.5}
section{display:flex;flex-direction:column;gap:12px}
section>h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;color:var(--ink-soft)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:var(--shadow)}
.tile .label{font-size:12px;color:var(--ink-soft);margin-bottom:6px}
.tile .value{font-family:"Oswald",sans-serif;font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
.tile .sub{font-size:12px;color:var(--ink-soft);margin-top:4px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
.pad{padding:16px 18px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:8px 8px;text-align:right;white-space:nowrap}
th:first-child,td:first-child,td.left,th.left{text-align:left}
thead th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-soft);font-weight:600;border-bottom:1px solid var(--line)}
tbody tr:not(:last-child) td{border-bottom:1px solid var(--line)}
td{font-variant-numeric:tabular-nums}
td.name{font-weight:600}
td.soft,span.soft{color:var(--ink-soft)}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px}
.pill.Returning{background:var(--good-soft);color:var(--good)}
.pill.Transfer{background:var(--blue-soft);color:var(--blue)}
.pill.nodata{background:var(--muted-soft);color:var(--ink-soft)}
.sticky{position:sticky;left:0;background:var(--surface);z-index:1}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:9px}
.card h3{font-size:18px;font-weight:600}
.statline{display:flex;gap:18px}
.big{font-family:"Oswald",sans-serif;font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.small{font-size:12.5px;line-height:1.5}
table.mini{font-size:12px}table.mini td{padding:3px 4px;border:none!important}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;background:var(--blue-soft);color:var(--blue);margin:0 4px 4px 0}
td.up{color:var(--good);font-weight:600}td.down{color:var(--warn);font-weight:600}
ul.bullets{margin:0;padding-left:18px;font-size:14px;line-height:1.7}
.flag{font-size:11px;color:var(--warn);font-weight:600}
.bar{height:8px;border-radius:999px;background:var(--line);overflow:hidden;min-width:80px}
.bar>div{height:100%;background:var(--blue)}
.note{font-size:12.5px;color:var(--ink-soft);margin:0;line-height:1.55}
.callout{border-left:4px solid var(--warn);background:var(--warn-soft);padding:12px 14px;border-radius:8px;font-size:13.5px;line-height:1.5}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:700px){.two{grid-template-columns:1fr}.meta{text-align:left}}
@media print{
:root{--ink:#14181f;--ink-soft:#4a5164;--paper:#fff;--surface:#fff;--line:#d5d8df;--blue:#1e4dd8;--blue-soft:#eaefff;--good:#157a4a;--good-soft:#e5f5ec;--warn:#b3541e;--warn-soft:#fbebdd;--muted-soft:#eef0f3;--shadow:none}
@page{size:letter landscape;margin:0.35in}
body{padding:0;font-size:11px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
main{max-width:none;gap:16px}
.scroll{overflow:visible}.sticky{position:static}
table{font-size:10.5px}th,td{padding:4px 6px}
thead th{font-size:9px}
.tile{padding:8px 10px}.tile .value{font-size:20px}
.two{grid-template-columns:1fr 1fr}
.tiles{grid-template-columns:repeat(auto-fit,minmax(105px,1fr))}.cards{grid-template-columns:repeat(3,1fr)}.card{padding:8px 10px;gap:5px}.big{font-size:17px}.card h3{font-size:14px}
.pagebreak{break-before:page}
tr,.tile,.callout,.panel,.two,.card{break-inside:avoid}
section>h2{break-after:avoid}
.callout{padding:8px 10px;font-size:11.5px}
}
footer{border-top:1px solid var(--line);padding-top:14px;font-size:12.5px;color:var(--ink-soft);line-height:1.6}
"""


# ---------------------------------------------------------------- report

def find_team(query, opponents):
    if query in opponents:
        return query, opponents[query]["name"]
    q = slug(query)
    hits = [(tid, info["name"]) for tid, info in opponents.items() if q in slug(info["name"])]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"No opponent matches '{query}'. Options: "
                 + ", ".join(i["name"] for i in opponents.values()))
    sys.exit(f"'{query}' matches several teams: " + ", ".join(n for _, n in hits))


# ---------------------------------------------------------------- pdf

BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_browser():
    """Edge comes with every Windows PC, so it's almost always found.
    Set REPORT_BROWSER to a browser's full path to use a different one."""
    custom = os.environ.get("REPORT_BROWSER")
    if custom and Path(custom).exists():
        return custom
    for path in BROWSER_PATHS:
        if Path(path).exists():
            return path
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def save_pdf(html_file):
    """Print the report to PDF with the browser in the background (no window opens)."""
    browser = find_browser()
    if not browser:
        return None, "no Edge or Chrome found"
    pdf_file = html_file.with_suffix(".pdf")
    if pdf_file.exists():
        pdf_file.unlink()
    cmd = [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           "--virtual-time-budget=8000", f"--print-to-pdf={pdf_file.resolve()}",
           html_file.resolve().as_uri()]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, str(err)
    if pdf_file.exists() and pdf_file.stat().st_size > 0:
        return pdf_file, None
    return None, "the browser did not create the PDF"


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python make_report.py denver   (or a team ID)')
    for needed in (DB_FILE, ROSTER_FILE, OPP_FILE):
        if not needed.exists():
            sys.exit(f"Missing {needed}.")

    opponents = json.loads(OPP_FILE.read_text(encoding="utf-8"))
    team_id, team_name = find_team(" ".join(sys.argv[1:]), opponents)
    game_dates = sorted(opponents[team_id]["dates"])

    with open(ROSTER_FILE, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    roster = [r for r in all_rows if r["team_id"] == team_id]
    ucr_roster = [r for r in all_rows if r["team_id"] == UCR_ID]

    db = sqlite3.connect(DB_FILE)
    basket_y = calibrate_basket(db)

    # --- players
    totals = player_season_totals(db, [r["espn_athlete_id"] for r in roster], LAST_SEASON)
    players = []
    for r in roster:
        t = totals.get(r["espn_athlete_id"], {})
        players.append((r, t))
    players.sort(key=lambda rt: -rt[1].get("MIN", 0))

    counts = {"Returning": 0, "Transfer": 0, "No data": 0}
    for r, _ in players:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    returning_min = sum(t.get("MIN", 0) for r, t in players if r["status"] == "Returning")
    team_min = db.execute(
        """SELECT SUM(ps.value) FROM player_stats ps JOIN games g USING (game_id)
           WHERE ps.team_id = ? AND ps.stat = 'MIN' AND g.season = ?""",
        (team_id, LAST_SEASON)).fetchone()[0] or 0
    continuity = pct(returning_min, team_min)

    with_data = [t for r, t in players if t.get("MIN")]
    profile = roster_profile(combine(with_data))

    ucr_totals = player_season_totals(db, [r["espn_athlete_id"] for r in ucr_roster], LAST_SEASON)
    ucr_profile = roster_profile(combine(list(ucr_totals.values())))

    last_team = team_season(db, team_id, LAST_SEASON)
    this_team = team_season(db, team_id, SEASON)

    zones, zone_total = zone_table(db, [r["espn_athlete_id"] for r, _ in players], LAST_SEASON, basket_y)
    ucr_zones, ucr_zone_total = zone_table(db, [r["espn_athlete_id"] for r in ucr_roster], LAST_SEASON, basket_y)

    h2h = db.execute(
        """SELECT date, home_id, home_score, away_score FROM games
           WHERE season IN (?, ?) AND ((home_id = ? AND away_id = ?) OR (home_id = ? AND away_id = ?))
           ORDER BY date""", (LAST_SEASON, SEASON, team_id, UCR_ID, UCR_ID, team_id)).fetchall()

    roster_ids = [r["espn_athlete_id"] for r, _ in players]
    d1_coarse = coarse(all_zones(db, LAST_SEASON, basket_y))
    opp_coarse = coarse(zones)
    roster_assisted = assisted_share(db, roster_ids, LAST_SEASON)
    roster_mix = scoring_mix(combine(with_data))

    # Top 3: points per game. Uses this season once a player has 5+ games, otherwise last season.
    cur_totals = player_season_totals(db, roster_ids, SEASON)

    def scoring_rank(rt):
        r, t = rt
        cur = cur_totals.get(r["espn_athlete_id"], {})
        if cur.get("GP", 0) >= 5:
            return per_game(cur, "PTS")
        return per_game(t, "PTS") if t.get("GP", 0) >= 5 else -1

    top_cards = []
    for r, t in sorted(players, key=scoring_rank, reverse=True)[:3]:
        if scoring_rank((r, t)) < 0:
            continue
        aid = r["espn_athlete_id"]
        cur = cur_totals.get(aid, {})
        season_used = SEASON if cur.get("GP", 0) >= 5 else LAST_SEASON
        src = cur if season_used == SEASON else t
        zc = coarse(zone_table(db, [aid], season_used, basket_y)[0])
        top_cards.append({"r": r, "t": src, "season": season_used, "zones": zc,
                          "assisted": assisted_share(db, [aid], season_used),
                          "mix": scoring_mix(src), "tags": player_tags(src, zc)})

    # Win/loss trends: this season once there are 3+ wins and 3+ losses, otherwise last season
    cur_lines = game_lines(db, team_id, SEASON)
    cur_w = sum(1 for ln in cur_lines if ln["won"])
    if cur_w >= 3 and len(cur_lines) - cur_w >= 3:
        trend_season, trend_lines = SEASON, cur_lines
    else:
        trend_season, trend_lines = LAST_SEASON, game_lines(db, team_id, LAST_SEASON)
    trends, n_wins, n_losses = win_loss_trends(trend_lines)

    former_ucr = [r for r, _ in players if r["last_season_team_id"] == UCR_ID and r["status"] == "Transfer"]
    db.close()

    # --- trust level for last season's team numbers
    if continuity is None:
        trust = "unknown"
    elif continuity >= 60:
        trust = "high"
    elif continuity >= 30:
        trust = "medium"
    else:
        trust = "low"

    out = []
    w = out.append
    w(f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
      f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
      f"<title>{esc(team_name)} Scouting Report</title>"
      "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700"
      "&family=Source+Sans+3:wght@400;500;600;700&display=swap'>"
      f"<style>{CSS}</style></head><body><main>")

    when = ", ".join(date.fromisoformat(d).strftime("%b %-d" if sys.platform != "win32" else "%b %#d")
                     for d in game_dates)
    w("<header class='page'><div><p class='eyebrow'>Opponent Scouting Report · UC Riverside Men's Basketball</p>"
      f"<h1>{esc(team_name)}</h1></div><div class='meta'>Plays UCR: <strong>{esc(when)}</strong><br>"
      f"Generated {date.today().strftime('%b %d, %Y')} from {esc(DB_FILE.name)}</div></header>")

    def tile(label, value, sub=""):
        w(f"<div class='tile'><div class='label'>{label}</div><div class='value'>{value}</div>"
          + (f"<div class='sub'>{sub}</div>" if sub else "") + "</div>")

    def label_for(season):
        return f"{season - 1}-{str(season)[2:]}"

    short = esc(team_name.split()[0])

    # --- at a glance
    w("<section><h2>At a glance</h2><div class='tiles'>")
    if last_team:
        tile(f"{label_for(LAST_SEASON)} record", f"{last_team['wins']}-{last_team['losses']}",
             f"{fmt(last_team['ppg'])} scored, {fmt(last_team['opp_ppg'])} allowed")
    tile("Minutes returning", fmt(continuity, 0, "%"),
         f"{counts['Returning']} returning, {counts['Transfer']} D1 transfers, {counts['No data']} without D1 stats")
    if this_team:
        tile(f"{label_for(SEASON)} so far", f"{this_team['wins']}-{this_team['losses']}",
             f"{fmt(this_team['ppg'])} scored, {fmt(this_team['opp_ppg'])} allowed")
    w("</div>")
    trust_text = {
        "high": "Most of last season's team is back, so last season's team numbers are a reasonable guide.",
        "medium": "About half the team is new. Weigh the player-based numbers over last season's team numbers.",
        "low": "This is largely a new team. Last season's team numbers describe players who are mostly gone, so this report leans on each current player's own numbers.",
        "unknown": "Last season's minutes aren't available for this team.",
    }[trust]
    w(f"<div class='callout'><strong>How much last season counts ({trust}):</strong> {esc(trust_text)}</div>")
    if former_ucr:
        names = ", ".join(r["player"] for r in former_ucr)
        w(f"<div class='callout'><strong>Former Highlanders:</strong> {esc(names)} played for UCR last season.</div>")
    w("</section>")

    # --- this season so far, blended with last season
    if this_team:
        n = this_team["games"]
        flag = " <span class='flag'>small sample</span>" if n < 5 else ""
        w(f"<section><h2>This season so far ({this_team['wins']}-{this_team['losses']}, {n} games){flag}</h2>"
          "<div class='panel scroll'><table><thead><tr><th class='left'>Per game</th><th>Best estimate</th>"
          f"<th>{label_for(SEASON)} so far</th><th>Last season</th><th>Weight on this season</th><th></th>"
          "<th class='left'>Last season from</th></tr></thead><tbody>")
        for row in blended_rows(this_team, profile, last_team):
            s = row["suffix"]
            w(f"<tr><td class='name'>{row['label']}</td><td class='name'>{fmt(row['estimate'], 1, s)}</td>"
              f"<td>{fmt(row['raw'], 1, s)}</td><td class='soft'>{fmt(row['last'], 1, s)}</td>"
              f"<td>{100 * row['weight']:.0f}%</td>"
              f"<td><div class='bar'><div style='width:{100 * row['weight']:.0f}%'></div></div></td>"
              f"<td class='left soft'>{esc(row['basis'])}</td></tr>")
        w("</tbody></table></div>"
          "<p class='note'><strong>Best estimate</strong> mixes this season with last season, because a few games "
          "can mislead. Last season counts like several games' worth (more for noisy stats like rebounding and "
          "opponent FG%, fewer for style stats), so this season takes over as games pile up. The weight column "
          "shows how much of each estimate is this season. Offense uses what the current players did last season; "
          "defense and rebounding use last season's team, since those carry over with the coach.</p></section>")

    # --- top 3
    w("<section><h2>Top 3 players</h2><div class='cards'>")
    for c in top_cards:
        r, t = c["r"], c["t"]
        where = r["last_season_team"] if c["season"] == LAST_SEASON and r["status"] == "Transfer" else team_name
        origin = (f"{esc(r['status'])}, from {esc(r['last_season_team'])}" if r["status"] == "Transfer"
                  else esc(r["status"]))
        two_m, two_a = t.get("FGM", 0) - t.get("3PTM", 0), t.get("FGA", 0) - t.get("3PTA", 0)
        gp = t.get("GP", 0)
        efg = pct(t.get("FGM", 0) + 0.5 * t.get("3PTM", 0), t.get("FGA", 0))
        w("<div class='card'>")
        w(f"<div class='card-head'><h3>{esc(r['player'])} <span class='soft'>#{esc(r['jersey'])}</span></h3>"
          f"<div class='soft small'>{esc(r['position'])} · {esc(r['height'])} · {esc(r['class'])} · {origin}</div></div>")
        w("<div class='statline'>")
        for lab, key in [("PPG", "PTS"), ("RPG", "REB"), ("APG", "AST"), ("MPG", "MIN")]:
            w(f"<div><div class='big'>{fmt(per_game(t, key))}</div><div class='soft small'>{lab}</div></div>")
        w("</div>")
        w(f"<div class='small'><strong>2P</strong> {fmt(pct(two_m, two_a))}% ({fmt(two_a / gp if gp else None)} att/g) · "
          f"<strong>3P</strong> {fmt(pct(t.get('3PTM', 0), t.get('3PTA', 0)))}% ({fmt(per_game(t, '3PTA'))} att/g) · "
          f"<strong>FT</strong> {fmt(pct(t.get('FTM', 0), t.get('FTA', 0)))}% ({fmt(per_game(t, 'FTA'))} att/g) · "
          f"<strong>eFG</strong> {fmt(efg)}%</div>")
        if c["mix"]:
            m = c["mix"]
            w(f"<div class='small'><strong>How he scores:</strong> {m['two']:.0f}% of his points on 2s, "
              f"{m['three']:.0f}% on 3s, {m['ft']:.0f}% at the line"
              + (f"; {c['assisted']:.0f}% of his makes are assisted" if c["assisted"] is not None else "")
              + ".</div>")
        total = sum(a for _, a in c["zones"].values())
        if total:
            w("<table class='mini'><tbody>")
            for z in COARSE_ORDER:
                made, att = c["zones"][z]
                share = 100 * att / total
                w(f"<tr><td>{z}</td><td><div class='bar'><div style='width:{min(share * 2, 100):.0f}%'></div></div></td>"
                  f"<td>{share:.0f}%</td><td class='soft'>{fmt(pct(made, att), 0, '%')} FG</td></tr>")
            w("</tbody></table>")
        if c["tags"]:
            w("<div>" + " ".join(f"<span class='tag'>{esc(tg)}</span>" for tg in c["tags"]) + "</div>")
        w(f"<div class='soft small'>{label_for(c['season'])} at {esc(where)}, {int(gp)} games"
          + (" · <span class='flag'>small sample</span>" if gp < SMALL_GAMES else "") + "</div>")
        w("</div>")
    if not top_cards:
        w("<p class='note'>No player on this roster has 5+ Division I games yet.</p>")
    w("</div><p class='note'>Ranked by points per game. Uses this season's numbers once a player has 5 games, "
      "otherwise last season's, wherever he played. Shot shares are of his field goal attempts. Labels are fixed "
      "rules, such as 4+ threes a game at 35% or better for a volume shooter.</p></section>")

    # --- how they score
    w(f"<section><h2>How they score</h2><div class='tiles'>")
    if roster_mix:
        tile("Points from 2s", f"{roster_mix['two']:.0f}%")
        tile("Points from 3s", f"{roster_mix['three']:.0f}%")
        tile("Points from free throws", f"{roster_mix['ft']:.0f}%")
    if roster_assisted is not None:
        tile("Makes that are assisted", f"{roster_assisted:.0f}%", "low = more one-on-one scoring")
    if profile:
        tile("Shots that are 3s", fmt(profile["three_rate"], 0, "%"))
        tile("FT attempts per FGA", fmt(profile["ft_rate"], 0, "%"), "how often they get to the line")
    w("</div><p class='note'>From the current roster's players last season, wherever they played. "
      "Freshmen and players without D1 stats aren't included.</p></section>")

    # --- shot profile vs D1 average
    opp_total = sum(a for _, a in opp_coarse.values())
    d1_total = sum(a for _, a in d1_coarse.values())
    w(f"<section><h2>Shot profile</h2><div class='panel scroll'><table><thead><tr>"
      f"<th class='left'>Zone</th><th>{short} share</th><th></th><th>{short} FG%</th>"
      f"<th>D1 avg share</th><th>D1 avg FG%</th><th>vs average</th></tr></thead><tbody>")
    diffs = []
    for z in COARSE_ORDER:
        made, att = opp_coarse[z]
        dm, da = d1_coarse[z]
        share = 100 * att / opp_total if opp_total else 0
        d1_share = 100 * da / d1_total if d1_total else 0
        diff = share - d1_share
        diffs.append((diff, z, share, d1_share))
        cls = "up" if diff >= 4 else ("down" if diff <= -4 else "soft")
        flag = " <span class='flag'>small sample</span>" if 0 < att < SMALL_SHOTS else ""
        w(f"<tr><td>{z}{flag}</td><td>{share:.0f}%</td>"
          f"<td><div class='bar'><div style='width:{min(share * 2.5, 100):.0f}%'></div></div></td>"
          f"<td>{fmt(pct(made, att))}</td><td class='soft'>{d1_share:.0f}%</td>"
          f"<td class='soft'>{fmt(pct(dm, da))}</td><td class='{cls}'>{diff:+.0f} pts</td></tr>")
    w("</tbody></table></div>")
    if opp_total:
        hi = max(diffs)
        lo = min(diffs)
        parts = []
        if hi[0] >= 4:
            parts.append(f"more shots {hi[1].lower()} than average ({hi[2]:.0f}% vs {hi[3]:.0f}%)")
        if lo[0] <= -4:
            parts.append(f"fewer {lo[1].lower()} ({lo[2]:.0f}% vs {lo[3]:.0f}%)")
        if parts:
            w(f"<p class='note'><strong>Takeaway:</strong> this roster took {' and '.join(parts)} last season.</p>")
    w(f"<p class='note'>{opp_total} shots by the current roster last season. D1 average is every shot in the database "
      f"({d1_total:,} shots). Zones are approximate because shot locations are charted by hand.</p></section>")

    # --- win/loss trends
    w(f"<section><h2>What decides their games: wins vs losses, {label_for(trend_season)} "
      f"({n_wins}-{n_losses})</h2>")
    if trend_season == LAST_SEASON and trust in ("low", "medium"):
        w(f"<div class='callout'>These trends are from last season's team, which returns only "
          f"{fmt(continuity, 0, '%')} of its minutes. They say more about how this coach's teams win than about "
          f"these players. They switch to this season automatically once {short} has 3 wins and 3 losses.</div>")
    if trends:
        w("<div class='panel pad'><ul class='bullets'>")
        for tr in [t for t in trends if not t["context"]][:3]:
            w(f"<li><strong>{tr['label']}:</strong> {fmt(tr['win'], tr['d'], tr['suf'])} in wins vs "
              f"{fmt(tr['loss'], tr['d'], tr['suf'])} in losses.</li>")
        w("</ul></div>")
        w("<div class='panel scroll'><table><thead><tr><th class='left'>Per game</th><th>In wins</th>"
          "<th>In losses</th><th>Difference</th></tr></thead><tbody>")
        top_keys = [t["key"] for t in trends if not t["context"]][:3]
        for tr in trends:
            strong = " class='name'" if tr["key"] in top_keys else (" class='soft'" if tr["context"] else "")
            w(f"<tr><td{strong}>{tr['label']}</td><td>{fmt(tr['win'], tr['d'], tr['suf'])}</td>"
              f"<td>{fmt(tr['loss'], tr['d'], tr['suf'])}</td>"
              f"<td>{tr['win'] - tr['loss']:+.{tr['d']}f}</td></tr>")
        w("</tbody></table></div>")
        w("<p class='note'>Sorted by how clearly each stat separated their wins from their losses: the gap between "
          "the two averages, compared with how much that stat normally varies from game to game. "
          "The top 3 are in bold. Points scored and allowed are shown first for context. "
          "These are patterns, not proven causes.</p>")
    else:
        w("<p class='note'>Not enough wins and losses to compare yet.</p>")
    w("</section>")

    # --- roster tables
    def name_cell(r, gp):
        small = " <span class='flag'>small sample</span>" if 0 < gp < SMALL_GAMES else ""
        return (f"<td class='name left sticky'>{esc(r['player'])} <span class='soft'>#{esc(r['jersey'])}</span>"
                f"{small}</td>")

    def split(t, made, att):
        return (f"<td>{fmt(per_game(t, made))}-{fmt(per_game(t, att))}</td>"
                f"<td>{fmt(pct(t.get(made, 0), t.get(att, 0)))}</td>")

    season_label = f"{LAST_SEASON - 1}-{str(LAST_SEASON)[2:]}"
    w(f"<section class='pagebreak'><h2>Full roster: {SEASON - 1}-{str(SEASON)[2:]} players, {season_label} stats</h2>"
      "<div class='panel scroll'><table><thead><tr>"
      "<th class='left sticky'>Player</th><th class='left'>Pos</th><th class='left'>Ht</th><th class='left'>Yr</th>"
      "<th class='left'>Status</th><th class='left'>Last season at</th>"
      f"<th>GP</th><th>GS</th><th>MPG</th><th>PPG</th><th>RPG</th><th>ORB</th><th>APG</th>"
      "<th>STL</th><th>BLK</th><th>TO</th><th>PF</th><th>A/TO</th>"
      "</tr></thead><tbody>")
    for r, t in players:
        gp = t.get("GP", 0)
        pill = "nodata" if r["status"] == "No data" else r["status"]
        if r["status"] == "Transfer":
            where = esc(r["last_season_team"])
        elif r["status"] == "No data" and r["previous_schools"]:
            where = f"<span class='soft'>{esc(r['previous_schools'])}</span>"
        else:
            where = ""
        if gp:
            to = t.get("TO", 0)
            ast_to = fmt(t.get("AST", 0) / to) if to else "–"
            stats = (f"<td>{int(gp)}</td><td>{int(t.get('GS', 0))}</td><td>{fmt(per_game(t, 'MIN'))}</td>"
                     f"<td>{fmt(per_game(t, 'PTS'))}</td><td>{fmt(per_game(t, 'REB'))}</td>"
                     f"<td>{fmt(per_game(t, 'OREB'))}</td><td>{fmt(per_game(t, 'AST'))}</td>"
                     f"<td>{fmt(per_game(t, 'STL'))}</td><td>{fmt(per_game(t, 'BLK'))}</td>"
                     f"<td>{fmt(per_game(t, 'TO'))}</td><td>{fmt(per_game(t, 'PF'))}</td><td>{ast_to}</td>")
        else:
            stats = "<td class='soft left' colspan='12'>no Division I stats last season</td>"
        w(f"<tr>{name_cell(r, gp)}"
          f"<td class='left'>{esc(r['position'])}</td><td class='left'>{esc(r['height'])}</td>"
          f"<td class='left'>{esc(r['class'])}</td><td class='left'><span class='pill {pill}'>{esc(r['status'])}</span></td>"
          f"<td class='left'>{where}</td>{stats}</tr>")
    w("</tbody></table></div>"
      f"<p class='note'>Per-game averages from {season_label}, sorted by minutes played. Transfers' stats are from their "
      "previous D1 school. Players with no D1 stats are freshmen, redshirts, or came from junior college, Division II, "
      "NAIA or overseas, which ESPN doesn't cover.</p></section>")

    w(f"<section><h2>Shooting splits, {season_label}</h2>"
      "<div class='panel scroll'><table><thead><tr><th class='left sticky'>Player</th>"
      "<th>FGM-A/g</th><th>FG%</th><th>2PM-A/g</th><th>2P%</th><th>3PM-A/g</th><th>3P%</th>"
      "<th>FTM-A/g</th><th>FT%</th><th>eFG%</th><th>3PA share</th><th>FT rate</th>"
      "</tr></thead><tbody>")
    for r, t in players:
        gp = t.get("GP", 0)
        if not gp:
            continue
        two = {"2PM": t.get("FGM", 0) - t.get("3PTM", 0), "2PA": t.get("FGA", 0) - t.get("3PTA", 0), "GP": gp}
        fga = t.get("FGA", 0)
        efg = pct(t.get("FGM", 0) + 0.5 * t.get("3PTM", 0), fga)
        w(f"<tr>{name_cell(r, gp)}{split(t, 'FGM', 'FGA')}{split(two, '2PM', '2PA')}"
          f"{split(t, '3PTM', '3PTA')}{split(t, 'FTM', 'FTA')}<td>{fmt(efg)}</td>"
          f"<td>{fmt(pct(t.get('3PTA', 0), fga), 0, '%')}</td><td>{fmt(pct(t.get('FTA', 0), fga), 0, '%')}</td></tr>")
    w("</tbody></table></div>"
      "<p class='note'><strong>eFG%</strong> counts a made three as 1.5 makes, so it compares shooters fairly. "
      "<strong>3PA share</strong> is the share of a player's shots that are threes. "
      "<strong>FT rate</strong> is free throw attempts per field goal attempt, a measure of how often he gets to the line.</p></section>")

    # --- roster profile vs last season's team vs UCR
    w("<section><h2>What this roster did last season, combined</h2><div class='panel scroll'><table><thead><tr>"
      f"<th class='left'>Per 40-minute game</th><th>{esc(team_name.split()[0])} roster</th>"
      f"<th>{esc(team_name.split()[0])} team {LAST_SEASON - 1}-{str(LAST_SEASON)[2:]}</th><th>UCR roster</th></tr></thead><tbody>")
    rows = [
        ("Points", "pts", "ppg", 1, ""), ("Rebounds", "reb", "reb", 1, ""),
        ("Assists", "ast", "ast", 1, ""), ("Turnovers", "to", "to", 1, ""),
        ("FG %", "fg", "fg", 1, "%"), ("3PT %", "three", "three", 1, "%"),
        ("2PT %", "two", "two", 1, "%"), ("FT %", "ft", "ft", 1, "%"), ("3PT attempts per FGA", "three_rate", None, 0, "%"),
        ("Free throw attempts per FGA", "ft_rate", None, 0, "%"),
        ("Offensive rebounds", "oreb", None, 1, ""), ("Steals", "stl", None, 1, ""), ("Blocks", "blk", None, 1, ""),
    ]
    for label, pk, tk, d, suf in rows:
        a = fmt(profile.get(pk), d, suf) if profile else "–"
        b = fmt(last_team.get(tk), d, suf) if (last_team and tk) else "–"
        c = fmt(ucr_profile.get(pk), d, suf) if ucr_profile else "–"
        w(f"<tr><td>{label}</td><td>{a}</td><td class='soft'>{b}</td><td>{c}</td></tr>")
    w("</tbody></table></div>"
      "<p class='note'><strong>Roster columns</strong> add up what the players on each current roster did last season, "
      "wherever they played, and scale it to one 40-minute game. Players without D1 stats are left out, and transfers' "
      "numbers come from different teams and roles, so treat these as a rough profile, not a prediction. "
      "<strong>Team column</strong> is last season's actual team, which matters less the more the roster turned over.</p>")
    if last_team:
        w(f"<p class='note'>Last season: {last_team['wins']}-{last_team['losses']}, "
          f"{fmt(last_team['ppg'])} points scored and {fmt(last_team['opp_ppg'])} allowed per game.</p>")
    w("</section>")

    # --- shot zones
    def zone_rows(cnt, total):
        rows_html = []
        for z in shot_zones.ZONES:
            made, att = cnt[z]
            share = 100 * att / total if total else 0
            flag = " <span class='flag'>small sample</span>" if 0 < att < SMALL_SHOTS else ""
            rows_html.append(
                f"<tr><td>{z}{flag}</td><td>{made}-{att}</td><td>{fmt(pct(made, att))}</td>"
                f"<td>{share:.0f}%</td><td><div class='bar'><div style='width:{min(share * 2.5, 100):.0f}%'></div></div></td></tr>")
        return "".join(rows_html)

    w("<section><h2>Detailed shot zones vs UCR's roster</h2><div class='two'>")
    w(f"<div class='panel scroll'><table><thead><tr><th class='left'>{esc(team_name.split()[0])} roster ({zone_total} FGA)</th>"
      "<th>FGM-A</th><th>FG%</th><th>Share</th><th></th></tr></thead><tbody>"
      f"{zone_rows(zones, zone_total)}</tbody></table></div>")
    w(f"<div class='panel scroll'><table><thead><tr><th class='left'>UCR roster ({ucr_zone_total} FGA)</th>"
      "<th>FGM-A</th><th>FG%</th><th>Share</th><th></th></tr></thead><tbody>"
      f"{zone_rows(ucr_zones, ucr_zone_total)}</tbody></table></div>")
    w("</div><p class='note'>Shots by the players on each current roster, from last season's play-by-play. "
      "Left and right are from the shooter's view. Shot locations are charted by hand at each game, so zones are approximate.</p></section>")

    # --- head to head
    if h2h:
        w("<section><h2>Recent meetings with UCR</h2><div class='panel pad'>")
        for d, home_id, hs, as_ in h2h:
            ucr_home = home_id == UCR_ID
            ucr_pts, opp_pts = (hs, as_) if ucr_home else (as_, hs)
            result = "W" if (ucr_pts or 0) > (opp_pts or 0) else "L"
            w(f"<p class='note'>{esc(d)}: UCR {result} {int(ucr_pts or 0)}-{int(opp_pts or 0)} "
              f"({'home' if ucr_home else 'away'})</p>")
        w("</div></section>")

    w("<footer><strong>How this was made:</strong> generated automatically by make_report.py from ESPN box scores and "
      "play-by-play, with the 2026-27 roster taken from the school's official athletics site. Every number is "
      "recalculated each time the report is built, so it updates as new games are added. Box scores can't measure "
      "defense by individual player or how roles change on a new team; film fills those gaps.</footer>")
    w("</main></body></html>")

    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / f"{slug(team_name)}.html"
    out_file.write_text("".join(out), encoding="utf-8")
    print(f"Report written to {out_file}")
    pdf_file, problem = save_pdf(out_file)
    if pdf_file:
        print(f"PDF written to    {pdf_file}")
    else:
        print(f"PDF not created ({problem}). Open the .html file, press Ctrl+P, and choose Save as PDF.")
    print(f"  Roster: {len(players)} players ({counts['Returning']} returning, "
          f"{counts['Transfer']} transfers, {counts['No data']} without D1 stats)")
    print(f"  Minutes returning: {fmt(continuity, 0, '%')}  (trust in last season: {trust})")
    print(f"  Shots charted for this roster last season: {zone_total}")


if __name__ == "__main__":
    main()
