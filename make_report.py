"""
make_report.py - Build a scouting report for one opponent, straight from the database.

Usage:
    python make_report.py denver
    python make_report.py "san jose"
    python make_report.py 2172           (ESPN team ID also works)

Writes reports/<team>.html. Open it in any browser:
    start reports\\denver-pioneers.html

Needs (all made by earlier scripts):
    data/scout.db               build_db.py
    data/rosters_2027.csv       label_rosters.py
    data/opponents_27_2027.json fetch_opponents.py

Every number is calculated fresh from the database each time this runs, so after
new games are downloaded and build_db.py is rerun, the report updates itself.
"""

import csv
import html
import json
import math
import re
import sqlite3
import sys
import unicodedata
from datetime import date
from pathlib import Path

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
        "ft": pct(g(us, "freeThrowsMade"), g(us, "freeThrowsAttempted")),
        "opp_fg": pct(g(them, "fieldGoalsMade"), g(them, "fieldGoalsAttempted")),
        "opp_three": pct(g(them, "threePointFieldGoalsMade"), g(them, "threePointFieldGoalsAttempted")),
        "reb": g(us, "totalRebounds") / n, "opp_reb": g(them, "totalRebounds") / n,
        "to": g(us, "turnovers") / n, "opp_to": g(them, "turnovers") / n,
        "ast": g(us, "assists") / n,
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
        "ft": pct(totals.get("FTM", 0), totals.get("FTA", 0)),
        "three_rate": pct(totals.get("3PTA", 0), fga),
        "ft_rate": pct(totals.get("FTA", 0), fga),
    }


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
main{width:100%;max-width:980px;display:flex;flex-direction:column;gap:28px}
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
th,td{padding:9px 11px;text-align:right;white-space:nowrap}
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
.flag{font-size:11px;color:var(--warn);font-weight:600}
.bar{height:8px;border-radius:999px;background:var(--line);overflow:hidden;min-width:80px}
.bar>div{height:100%;background:var(--blue)}
.note{font-size:12.5px;color:var(--ink-soft);margin:0;line-height:1.55}
.callout{border-left:4px solid var(--warn);background:var(--warn-soft);padding:12px 14px;border-radius:8px;font-size:13.5px;line-height:1.5}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:700px){.two{grid-template-columns:1fr}.meta{text-align:left}}
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

    # --- roster turnover
    w("<section><h2>Roster turnover</h2><div class='tiles'>")
    w(f"<div class='tile'><div class='label'>Minutes returning</div><div class='value'>{fmt(continuity, 0, '%')}</div>"
      f"<div class='sub'>of last season's minutes</div></div>")
    w(f"<div class='tile'><div class='label'>Returning players</div><div class='value'>{counts['Returning']}</div></div>")
    w(f"<div class='tile'><div class='label'>D1 transfers with stats</div><div class='value'>{counts['Transfer']}</div></div>")
    w(f"<div class='tile'><div class='label'>No D1 stats</div><div class='value'>{counts['No data']}</div>"
      f"<div class='sub'>freshmen, redshirts, JUCO/D2</div></div>")
    w("</div>")
    trust_text = {
        "high": "Most of last season's team is back, so last season's team numbers below are a reasonable guide.",
        "medium": "About half the team is new. Use last season's team numbers with caution; the roster-based numbers are the better guide.",
        "low": "This is largely a new team. Last season's team numbers describe players who are mostly gone. Rely on the roster-based numbers instead.",
        "unknown": "Last season's minutes aren't available for this team.",
    }[trust]
    w(f"<div class='callout'><strong>How much to trust last season ({trust}):</strong> {esc(trust_text)}</div>")
    if former_ucr:
        names = ", ".join(r["player"] for r in former_ucr)
        w(f"<div class='callout'><strong>Former Highlanders:</strong> {esc(names)} played for UCR last season.</div>")
    w("</section>")

    # --- this season, once games exist
    if this_team:
        n = this_team["games"]
        flag = " <span class='flag'>small sample</span>" if n < 5 else ""
        w(f"<section><h2>This season so far ({this_team['wins']}-{this_team['losses']}, {n} games){flag}</h2><div class='tiles'>")
        for label, v, sub in [
            ("Points / game", fmt(this_team["ppg"]), f"allowed {fmt(this_team['opp_ppg'])}"),
            ("FG %", fmt(this_team["fg"], 1, "%"), f"opp. {fmt(this_team['opp_fg'], 1, '%')}"),
            ("3PT %", fmt(this_team["three"], 1, "%"), f"opp. {fmt(this_team['opp_three'], 1, '%')}"),
            ("Rebounds / game", fmt(this_team["reb"]), f"opp. {fmt(this_team['opp_reb'])}"),
            ("Turnovers / game", fmt(this_team["to"]), f"forced {fmt(this_team['opp_to'])}"),
        ]:
            w(f"<div class='tile'><div class='label'>{label}</div><div class='value'>{v}</div><div class='sub'>{sub}</div></div>")
        w("</div></section>")

    # --- roster table
    w(f"<section><h2>{SEASON - 1}-{str(SEASON)[2:]} roster, with each player's {LAST_SEASON - 1}-{str(LAST_SEASON)[2:]} stats</h2>"
      "<div class='panel scroll'><table><thead><tr>"
      "<th>#</th><th class='left'>Player</th><th class='left'>Pos</th><th class='left'>Ht</th><th class='left'>Yr</th>"
      "<th class='left'>Status</th><th class='left'>Last season at</th>"
      "<th>GP</th><th>MPG</th><th>PPG</th><th>RPG</th><th>APG</th><th>FG%</th><th>3PM-A/g</th><th>3P%</th><th>FT%</th>"
      "</tr></thead><tbody>")
    for r, t in players:
        gp = t.get("GP", 0)
        pill = "nodata" if r["status"] == "No data" else r["status"]
        small = " <span class='flag'>small sample</span>" if 0 < gp < SMALL_GAMES else ""
        where = r["last_season_team"] if r["status"] == "Transfer" else ("" if r["status"] == "Returning" else "")
        if r["status"] == "No data" and r["previous_schools"]:
            where = f"<span class='soft'>{esc(r['previous_schools'])}</span>"
        else:
            where = esc(where)
        if gp:
            stats = (f"<td>{int(gp)}</td><td>{fmt(per_game(t, 'MIN'))}</td><td>{fmt(per_game(t, 'PTS'))}</td>"
                     f"<td>{fmt(per_game(t, 'REB'))}</td><td>{fmt(per_game(t, 'AST'))}</td>"
                     f"<td>{fmt(pct(t.get('FGM', 0), t.get('FGA', 0)))}</td>"
                     f"<td>{fmt(per_game(t, '3PTM'))}-{fmt(per_game(t, '3PTA'))}</td>"
                     f"<td>{fmt(pct(t.get('3PTM', 0), t.get('3PTA', 0)))}</td>"
                     f"<td>{fmt(pct(t.get('FTM', 0), t.get('FTA', 0)))}</td>")
        else:
            stats = "<td class='soft' colspan='9' style='text-align:left'>no Division I stats last season</td>"
        w(f"<tr><td>{esc(r['jersey'])}</td><td class='name left'>{esc(r['player'])}{small}</td>"
          f"<td class='left'>{esc(r['position'])}</td><td class='left'>{esc(r['height'])}</td>"
          f"<td class='left'>{esc(r['class'])}</td><td class='left'><span class='pill {pill}'>{esc(r['status'])}</span></td>"
          f"<td class='left'>{where}</td>{stats}</tr>")
    w("</tbody></table></div>"
      "<p class='note'>Sorted by minutes played last season. Transfers' stats are from their previous D1 school. "
      "Players listed with no D1 stats are freshmen, redshirts, or came from junior college, Division II, NAIA or overseas, "
      "which ESPN doesn't cover.</p></section>")

    # --- roster profile vs last season's team vs UCR
    w("<section><h2>What this roster did last season, combined</h2><div class='panel scroll'><table><thead><tr>"
      f"<th class='left'>Per 40-minute game</th><th>{esc(team_name.split()[0])} roster</th>"
      f"<th>{esc(team_name.split()[0])} team {LAST_SEASON - 1}-{str(LAST_SEASON)[2:]}</th><th>UCR roster</th></tr></thead><tbody>")
    rows = [
        ("Points", "pts", "ppg", 1, ""), ("Rebounds", "reb", "reb", 1, ""),
        ("Assists", "ast", "ast", 1, ""), ("Turnovers", "to", "to", 1, ""),
        ("FG %", "fg", "fg", 1, "%"), ("3PT %", "three", "three", 1, "%"),
        ("FT %", "ft", "ft", 1, "%"), ("3PT attempts per FGA", "three_rate", None, 0, "%"),
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

    w("<section><h2>Where this roster shot from last season</h2><div class='two'>")
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
    print(f"  Roster: {len(players)} players ({counts['Returning']} returning, "
          f"{counts['Transfer']} transfers, {counts['No data']} without D1 stats)")
    print(f"  Minutes returning: {fmt(continuity, 0, '%')}  (trust in last season: {trust})")
    print(f"  Shots charted for this roster last season: {zone_total}")


if __name__ == "__main__":
    main()
