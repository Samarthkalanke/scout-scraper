"""
build_site.py - Build the private scouting website from the finished reports.

Usage:
    python build_site.py

Reads:
    data/opponents_27_2027.json   UCR's schedule (fetch_opponents.py)
    reports/*.html, *.pdf         the reports (make_report.py)
    data/scout.db                 for each opponent's record

Writes the folder site/, rebuilt from scratch each run:
    site/index.html               home page: every game on the schedule, newest info first
    site/reports/...              copies of the reports and PDFs (previews left out)

The site folder is what gets uploaded to Cloudflare Pages. It's made by this
script, so it doesn't go on GitHub (add "site/" to .gitignore).

Preview it before uploading:  start site\\index.html
"""

import html
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from make_report import DB_FILE, OPP_FILE, OUT_DIR, SEASON, UCR_ID, slug

SITE_DIR = Path("site")


def esc(v):
    return html.escape(str(v))


def nice_date(iso):
    d = date.fromisoformat(iso)
    return f"{d:%a}, {d:%b} {d.day}"


def record(db, team_id, season):
    """W-L from the games in the database, or None if there are none."""
    wins = losses = 0
    for home_id, hs, as_ in db.execute(
            """SELECT home_id, home_score, away_score FROM games
               WHERE season = ? AND (home_id = ? OR away_id = ?)""", (season, team_id, team_id)):
        if hs is None or as_ is None:
            continue
        mine, theirs = (hs, as_) if home_id == team_id else (as_, hs)
        if mine > theirs:
            wins += 1
        elif theirs > mine:
            losses += 1
    return f"{wins}-{losses}" if wins + losses else None


CSS = """
:root{--ink:#14181f;--ink-soft:#4a5164;--paper:#f5f6f8;--surface:#fff;--line:#e2e4ea;--blue:#1e4dd8;--blue-soft:#eaefff;--muted-soft:#eef0f3;--shadow:0 1px 2px rgba(20,24,31,.04),0 8px 24px -12px rgba(20,24,31,.12)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eef0f4;--ink-soft:#a7adbd;--paper:#0e1116;--surface:#171b22;--line:#2a2f3a;--blue:#6f92ff;--blue-soft:#1b2647;--muted-soft:#232833;--shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px -12px rgba(0,0,0,.5)}}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:"Source Sans 3",ui-sans-serif,system-ui,sans-serif;margin:0;padding:28px 16px 48px;display:flex;justify-content:center}
main{width:100%;max-width:900px;display:flex;flex-direction:column;gap:24px}
h1,h2{font-family:"Oswald",ui-sans-serif,sans-serif;margin:0}
header{border-bottom:3px solid var(--ink);padding-bottom:14px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:8px}
.eyebrow{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--blue);font-weight:600;margin:0 0 4px}
header h1{font-size:clamp(26px,5vw,34px);font-weight:700}
.meta{font-size:13px;color:var(--ink-soft)}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;color:var(--ink-soft);margin-bottom:10px}
.next{background:var(--surface);border:1px solid var(--line);border-left:5px solid var(--blue);border-radius:12px;padding:18px 20px;box-shadow:var(--shadow);display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:14px}
.next .when{font-size:13px;color:var(--blue);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.next .name{font-family:"Oswald",sans-serif;font-size:28px;font-weight:600;margin:2px 0}
.list{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
.row{display:grid;grid-template-columns:120px 1fr auto;align-items:center;gap:12px;padding:12px 16px}
.row:not(:last-child){border-bottom:1px solid var(--line)}
.row.past{opacity:.55}
.date{font-size:13px;color:var(--ink-soft);font-variant-numeric:tabular-nums}
.team{font-weight:600}
.rec{font-size:12.5px;color:var(--ink-soft);font-weight:400;margin-top:2px}
.links{display:flex;gap:8px}
a.btn{display:inline-block;font-size:13px;font-weight:600;padding:6px 12px;border-radius:8px;text-decoration:none;background:var(--blue);color:#fff}
a.btn.ghost{background:var(--blue-soft);color:var(--blue)}
.none{font-size:12.5px;color:var(--ink-soft);background:var(--muted-soft);padding:5px 10px;border-radius:8px}
footer{font-size:12.5px;color:var(--ink-soft);line-height:1.6;border-top:1px solid var(--line);padding-top:14px}
@media (max-width:600px){.row{grid-template-columns:1fr;gap:6px}.links{margin-top:4px}}
"""


