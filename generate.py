#!/usr/bin/env python3
"""Render the profile cards in assets/ from live GitHub data.

Five cards, each in a dark and a light variant:
  bridge-*.svg   identity, radar of public repos, focus, telemetry, languages
  captain-*.svg  the longer "about me", as a ship's log
  fleet-*.svg    public repos, newest first, straight from the GitHub API
  engine-*.svg   tool stack as icons (skillicons.dev, inlined at build time)
  sonar-*.svg    52-week contribution trace

Data sources, in order of preference:
  contributions  GraphQL (needs GH_TOKEN)  ->  public contributions page
  repos/langs    REST API (token optional)
If a source fails, the last good snapshot in assets/stats.json is reused.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request
import zlib
from html import escape
from pathlib import Path

USER = "nenikolaidis"
ASSETS = Path("assets")
CACHE = ASSETS / "stats.json"

NAME = "NEARCHOS NIKOLAIDIS"
ROLE = "IT Engineer · Data Engineering · DevOps"
MOTTO = "charting better ways to build"
POSITION = "Greece · UTC+2"

HEADING = ["data engineering", "data analysis", "devops · automation", "cloud · azure"]
# (what, where, blinking cursor after where)
ON_DUTY = [
    ("it engineer", "infrastructure · support", False),
    ("always learning", "studying · building new things", False),
    ("building", "uncharted waters", True),
]

# (label, palette colour, icons). Plain ids come from skillicons.dev;
# "si:<slug>" ids are Simple Icons logos drawn on a matching tile.
ENGINE_ROOM = [
    ("languages", "accent", ["py", "java", "c", "js", "php"]),
    ("scripting · web", "accent", ["bash", "powershell", "html", "css"]),
    ("data", "amber", ["postgres", "mysql", "mongodb", "sqlite", "si:pandas", "si:numpy"]),
    ("devops · tools", "blue", ["docker", "git", "github", "githubactions", "vscode"]),
    ("cloud · systems", "blue", ["azure", "linux", "windows", "si:vmware", "si:virtualbox"]),
    ("network · familiar", "dim", ["si:cisco", "si:wireshark"]),
]
ALSO = "active directory · excel"  # no icon for these
SIMPLE_ICONS = "https://cdn.jsdelivr.net/npm/simple-icons@16.34.0"
TILE = {"dark": "#242938", "light": "#F4F2ED"}  # skillicons' own tile colours
SPOKEN = "greek (native) · english (C2) · french (C2)"

BRIDGE_LOG = [
    ("HELM", "keeping infrastructure running"),
    ("ENGINE", "automating repetitive work"),
    ("SONAR", "turning raw data into answers"),
]

# (key, palette colour, lines)
CAPTAIN_LOG = [
    ("mission", "accent", ["Start with the problem, understand the system,",
                           "build the solution, then improve it."]),
    ("on_watch", "amber", ["IT engineer: keeping systems, networks and people running.",
                           "Troubleshooting hardware, software and infrastructure."]),
    ("off_watch", "port", ["Side projects, a homelab to break and fix,",
                           "and always something new to learn."]),
    ("home_port", "blue", ["Greece. Named after an admiral who mapped",
                           "unknown coastlines; I map unknown systems."]),
]

W = 880
PAD = 24
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

PALETTES = {
    "dark": {
        "bg": "#0a1622",
        "grid": "rgba(120,170,210,0.06)",
        "panel": "rgba(120,170,210,0.04)",
        "border": "rgba(120,170,210,0.16)",
        "text": "#d8e3ec",
        "dim": "#6f8599",
        "accent": "#3fd0c9",
        "amber": "#ffb547",
        "port": "#ff5d5d",
        "starboard": "#4ade80",
        "blue": "#5aa9ff",
        "track": "rgba(120,170,210,0.10)",
    },
    "light": {
        "bg": "#f3f6f9",
        "grid": "rgba(20,60,100,0.06)",
        "panel": "rgba(20,60,100,0.03)",
        "border": "rgba(20,60,100,0.16)",
        "text": "#13212e",
        "dim": "#5d7184",
        "accent": "#0f8a84",
        "amber": "#b86e00",
        "port": "#c62828",
        "starboard": "#15803d",
        "blue": "#1565c0",
        "track": "rgba(20,60,100,0.10)",
    },
}

LANG_COLORS = {
    "Python": "#3776AB", "JavaScript": "#F1E05A", "TypeScript": "#3178C6",
    "PHP": "#4F5D95", "Java": "#B07219", "C": "#A8B9CC", "C++": "#F34B7D",
    "HTML": "#E34C26", "CSS": "#663399", "Shell": "#89E051", "SQL": "#E38C00",
    "Dockerfile": "#384D54", "HCL": "#844FBA", "Go": "#00ADD8",
}


# --------------------------------------------------------------------------- data

def _request(url: str, data: bytes | None = None, accept: str = "application/vnd.github+json") -> bytes:
    headers = {"Accept": accept, "User-Agent": f"{USER}-profile-card"}
    token = os.environ.get("GH_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def gh_api(path: str):
    return json.loads(_request(f"https://api.github.com/{path}"))


CONTRIB_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_contributions() -> dict[str, int]:
    """Daily contribution counts for the last year, keyed by ISO date."""
    if os.environ.get("GH_TOKEN"):
        try:
            body = json.dumps({"query": CONTRIB_QUERY, "variables": {"login": USER}}).encode()
            data = json.loads(_request("https://api.github.com/graphql", data=body))
            weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
            return {d["date"]: d["contributionCount"] for w in weeks for d in w["contributionDays"]}
        except Exception as e:  # fall through to the public page
            print(f"warn: graphql contributions failed ({e})", file=sys.stderr)

    html = _request(f"https://github.com/users/{USER}/contributions", accept="text/html").decode()
    dates = dict(re.findall(r'data-date="([\d-]+)" id="([\w-]+)"', html))
    dates = {cell: date for date, cell in dates.items()}
    days = {}
    for cell, label in re.findall(r'for="([\w-]+)"[^>]*>([^<]*)</tool-tip>', html):
        if cell in dates:
            m = re.match(r"(\d+) contribution", label)
            days[dates[cell]] = int(m.group(1)) if m else 0
    if not days:
        raise RuntimeError("contributions page had no calendar cells")
    return days


def fetch_repos() -> tuple[list[dict], dict[str, int]]:
    """Public, non-fork repos (excluding this one) and summed language bytes."""
    repos = [
        {"name": r["name"], "pushed_at": r["pushed_at"], "description": r["description"],
         "homepage": r["homepage"] if (r["homepage"] or "").startswith("http") else None,
         "language": r["language"], "stars": r["stargazers_count"]}
        for r in gh_api(f"users/{USER}/repos?per_page=100&type=owner&sort=pushed")
        if not r["fork"] and not r["archived"] and r["name"].lower() != USER.lower()
    ]
    langs: dict[str, int] = {}
    for r in repos:
        for lang, size in gh_api(f"repos/{USER}/{r['name']}/languages").items():
            langs[lang] = langs.get(lang, 0) + size
    return repos, langs


def collect() -> dict:
    cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    stats = dict(cached)
    try:
        stats["days"] = fetch_contributions()
    except Exception as e:
        print(f"warn: contributions unavailable ({e}); using cache", file=sys.stderr)
    try:
        stats["repos"], stats["langs"] = fetch_repos()
    except Exception as e:
        print(f"warn: repos unavailable ({e}); using cache", file=sys.stderr)
    if "days" not in stats or "repos" not in stats:
        raise SystemExit("error: no live data and no cache to fall back on")
    return stats


def summarize(days: dict[str, int]) -> dict:
    ordered = sorted(days.items())
    counts = [c for _, c in ordered]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current = 0
    tail = counts[:-1] if counts and counts[-1] == 0 else counts  # today may still be empty
    for c in reversed(tail):
        if not c:
            break
        current += 1
    weeks = [sum(counts[i:i + 7]) for i in range(0, len(counts), 7)]
    week_starts = [ordered[i][0] for i in range(0, len(ordered), 7)]
    return {
        "year": sum(counts),
        "month": sum(counts[-30:]),
        "longest": longest,
        "current": current,
        "weeks": weeks,
        "week_starts": week_starts,
    }


# ------------------------------------------------------------------------ drawing

def t(x, y, s, size=13, fill=None, weight=400, anchor="start", extra="") -> str:
    paint = f' fill="{fill}"' if fill else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}"{paint} '
            f'text-anchor="{anchor}" {extra}>{escape(s)}</text>')


def panel(x, y, w, h, label, p, note="") -> str:
    tag = f"[ {label} ]"
    out = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" '
           f'fill="{p["panel"]}" stroke="{p["border"]}"/>'
           + t(x + 14, y + 22, tag, 11, p["accent"], 700, extra='letter-spacing="1.5"'))
    if note:
        out += t(x + 14 + len(tag) * (11 * 0.6 + 1.5) + 8, y + 22, note, 11, p["dim"])
    return out


def frame(h: int, title: str, desc: str, p: dict, body: str, style: str = "") -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title>
<desc id="desc">{escape(desc)}</desc>
<style>
text {{ font-family: {MONO}; }}
{style}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
</style>
<defs>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
<path d="M 40 0 L 0 0 0 40" fill="none" stroke="{p['grid']}"/>
</pattern>
</defs>
<rect x="0.5" y="0.5" width="{W - 1}" height="{h - 1}" rx="14" fill="{p['bg']}" stroke="{p['border']}"/>
<rect x="0.5" y="0.5" width="{W - 1}" height="{h - 1}" rx="14" fill="url(#grid)"/>
<g fill="{p['text']}">
{body}
</g>
</svg>
"""