def main():
    if not OPP_FILE.exists():
        raise SystemExit(f"Missing {OPP_FILE}. Run fetch_opponents.py first.")
    opponents = json.loads(OPP_FILE.read_text(encoding="utf-8"))
    db = sqlite3.connect(DB_FILE) if DB_FILE.exists() else None

    # Fresh site folder, with copies of the real reports (not previews)
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / "reports").mkdir(parents=True)
    copied = 0
    for f in sorted(OUT_DIR.glob("*")) if OUT_DIR.exists() else []:
        if f.suffix in (".html", ".pdf") and "-preview" not in f.stem:
            shutil.copy2(f, SITE_DIR / "reports" / f.name)
            copied += 1

    # One row per game on the schedule (a conference opponent appears twice)
    today = date.today().isoformat()
    games = sorted((d, tid, info["name"]) for tid, info in opponents.items() for d in info["dates"])
    rows, next_game = [], None
    for d, tid, name in games:
        s = slug(name)
        has_report = (SITE_DIR / "reports" / f"{s}.html").exists()
        has_pdf = (SITE_DIR / "reports" / f"{s}.pdf").exists()
        last = record(db, tid, SEASON - 1) if db else None
        cur = record(db, tid, SEASON) if db else None
        game = {"date": d, "name": name, "slug": s, "report": has_report, "pdf": has_pdf,
                "last": last, "cur": cur, "past": d < today}
        rows.append(game)
        if next_game is None and not game["past"]:
            next_game = game
    if db:
        db.close()

    def links(g):
        if not g["report"]:
            return "<span class='none'>No report yet</span>"
        out = f"<a class='btn' href='reports/{g['slug']}.html'>View report</a>"
        if g["pdf"]:
            out += f"<a class='btn ghost' href='reports/{g['slug']}.pdf'>PDF</a>"
        return out

    def records(g):
        parts = []
        if g["cur"]:
            parts.append(f"{g['cur']} this season")
        if g["last"]:
            parts.append(f"{g['last']} last season")
        return " · ".join(parts)

    out = []
    w = out.append
    w("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
      "<meta name='viewport' content='width=device-width,initial-scale=1'>"
      "<title>UCR Scouting</title>"
      "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700"
      "&family=Source+Sans+3:wght@400;600;700&display=swap'>"
      f"<style>{CSS}</style></head><body><main>")
    w("<header><div><p class='eyebrow'>UC Riverside Men's Basketball</p><h1>Opponent Scouting</h1></div>"
      f"<div class='meta'>Updated {date.today():%b} {date.today().day}, {date.today():%Y}</div></header>")

    if next_game:
        w("<section><h2>Next game</h2><div class='next'><div>"
          f"<div class='when'>{esc(nice_date(next_game['date']))}</div>"
          f"<div class='name'>{esc(next_game['name'])}</div>"
          f"<div class='rec'>{esc(records(next_game))}</div></div>"
          f"<div class='links'>{links(next_game)}</div></div></section>")

    w("<section><h2>Full schedule</h2><div class='list'>")
    for g in rows:
        w(f"<div class='row{' past' if g['past'] else ''}'>"
          f"<div class='date'>{esc(nice_date(g['date']))}</div>"
          f"<div class='team'>{esc(g['name'])}<div class='rec'>{esc(records(g))}</div></div>"
          f"<div class='links'>{links(g)}</div></div>")
    w("</div></section>")

    w("<footer>Reports are built from ESPN box scores and play-by-play and update after every game. "
      "Played games are faded. \"No report yet\" means the team isn't covered by ESPN or its roster "
      "hasn't been posted.</footer></main></body></html>")

    (SITE_DIR / "index.html").write_text("".join(out), encoding="utf-8")
    print(f"Built {SITE_DIR / 'index.html'}: {len(rows)} games, {copied} report files copied")
    if next_game:
        print(f"  Next game: {next_game['name']} on {next_game['date']}"
              + ("" if next_game["report"] else "  (no report yet)"))


if __name__ == "__main__":
    main()