def radar(cx: float, cy: float, r: float, repos: list[dict], p: dict, today: dt.date) -> str:
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{p["panel"]}" stroke="{p["border"]}"/>']
    for f in (0.66, 0.33):
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r * f:.1f}" fill="none" stroke="{p["border"]}"/>')
    out.append(f'<path d="M {cx - r} {cy} H {cx + r} M {cx} {cy - r} V {cy + r}" stroke="{p["border"]}"/>')

    # sweep: a fading wedge rotating around the centre
    a = math.radians(-55)
    x2, y2 = cx + r * math.cos(a), cy + r * math.sin(a)
    out.append(
        f'<g class="sweep"><path d="M {cx} {cy} L {cx + r} {cy} A {r} {r} 0 0 0 {x2:.1f} {y2:.1f} Z" '
        f'fill="url(#sweepfill)"/><line x1="{cx}" y1="{cy}" x2="{cx + r}" y2="{cy}" '
        f'stroke="{p["accent"]}" stroke-width="1.5"/></g>')

    # one blip per repo: bearing from its name, range from how recently it was pushed
    for i, repo in enumerate(repos):
        pushed = dt.date.fromisoformat(repo["pushed_at"][:10])
        age = min((today - pushed).days / 365, 1)
        dist = r * (0.18 + 0.72 * age)
        bearing = math.radians(zlib.crc32(repo["name"].encode()) % 360)
        bx, by = cx + dist * math.cos(bearing), cy + dist * math.sin(bearing)
        out.append(f'<circle class="blip" style="animation-delay:{i * 0.7:.1f}s" cx="{bx:.1f}" '
                   f'cy="{by:.1f}" r="3" fill="{p["accent"]}"/>')
    out.append(f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="{p["amber"]}"/>')
    return "\n".join(out)


ANIMATIONS = """
.sweep { transform-origin: 100px 104px; animation: spin 6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.cursor { animation: blink 1.1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.blip { animation: pulse 4.2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
.boot { animation: boot 0.5s ease-out backwards; }
@keyframes boot { from { opacity: 0; transform: translateX(-6px); } }
"""


def render_bridge(theme: str, stats: dict, s: dict, today: dt.date) -> str:
    p = PALETTES[theme]
    b = [f'<defs><linearGradient id="sweepfill" x1="1" y1="0" x2="0.4" y2="-0.6">'
         f'<stop offset="0" stop-color="{p["accent"]}" stop-opacity="0.45"/>'
         f'<stop offset="1" stop-color="{p["accent"]}" stop-opacity="0"/></linearGradient></defs>']

    # header
    b.append(radar(100, 104, 66, stats["repos"], p, today))
    x = 200
    b.append(t(x, 46, "[ NAV-01 ] bridge console", 11, p["dim"], extra='letter-spacing="1.5"'))
    b.append(t(x, 82, NAME, 30, None, 700, extra='letter-spacing="3"'))
    b.append(t(x, 110, ROLE, 15))
    b.append(t(x, 138, "> ", 15, p["accent"], 700) + t(x + 18, 138, MOTTO, 15, p["accent"]))
    b.append(f'<rect class="cursor" x="{x + 18 + len(MOTTO) * 9.03 + 4:.1f}" y="126" width="8" height="15" fill="{p["accent"]}"/>')
    b.append(t(x, 164, "◎ " + POSITION, 12, p["dim"]))
    b.append(f'<circle cx="{W - PAD - 92}" cy="42" r="4" fill="{p["starboard"]}" class="blip"/>'
             + t(W - PAD, 46, "UNDERWAY", 11, p["starboard"], 700, "end", 'letter-spacing="1.5"'))

    # boot sequence
    b.append(f'<line x1="{PAD}" y1="188" x2="{W - PAD}" y2="188" stroke="{p["border"]}"/>')
    for i, (unit, msg) in enumerate(BRIDGE_LOG):
        yy = 214 + i * 22
        b.append(f'<g class="boot" style="animation-delay:{0.3 + i * 0.6:.1f}s">'
                 + t(PAD, yy, f"[{unit}]", 13, p["dim"]) + t(PAD + 84, yy, msg, 13)
                 + t(W - PAD, yy, "[ OK ]", 13, p["starboard"], 700, "end", 'xml:space="preserve"')
                 + "</g>")

    # three panels
    y, h, gap = 282, 132, 16
    pw = (W - 2 * PAD - 2 * gap) / 3
    cols = [PAD + i * (pw + gap) for i in range(3)]

    b.append(panel(cols[0], y, pw, h, "HEADING", p))
    for i, item in enumerate(HEADING):
        b.append(t(cols[0] + 14, y + 50 + i * 22, "▸ ", 13, p["amber"]) + t(cols[0] + 32, y + 50 + i * 22, item, 13))

    b.append(panel(cols[1], y, pw, h, "TELEMETRY", p))
    rows = [("contrib · 12mo", s["year"]), ("contrib · 30d", s["month"]), ("public repos", len(stats["repos"]))]
    for i, (k, v) in enumerate(rows):
        yy = y + 54 + i * 30
        b.append(t(cols[1] + 14, yy, k, 12, p["dim"]) + t(cols[1] + pw - 14, yy + 2, str(v), 22, None, 700, "end"))

    b.append(panel(cols[2], y, pw, h, "ON DUTY", p))
    for i, (what, where, cursor) in enumerate(ON_DUTY):
        yy = y + 46 + i * 31
        b.append(t(cols[2] + 14, yy, what, 13) + t(cols[2] + 14, yy + 14, "@ " + where, 11, p["dim"]))
        if cursor:
            cx = cols[2] + 14 + (len(where) + 2) * 6.6 + 3
            b.append(f'<rect class="cursor" x="{cx:.1f}" y="{yy + 5}" width="7" height="11" fill="{p["amber"]}"/>')

    # languages manifest: segmented bar, then a 3-column grid with per-language bars
    y2 = y + h + gap
    total = sum(stats["langs"].values()) or 1
    ranked = sorted(stats["langs"].items(), key=lambda kv: -kv[1])
    top = ranked[:6]
    grid_rows = (len(top) + 2) // 3
    h2 = 70 + grid_rows * 30
    kb = total / 1024
    b.append(panel(PAD, y2, W - 2 * PAD, h2, "MANIFEST", p,
                   f"{len(ranked)} languages · {kb:,.0f} KB across public repos"))
    bx, bw = PAD + 14, W - 2 * PAD - 28
    cur = bx
    for lang, size in ranked:
        seg = bw * size / total
        if seg >= 3:
            b.append(f'<rect x="{cur:.1f}" y="{y2 + 36}" width="{seg - 2:.1f}" height="10" rx="2" '
                     f'fill="{LANG_COLORS.get(lang, p["dim"])}"/>')
        cur += seg
    cw = bw / 3
    lead = top[0][1] if top else 1
    for i, (lang, size) in enumerate(top):
        cx, cy = bx + (i % 3) * cw, y2 + 74 + (i // 3) * 30
        color = LANG_COLORS.get(lang, p["dim"])
        b.append(f'<circle cx="{cx + 4}" cy="{cy - 4}" r="4" fill="{color}"/>')
        b.append(t(cx + 14, cy, lang, 12))
        pct = 100 * size / total
        b.append(t(cx + cw - 24, cy, f"{pct:.1f}%" if pct >= 0.1 else "<0.1%", 12, p["accent"], 700, "end"))
        b.append(f'<rect x="{cx + 14}" y="{cy + 7}" width="{cw - 38:.1f}" height="3" rx="1.5" fill="{p["track"]}"/>')
        b.append(f'<rect x="{cx + 14}" y="{cy + 7}" width="{(cw - 38) * size / lead:.1f}" height="3" rx="1.5" fill="{color}"/>')

    # footer
    fy = y2 + h2 + 28
    b.append(t(PAD, fy, f"> last_sync {today.isoformat()}", 11, p["dim"]))
    b.append(t(W - PAD, fy, f"github.com/{USER}", 11, p["dim"], anchor="end"))

    desc = (f"{ROLE}. {MOTTO}. Based in Greece. "
            f"{s['year']} contributions in the last year across {len(stats['repos'])} public repositories.")
    return frame(fy + 20, f"{NAME} profile card", desc, p, "\n".join(b), ANIMATIONS)


def render_captain(theme: str) -> str:
    p = PALETTES[theme]
    b = []
    y = PAD + 58
    for i, (key, color, lines) in enumerate(CAPTAIN_LOG):
        delay = f'style="animation-delay:{0.2 + i * 0.4:.1f}s"'
        b.append(f'<g class="boot" {delay}>' + t(PAD + 18, y, "> ", 14, p["dim"]) + t(PAD + 36, y, key, 14, p[color], 700))
        for j, line in enumerate(lines):
            b.append(t(PAD + 44, y + 22 + j * 21, line, 13))
        b.append("</g>")
        y += 22 + len(lines) * 21 + 18
    h = y + PAD - 22
    b.insert(0, panel(PAD, PAD, W - 2 * PAD, h - 2 * PAD, "CAPTAIN'S LOG", p, "$ cat captain.log"))
    b.insert(1, f'<line x1="{PAD + 14}" y1="{PAD + 34}" x2="{W - PAD - 14}" y2="{PAD + 34}" stroke="{p["border"]}"/>')
    desc = " ".join(f"{key}: {' '.join(lines)}" for key, _, lines in CAPTAIN_LOG)
    return frame(h, "Captain's log", desc, p, "\n".join(b), ANIMATIONS)


def ago(iso: str, today: dt.date) -> str:
    days = (today - dt.date.fromisoformat(iso[:10])).days
    if days < 1:
        return "today"
    if days < 14:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    if days < 730:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def render_fleet(theme: str, repos: list[dict], today: dt.date, limit: int = 4) -> str:
    """The most recently pushed public repos. Descriptions come from each repo's GitHub 'About'."""
    p = PALETTES[theme]
    ships = sorted(repos, key=lambda r: r["pushed_at"], reverse=True)
    shown = ships[:limit]
    row = 46
    h = PAD + 52 + max(len(shown), 1) * row + (20 if len(ships) > limit else 0) + PAD - 6
    b = [panel(PAD, PAD, W - 2 * PAD, h - 2 * PAD, "FLEET", p,
               f"latest {len(shown)} of {len(ships)} public repos · auto-discovered")]
    x0, x1 = PAD + 14, W - PAD - 14
    for i, r in enumerate(shown):
        y = PAD + 58 + i * row
        if i:
            b.append(f'<line x1="{x0}" y1="{y - 20}" x2="{x1}" y2="{y - 20}" stroke="{p["border"]}" stroke-dasharray="2 4"/>')
        lang = r.get("language") or "—"
        b.append(f'<circle cx="{x0 + 4}" cy="{y - 4}" r="4" fill="{LANG_COLORS.get(lang, p["dim"])}"/>')
        b.append(t(x0 + 16, y, r["name"], 14, None, 700))
        if r.get("homepage"):  # the repo's "Website" field marks it as live
            bx = x0 + 16 + len(r["name"]) * 8.45 + 12
            b.append(f'<rect x="{bx:.1f}" y="{y - 12}" width="52" height="16" rx="8" fill="none" stroke="{p["starboard"]}"/>'
                     f'<circle class="blip" cx="{bx + 11:.1f}" cy="{y - 4}" r="3" fill="{p["starboard"]}"/>'
                     + t(bx + 19, y, "LIVE", 10, p["starboard"], 700, extra='letter-spacing="1"'))
        meta = f'{lang} · {ago(r["pushed_at"], today)}'
        if r.get("stars"):
            meta = f'★ {r["stars"]} · ' + meta
        b.append(t(x1, y, meta, 11, p["dim"], anchor="end"))
        if r.get("description"):
            b.append(t(x0 + 16, y + 18, clip(r["description"], 104), 12, p["dim"]))
        else:
            b.append(t(x0 + 16, y + 18, "~ no log entry yet ~", 12, p["dim"], extra='font-style="italic" opacity="0.6"'))
    if not shown:
        b.append(t(x0, PAD + 58, "~ harbour empty ~", 13, p["dim"]))
    if len(ships) > limit:
        b.append(t(x1, h - PAD - 10, f"+ {len(ships) - limit} more at github.com/{USER}", 11, p["dim"], anchor="end"))
    desc = "Public repositories: " + "; ".join(
        f'{r["name"]}' + (f' ({r["description"]})' if r.get("description") else "") for r in shown)
    return frame(h, "Fleet: public repositories", desc, p, "\n".join(b), ANIMATIONS)


_icon_cache: dict[tuple[str, str], str] = {}
_si_colors: dict[str, str] = {}


def _luminance(hex6: str) -> float:
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def icon_markup(icon: str, theme: str) -> str:
    """Inner markup of one 256x256 icon tile."""
    key = (icon, theme)
    if key in _icon_cache:
        return _icon_cache[key]
    if icon.startswith("si:"):
        slug = icon[3:]
        if not _si_colors:
            for entry in json.loads(_request(f"{SIMPLE_ICONS}/data/simple-icons.json", accept="application/json")):
                _si_colors[entry["slug"]] = entry["hex"]
        color = _si_colors[slug]
        # keep very dark or very light brand colours visible on the tile
        if theme == "dark" and _luminance(color) < 0.25:
            color = "D8E3EC"
        if theme == "light" and _luminance(color) > 0.85:
            color = "13212E"
        svg = _request(f"{SIMPLE_ICONS}/icons/{slug}.svg", accept="image/svg+xml").decode()
        path = re.search(r'<path d="([^"]+)"', svg).group(1)
        inner = (f'<rect width="256" height="256" rx="60" fill="{TILE[theme]}"/>'
                 f'<g transform="translate(56 56) scale(6)"><path d="{path}" fill="#{color}"/></g>')
    else:
        svg = _request(f"https://skillicons.dev/icons?i={icon}&theme={theme}", accept="image/svg+xml").decode()
        m = re.search(r'<svg[^>]*viewBox="0 0 256 256"[^>]*>', svg)
        if not m or len(svg) < 400:
            raise RuntimeError(f"skillicons has no icon '{icon}'")
        inner = svg[m.end(): svg.rstrip().rfind("</svg>")]
    _icon_cache[key] = inner
    return inner


def render_engine(theme: str) -> str:
    p = PALETTES[theme]
    icon, pitch = 48, 58
    col_w = (W - 2 * PAD - 28 - 24) / 2
    b = [t(PAD + 14, PAD + 52, "$ ", 13, p["dim"]) + t(PAD + 30, PAD + 52, "ls /engine-room", 13)]
    top = PAD + 66
    b.append(f'<line x1="{PAD + 14}" y1="{top}" x2="{W - PAD - 14}" y2="{top}" stroke="{p["border"]}"/>')
    row_h = 92
    for i, (label, color, icons) in enumerate(ENGINE_ROOM):
        x = PAD + 14 + (i % 2) * (col_w + 24)
        y = top + 28 + (i // 2) * row_h
        b.append(t(x, y, "> ", 13, p["dim"]) + t(x + 16, y, label, 13, p[color], 700))
        for j, ic in enumerate(icons):
            b.append(f'<svg x="{x + j * pitch:.1f}" y="{y + 12}" width="{icon}" height="{icon}" '
                     f'viewBox="0 0 256 256">{icon_markup(ic, theme)}</svg>')
    y = top + 28 + ((len(ENGINE_ROOM) + 1) // 2) * row_h
    b.append(f'<line x1="{PAD + 14}" y1="{y - 22}" x2="{W - PAD - 14}" y2="{y - 22}" stroke="{p["border"]}"/>')
    b.append(t(PAD + 14, y, "> ", 13, p["dim"]) + t(PAD + 30, y, "also", 13, p["amber"], 700)
             + t(PAD + 100, y, ALSO, 13))
    y += 24
    b.append(t(PAD + 14, y, "> ", 13, p["dim"]) + t(PAD + 30, y, "spoken", 13, p["port"], 700)
             + t(PAD + 100, y, SPOKEN, 13))
    h = y + 22 + PAD
    b.insert(0, panel(PAD, PAD, W - 2 * PAD, h - 2 * PAD, "ENGINE ROOM", p, "tools that keep the ship moving"))
    desc = "Tool stack: " + "; ".join(
        f"{label}: {', '.join(ic.removeprefix('si:') for ic in icons)}" for label, _, icons in ENGINE_ROOM
    ) + f"; also: {ALSO}; spoken: {SPOKEN}"
    return frame(h, "Engine room: tool stack", desc, p, "\n".join(b), ANIMATIONS)


def render_sonar(theme: str, s: dict) -> str:
    p = PALETTES[theme]
    h = 196
    b = [panel(PAD, PAD, W - 2 * PAD, h - 2 * PAD, "SONAR", p, "contributions per week · last 52 weeks")]
    summary = f'{s["year"]} total · best week {max(s["weeks"], default=0)} · streak {s["current"]}d · longest {s["longest"]}d'
    b.append(t(W - PAD - 14, PAD + 22, summary, 11, p["accent"], 700, "end"))

    weeks = s["weeks"]
    cx0, cx1 = PAD + 14, W - PAD - 14
    cy0, cy1 = PAD + 40, h - PAD - 26
    top = max(max(weeks, default=0), 1)
    for f in (0, 0.5, 1):
        yy = cy1 - (cy1 - cy0) * f
        b.append(f'<line x1="{cx0}" y1="{yy:.1f}" x2="{cx1}" y2="{yy:.1f}" stroke="{p["border"]}" stroke-dasharray="2 4"/>')
    n = max(len(weeks) - 1, 1)
    pts = [(cx0 + (cx1 - cx0) * i / n, cy1 - (cy1 - cy0) * v / top) for i, v in enumerate(weeks)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    b.append(f'<defs><linearGradient id="depth" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{p["accent"]}" stop-opacity="0.35"/>'
             f'<stop offset="1" stop-color="{p["accent"]}" stop-opacity="0"/></linearGradient></defs>')
    if pts:
        b.append(f'<polygon points="{cx0},{cy1} {line} {cx1},{cy1}" fill="url(#depth)"/>')
        b.append(f'<polyline points="{line}" fill="none" stroke="{p["accent"]}" stroke-width="1.8" stroke-linejoin="round"/>')
        lx, ly = pts[-1]
        b.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{p["amber"]}" class="blip"/>')

    # month ticks where a new month starts
    last = None
    for i, start in enumerate(s["week_starts"]):
        month = start[:7]
        if month != last and last is not None:
            x = cx0 + (cx1 - cx0) * i / n
            label = dt.date.fromisoformat(start).strftime("%b").lower()
            b.append(t(x, cy1 + 16, label, 10, p["dim"], anchor="middle"))
        last = month

    desc = (f"{s['year']} contributions in the last 52 weeks; best week {max(weeks, default=0)}; "
            f"longest streak {s['longest']} days.")
    return frame(h, "Contribution sonar", desc, p, "\n".join(b), ANIMATIONS)


README = Path("README.md")
LIVE_START, LIVE_END = "<!-- live:start -->", "<!-- live:end -->"


def update_readme(repos: list[dict]) -> None:
    """Rewrite the clickable live-demo line between the README markers."""
    if not README.exists():
        return
    text = README.read_text()
    if LIVE_START not in text or LIVE_END not in text:
        return
    live = sorted((r for r in repos if r.get("homepage")), key=lambda r: r["pushed_at"], reverse=True)
    links = " · ".join(f'<a href="{escape(r["homepage"])}">{escape(r["name"].removesuffix(".github.io"))}</a>'
                       for r in live)
    block = f"<sub>`▶ live` {links}</sub>" if live else ""
    head, rest = text.split(LIVE_START, 1)
    _, tail = rest.split(LIVE_END, 1)
    README.write_text(f"{head}{LIVE_START}\n{block}\n{LIVE_END}{tail}")


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    stats = collect()
    CACHE.write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n")
    s = summarize(stats["days"])
    today = dt.date.today()
    for theme in PALETTES:
        (ASSETS / f"bridge-{theme}.svg").write_text(render_bridge(theme, stats, s, today))
        (ASSETS / f"captain-{theme}.svg").write_text(render_captain(theme))
        (ASSETS / f"fleet-{theme}.svg").write_text(render_fleet(theme, stats["repos"], today))
        try:
            (ASSETS / f"engine-{theme}.svg").write_text(render_engine(theme))
        except Exception as e:  # keep yesterday's card rather than fail the run
            print(f"warn: engine room not refreshed ({e})", file=sys.stderr)
        (ASSETS / f"sonar-{theme}.svg").write_text(render_sonar(theme, s))
    update_readme(stats["repos"])
    print(f"ok: {s['year']} contributions, {len(stats['repos'])} repos, {len(stats['langs'])} languages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
