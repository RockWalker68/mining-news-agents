"""
page.py: turns the morning brief into a phone-friendly web page.

Layout, top to bottom:
  1. The Rat's poem as the headline
  2. Top stories (the three to five biggest of the day)
  3. Today's headlines, sorted into colored groups. Tap a group to see only it.
  4. Dr I Simon's takeaways under four headings
  5. Very short summaries from Meldrum and Yukon Cornelius
  6. Claims, notices & reviews: claim packages over 100 claims, BLM
     exploration notices, environmental reviews
  7. Filings & safety: the SEC filings and MSHA reports Dr I Simon chose
  8. The prediction scorecard
  9. Tap-to-expand extras: creative angles, the global view, world headlines,
     and the full discussions

A second page, research.html, lists new economic-geology papers with Yukon
Cornelius's notes. A tab at the top switches between the two.

Names come from prompts.py. Groups, colors and regions are set just below.
"""

import html
import re
import shutil
from datetime import datetime
from pathlib import Path
from string import Template

from prompts import STRATEGIST_NAME as S, GEOLOGIST_NAME as G, CHIEF_NAME as C, POET_NAME as R

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------

# Headline groups, in the order they appear: short name -> (label, light color, dark color).
# If you rename or add a group, update the list in SORT_HEADLINES in prompts.py to match.
GROUPS = {
    "production":   ("Production & operations",        "#b0602a", "#e08a4c"),
    "projects":     ("Projects & permitting",          "#2f6fb3", "#6aa9e9"),
    "exploration":  ("Exploration & discovery",        "#2e7d4f", "#5fc084"),
    "deals_policy": ("Deals, financing & policy",      "#7a4fb0", "#b08ae6"),
    "markets":      ("Markets & prices",               "#96700a", "#e2b93b"),
    "safety":       ("Safety, environment & conflict", "#c0392b", "#f0705f"),
    "technology":   ("Technology & demand",            "#c2408f", "#f07cc0"),
}

REGIONS = {
    "na": "North America", "latam": "Latin America", "africa": "Africa", "europe": "Europe",
    "mideast": "Middle East & Central Asia", "apac": "Asia-Pacific", "global": "Global",
}

# Headlines shown in each group before "Show more". The first three groups are
# the priority ones, so they show more.
SHOW_PER_GROUP = {"production": 8, "projects": 8, "exploration": 8}
SHOW_OTHER_GROUPS = 3


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def esc(text):
    return html.escape(text or "")


def inline(text):
    """Escape text, then allow **bold**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(text))


def to_html(text):
    """Convert plain agent text into paragraphs, labels and bullet lists."""
    out, items = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ", "• ")):
            items.append(f"<li>{inline(line[2:])}</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        if re.fullmatch(r"\**[A-Z][A-Z &']{3,}:\**", line):      # A label such as "ON THE HORIZON:"
            out.append(f'<p class="lbl">{esc(line.strip("*:").capitalize())}</p>')
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "\n".join(out)


def details(title, body):
    return f'<details><summary>{esc(title)}</summary><div class="more-text">{body}</div></details>'


def short_time(iso):
    t = datetime.fromisoformat(iso)
    return f"{t:%a} {t.hour % 12 or 12}{'am' if t.hour < 12 else 'pm'}"


def headline_item(article, show_group=False, extra=False):
    """One headline as a list item, with its colored bar, source and region."""
    group = article.get("group", "production")
    region = article.get("region", "global")
    tags = f'<span class="src">{esc(article.get("source", ""))}</span>'
    if region != "global":
        tags += f' <span class="tag">{esc(REGIONS.get(region, ""))}</span>'
    if show_group:
        tags += f' <span class="tag g-text" data-g="{group}">{esc(GROUPS[group][0])}</span>'
    return (f'<li class="hl{" extra" if extra else ""}" data-g="{group}">'
            f'<a href="{esc(article.get("link", "#"))}">{esc(article["title"])}</a> {tags}</li>')


# ---------------------------------------------------------------------------
# The sections of the page
# ---------------------------------------------------------------------------

def headlines_section(brief):
    """Top stories, then every headline sorted into its colored group."""
    articles = [a for a in brief.get("articles", []) if a.get("group", "production") in GROUPS]
    if not articles:
        return ""
    articles = articles[::-1]   # Newest first

    top = ""
    if brief.get("top"):
        items = "".join(headline_item(a, show_group=True) for a in brief["top"])
        top = f'<section><h2>Top stories</h2><ul class="links">{items}</ul></section>'

    chips = [f'<button class="chip on" data-g="all">All <span>{len(articles)}</span></button>']
    groups = []
    for key, (label, _, _) in GROUPS.items():
        members = [a for a in articles if a.get("group", "production") == key]
        if not members:
            continue
        # North American headlines lead each group; newest first within each part.
        members = ([a for a in members if a.get("region") == "na"]
                   + [a for a in members if a.get("region") != "na"])
        show = SHOW_PER_GROUP.get(key, SHOW_OTHER_GROUPS)
        chips.append(f'<button class="chip" data-g="{key}"><i class="dot"></i>{esc(label)} <span>{len(members)}</span></button>')
        items = "".join(headline_item(a, extra=i >= show) for i, a in enumerate(members))
        more = (f'<button class="more">Show {len(members) - show} more</button>'
                if len(members) > show else "")
        groups.append(f'<section class="grp" data-g="{key}"><h3><i class="dot"></i>{esc(label)} '
                      f'<span class="n">{len(members)}</span></h3><ul class="links">{items}</ul>{more}</section>')

    return (f'{top}<details class="headlines" open><summary>Today\'s headlines ({len(articles)})</summary>'
            f'<div class="chips">{"".join(chips)}</div>{"".join(groups)}</details>')


def takeaways_section(brief):
    """Dr I Simon's takeaways under the four headings."""
    blocks = "".join(
        f'<h3 class="th">{esc(heading)}</h3><ul class="takeaways">'
        + "".join(f"<li>{inline(b)}</li>" for b in items) + "</ul>"
        for heading, items in brief.get("sections", []) if items)
    if not blocks:   # The headings couldn't be read: show the conclusions as written
        blocks = to_html(brief.get("chief_conclusion", ""))
    return f"<section><h2>{esc(C)}'s takeaways</h2>{blocks}</section>"


def package_item(p, new=False):
    kind = "lode claims" if p.get("type") == "lode" else "claims"
    where = f'<a href="{esc(p["map"])}">map</a>' if p.get("map") and p["map"] != "#" else ""
    if p.get("claimant"):
        who = f" · claimant {esc(p['claimant'])}"
    elif p.get("lookup"):
        first_serial = (p.get("serials") or "").split(" to ")[0]
        who = (f' · claimant: <a href="{esc(p["lookup"])}">look up in MLRS</a> '
               f'<span class="src">(serial {esc(first_serial)})</span>')
    else:
        who = ""
    town = f'<br><span class="src">{esc(p["town"])}</span>' if p.get("town") else ""
    note = f'<p class="note"><strong>{esc(G)}:</strong> {inline(p["note"])}</p>' if p.get("note") else ""
    badge = '<span class="badge">New</span> ' if new else ""
    return (f'<li class="pkg">{badge}<strong>{p["count"]} {kind}</strong> · “{esc(p["name"])}” · {esc(p["state"])}{who}'
            f'<br><span class="src">{esc(p["source"])} · filed {esc(p["first"])} to {esc(p["last"])} · '
            f'about {p["acres"]:,} acres{" · " + where if where else ""}</span>{town}{note}</li>')


def gov_section(brief):
    """Claim packages, exploration notices and environmental reviews."""
    new = brief.get("claims_new", [])
    new_keys = {p["key"] for p in new}
    older = [p for p in brief.get("claims_active", []) if p["key"] not in new_keys]
    parts = []

    if new or older:
        items = "".join(package_item(p, new=True) for p in new) + "".join(package_item(p) for p in older)
        parts.append(f'<h3 class="th">Claim packages over 100 claims <span class="n">last 30 days</span></h3>'
                     f'<ul class="links">{items}</ul>')
    else:
        parts.append('<h3 class="th">Claim packages over 100 claims</h3><p class="src">None filed in the last 30 days.</p>')

    notices = brief.get("notices", [])
    if notices:
        rows = "".join(f'<li>{esc(n["state"])} · {esc(n["name"]) or "(unnamed)"} '
                       f'<span class="src">{esc(n["serial"])} · {esc(n["created"])}'
                       + (f' · {esc(n["town"])}' if n.get("town") else "")
                       + (f' · <a href="{esc(n["map"])}">map</a>' if n.get("map") and n["map"] != "#" else "")
                       + "</span></li>" for n in sorted(notices, key=lambda n: n["state"]))
        by_state = {}
        for n in notices:
            by_state[n["state"]] = by_state.get(n["state"], 0) + 1
        counts = ", ".join(f"{c} in {st}" for st, c in sorted(by_state.items(), key=lambda x: -x[1]))
        parts.append(details(f"New BLM exploration notices ({len(notices)}): {counts}", f'<ul class="links plain">{rows}</ul>'))

    reviews = brief.get("reviews", [])
    if reviews:
        rows = "".join(f'<li class="hl" data-g="projects"><a href="{esc(r["link"])}">{esc(r["title"])}</a> '
                       f'<span class="src">{esc(r["source"])}{" · " + esc(r["date"]) if r.get("date") else ""}</span>'
                       + (f' <span class="tag">{esc(r["state"])}</span>' if r.get("state") else "") + "</li>"
                       for r in reviews)
        parts.append(f'<h3 class="th">Environmental reviews</h3><ul class="links">{rows}</ul>')

    return f'<section id="claims"><h2>Claims, notices &amp; reviews</h2>{"".join(parts)}</section>'


def filings_section(brief):
    picks = brief.get("filings", [])
    if not picks:
        return ""
    rows = "".join(f'<li class="hl" data-g="{"safety" if f["id"].startswith(("ACC-", "107A-")) else "deals_policy"}">'
                   f'<a href="{esc(f["link"])}">{esc(f["title"])}</a> <span class="src">{esc(f["date"])}'
                   + (f' · MSHA mine ID {esc(f["mine_id"])}' if f.get("mine_id") else "")
                   + f'</span><p class="note"><strong>{esc(C)}:</strong> {inline(f.get("takeaway", ""))}</p></li>'
                   for f in picks)
    return f'<section><h2>Filings &amp; safety</h2><ul class="links">{rows}</ul></section>'


def scorecard_section(brief):
    card = brief.get("scorecard")
    if not card:
        return ""
    names = {"strategist": S, "geologist": G}

    def rate(row):
        return f'{row["hit_rate"]}%' if row["hit_rate"] is not None else "–"

    def by_conf(row, level):
        hits, misses = row["by_confidence"][level]
        return f"{hits}/{hits + misses}" if hits + misses else "–"

    head = "".join(f"<th>{esc(names[a])}</th>" for a in card)
    rows = [("Came true", lambda r: r["came true"]), ("Did not happen", lambda r: r["did not happen"]),
            ("No evidence", lambda r: r["no evidence"]), ("Still open", lambda r: r["open"]),
            ("Hit rate", rate), ("High confidence", lambda r: by_conf(r, "high")),
            ("Medium confidence", lambda r: by_conf(r, "medium")), ("Low confidence", lambda r: by_conf(r, "low"))]
    body = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{fn(card[a])}</td>" for a in card) + "</tr>"
                   for label, fn in rows)
    table = f'<table class="score"><tr><th></th>{head}</tr>{body}</table>'
    note = ('<p class="src">Hit rate counts only predictions the news settled. “No evidence” is never counted as a miss. '
            'Confidence rows show hits out of settled predictions.</p>')

    def plist(items, show_verdict=False):
        out = ""
        for i in items:
            who = esc({"strategist": S, "geologist": G, "chief": C}.get(i["author"], ""))
            verdict = (f' <span class="tag">{esc(i["status"])}</span><br><span class="src">{esc(i.get("verdict_note", ""))}</span>'
                       if show_verdict else "")
            out += (f'<li><strong>{who}:</strong> {esc(i["text"])} <span class="src">by {esc(i["by"])} · '
                    f'{esc(i["confidence"])} confidence</span>{verdict}</li>')
        return f'<ul class="plain">{out}</ul>' if out else '<p class="src">None.</p>'

    extra = details(f"Open predictions ({len(brief.get('open_predictions', []))})", plist(brief.get("open_predictions", [])))
    extra += details(f"Recent results ({len(brief.get('results', []))})", plist(brief.get("results", []), show_verdict=True))
    extra += details(f"{C}'s own predictions ({len(brief.get('chief_predictions', []))}, not scored)",
                     plist(brief.get("chief_predictions", [])))
    return f'<section><h2>Prediction scorecard</h2>{table}{note}{extra}</section>'


def extras_section(brief):
    """Everything that is collapsed by default."""
    extras = []
    if brief.get("creative"):
        extras.append(details(f"{C}'s creative angles",
                              "<ul>" + "".join(f"<li>{inline(c)}</li>" for c in brief["creative"]) + "</ul>"))
    if brief.get("global_view"):
        extras.append(details(f"{C}'s global view", to_html(brief["global_view"])))
    if brief.get("world"):
        links = "".join(f'<li><a href="{esc(a.get("link", "#"))}">{esc(a["title"])}</a> '
                        f'<span class="src">{esc(a.get("source", ""))}</span></li>' for a in brief["world"][::-1])
        extras.append(details(f"World headlines ({len(brief['world'])})", f'<ul class="links plain">{links}</ul>'))

    for session in brief.get("chief_sessions", []):
        body = (f"<h4>{esc(C)}'s review</h4>{to_html(session['opening'])}"
                f"<h4>{esc(S)}'s answers</h4>{to_html(session['strategist_answer'])}"
                f"<h4>{esc(G)}'s answers</h4>{to_html(session['geologist_answer'])}"
                f"<h4>{esc(C)}'s conclusions</h4>{to_html(session['conclusion'])}")
        extras.append(details(f"{C} session · {short_time(session['time'])}", body))

    for d in brief.get("discussions", []):
        body = (f"<h4>Summary</h4>{to_html(d['summary'])}"
                f"<h4>{esc(S)}</h4>{to_html(d['strategist'])}"
                f"<h4>{esc(G)}</h4>{to_html(d['geologist'])}"
                f"<h4>{esc(S)}'s reply</h4>{to_html(d['strategist_reply'])}"
                f"<h4>{esc(G)}'s reply</h4>{to_html(d['geologist_reply'])}")
        extras.append(details(f"{S} & {G} · {short_time(d['time'])} ({d['article_count']} articles)", body))

    for t in brief.get("triage_log", []):
        body = (f"<h4>What was screened</h4>{to_html(t['listing'])}"
                f"<h4>{esc(S)}</h4>{to_html(t['strategist'])}<h4>{esc(G)}</h4>{to_html(t['geologist'])}"
                + (f"<h4>{esc(C)}</h4>{to_html(t['chief'])}" if t.get("chief") else ""))
        extras.append(details(f"How filings and safety reports were screened · {short_time(t['time'])} ({t['count']} items)", body))

    if brief.get("gov_notes"):
        extras.append(details("Government data sources: status",
                              "<ul>" + "".join(f"<li>{esc(n)}</li>" for n in brief["gov_notes"]) + "</ul>"))
    return "".join(extras)


# ---------------------------------------------------------------------------
# The page template. $WORDS are filled in by build_page().
# ---------------------------------------------------------------------------

TEMPLATE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Mining Desk">
<meta name="theme-color" content="#f6f1e7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#16130f" media="(prefers-color-scheme: dark)">
<title>Mining Desk</title>
<link rel="apple-touch-icon" href="icon.png">
<link rel="icon" href="icon.png">
<style>
  :root {
    --bg: #f6f1e7; --ink: #241f19; --muted: #7a6f60; --card: #fffaf0;
    --line: #e2d8c6; --accent: #b0602a;
  }
  $LIGHT_COLORS
  @media (prefers-color-scheme: dark) {
    :root { --bg: #16130f; --ink: #ece4d6; --muted: #a39683; --card: #211c16;
            --line: #3a3128; --accent: #e08a4c; }
    $DARK_COLORS
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    padding: env(safe-area-inset-top) 16px calc(32px + env(safe-area-inset-bottom));
  }
  main { max-width: 640px; margin: 0 auto; }
  header { padding: 20px 0 6px; display: flex; justify-content: space-between;
           align-items: baseline; border-bottom: 1px solid var(--line); }
  .brand { font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 13px; }
  .date { color: var(--muted); font-size: 14px; }
  .poem {
    font-family: "Iowan Old Style", Georgia, serif; font-style: italic;
    font-size: 23px; line-height: 1.45; white-space: pre-line;
    padding: 28px 0 24px; border-bottom: 1px solid var(--line);
  }
  .byline { display: block; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            font-style: normal; font-size: 14px; color: var(--muted); margin-top: 12px; }
  h2 { font-size: 13px; letter-spacing: .08em; text-transform: uppercase;
       color: var(--accent); margin: 24px 0 10px; }
  h3 { font-size: 15px; margin: 0 0 6px; }
  h4 { font-size: 14px; color: var(--accent); margin: 18px 0 4px; }
  ul { padding-left: 20px; margin: 8px 0; }
  li { margin: 0 0 8px; }
  .th { margin: 18px 0 2px; }
  .takeaways li { margin-bottom: 10px; }
  .lbl { font-weight: 600; margin: 14px 0 2px; }

  /* Headlines: each has a colored bar for its group */
  .links { list-style: none; padding: 0; font-size: 15px; }
  .links a { color: var(--ink); }
  .hl { border-left: 3px solid var(--g); padding-left: 10px; margin-bottom: 10px; }
  .plain li { margin-bottom: 10px; }
  .src { color: var(--muted); font-size: 13px; }
  .tag { font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted);
         border: 1px solid var(--line); border-radius: 4px; padding: 1px 5px; white-space: nowrap; }
  .g-text { color: var(--g); border-color: var(--g); }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
         background: var(--g); margin-right: 6px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 0 6px; }
  .chip { font: inherit; font-size: 13px; color: var(--ink); background: var(--card);
          border: 1px solid var(--line); border-radius: 999px; padding: 5px 10px; cursor: pointer; }
  .chip span, .n { color: var(--muted); font-weight: 400; font-size: 13px; }
  .chip.on { border-color: var(--ink); font-weight: 600; }
  .grp { padding-top: 14px; }
  .grp h3 { margin-bottom: 8px; }
  .grp:not(.open) .extra { display: none; }
  .grp.open .more { display: none; }
  .more { font: inherit; font-size: 13px; color: var(--accent); background: none; border: 0;
          padding: 0 0 4px 13px; cursor: pointer; }

  .pair { display: grid; gap: 12px; margin: 24px 0; }
  @media (min-width: 560px) { .pair { grid-template-columns: 1fr 1fr; } }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 14px 16px; font-size: 16px; }
  .card p { margin: 0; }
  .role { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 4px; }
  details { border-top: 1px solid var(--line); }
  details:last-of-type { border-bottom: 1px solid var(--line); }
  summary { cursor: pointer; padding: 14px 0; font-weight: 600; font-size: 15px; }
  .more-text { font-size: 15px; padding-bottom: 16px; }
  .headlines { border-top: none; border-bottom: 1px solid var(--line); padding-bottom: 10px; }
  .quiet { padding: 40px 0; color: var(--muted); font-style: italic; }
  footer { color: var(--muted); font-size: 13px; margin-top: 28px; }

  /* Tabs between the brief and the research page */
  .tabs { display: flex; gap: 18px; border-bottom: 1px solid var(--line); }
  .tabs a { color: var(--muted); text-decoration: none; font-size: 15px; padding: 10px 0 8px;
            border-bottom: 2px solid transparent; }
  .tabs a.on { color: var(--ink); font-weight: 600; border-color: var(--accent); }

  /* Claims, filings and the scorecard */
  .pkg { border-left: 3px solid var(--accent); padding-left: 10px; margin-bottom: 14px; }
  .badge { font-size: 11px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
           color: var(--bg); background: var(--accent); border-radius: 4px; padding: 1px 6px; }
  .note { font-size: 14px; margin: 6px 0 0; }
  .score { width: 100%; border-collapse: collapse; font-size: 14px; margin: 6px 0 8px; }
  .score th, .score td { text-align: left; padding: 6px 4px; border-bottom: 1px solid var(--line); }
  .score td + td, .score th + th { text-align: right; }

  /* Research page */
  .paper { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
           padding: 14px 16px; margin: 0 0 14px; font-size: 15px; }
  .paper h3 { font-size: 16px; line-height: 1.35; margin-bottom: 4px; }
  .paper h3 a { color: var(--ink); }
  .paper p { margin: 8px 0 0; }
  .paper .k { font-weight: 600; }
  .newslink { border-left: 3px solid var(--accent); padding-left: 10px; }
  .newslink a, .pkg a, .note a, .src a { color: var(--ink); }
</style>
</head>
<body>
<main>
<header>
  <span class="brand">Mining Desk</span>
  <span class="date">$DATE</span>
</header>
<nav class="tabs">$NAV</nav>
$MAIN
$EXTRAS
<footer>$FOOTER</footer>
</main>
<script>
  // Tap a colored chip to show only that group of headlines.
  var box = document.querySelector(".headlines");
  if (box) {
    box.querySelectorAll(".chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var g = chip.dataset.g;
        box.querySelectorAll(".chip").forEach(function (c) { c.classList.toggle("on", c === chip); });
        box.querySelectorAll(".grp").forEach(function (s) {
          s.hidden = g !== "all" && s.dataset.g !== g;
          s.classList.toggle("open", g !== "all");
        });
      });
    });
    box.querySelectorAll(".more").forEach(function (button) {
      button.addEventListener("click", function () { button.parentNode.classList.add("open"); });
    });
  }

  // Research page: tap a chip to filter the papers.
  var papers = document.querySelector(".papers");
  if (papers) {
    papers.querySelectorAll(".chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var f = chip.dataset.f;
        papers.querySelectorAll(".chip").forEach(function (c) { c.classList.toggle("on", c === chip); });
        papers.querySelectorAll(".paper").forEach(function (card) {
          card.hidden = f !== "all" && (" " + card.dataset.tags + " ").indexOf(" " + f + " ") < 0;
        });
      });
    });
  }

  // Home-screen apps on iPhone keep an old copy open in the background.
  // If you come back after 30+ minutes, reload so you see the latest brief.
  var loadedAt = Date.now();
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && Date.now() - loadedAt > 30 * 60 * 1000) {
      location.reload();
    }
  });
</script>
</body>
</html>
""")


def build_page(brief, out_path):
    when = datetime.fromisoformat(brief["time"])
    updated = f"{when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"

    if brief.get("quiet"):
        main = '<p class="quiet">No new mining news in the last 24 hours. The desk is enjoying its coffee.</p>'
    else:
        main = f"""
<section class="poem">{esc(brief.get('poem', ''))}<span class="byline">— {esc(R)}</span></section>

{headlines_section(brief)}

{takeaways_section(brief)}

<section class="pair">
  <div class="card">
    <h3>{esc(S)} <span class="role">Strategist</span></h3>
    <p>{inline(brief.get('strategist_short', ''))}</p>
  </div>
  <div class="card">
    <h3>{esc(G)} <span class="role">Geologist</span></h3>
    <p>{inline(brief.get('geologist_short', ''))}</p>
  </div>
</section>
"""
    main += gov_section(brief) + filings_section(brief) + scorecard_section(brief)

    # One CSS rule per group sets its color (--g) wherever data-g appears.
    light = " ".join(f'[data-g="{k}"] {{ --g: {v[1]}; }}' for k, v in GROUPS.items())
    dark = " ".join(f'[data-g="{k}"] {{ --g: {v[2]}; }}' for k, v in GROUPS.items())

    page = TEMPLATE.substitute(
        DATE=esc(brief.get("date_label", "")), MAIN=main, EXTRAS=extras_section(brief),
        NAV=NAV_BRIEF, FOOTER=f"Updated {updated} Pacific · {brief.get('article_count', 0)} articles reviewed · "
                              "AI analysis for professional interest, not investment advice.",
        LIGHT_COLORS=light, DARK_COLORS=dark)
    write_page(page, out_path)


NAV_BRIEF = '<a class="on" href="index.html">Brief</a><a href="research.html">Research</a>'
NAV_RESEARCH = '<a href="index.html">Brief</a><a class="on" href="research.html">Research</a>'


def build_research_page(papers, out_path, now, waiting=0):
    """The Research tab: new economic-geology papers with Yukon Cornelius's notes."""
    chips_wanted = [("all", "All"), ("news", "Linked to news"), ("high", "High relevance"), ("alaska", "Alaska")]
    cards = []
    counts = {key: 0 for key, _ in chips_wanted}
    for p in papers:
        tags = ["all"]
        if p.get("news"):
            tags.append("news")
        if p.get("relevance") == "high":
            tags.append("high")
        where = p.get("where_it_applies") or p.get("us_alaska", "")
        if "alaska" in (where + p.get("title", "")).lower():
            tags.append("alaska")
        for t in tags:
            counts[t] += 1
        facts = " · ".join(esc(x) for x in (p.get("deposit_type"), p.get("commodities")) if x)
        rel = f' <span class="tag">{esc(p["relevance"])} relevance</span>' if p.get("relevance") else ""
        if p.get("title_only"):
            rel += ' <span class="tag">title only</span>'
        news = ""
        if p.get("news"):
            news = (f'<p class="newslink"><span class="k">In the news:</span> <a href="{esc(p["news"]["link"])}">'
                    f'{esc(p["news"]["title"])}</a><br>{inline(p["news"].get("why", ""))}</p>')
        body = ""
        for value, label in ((p.get("setting"), "Setting"), (where, "Where it applies"), (p.get("exploration"), "For explorers")):
            if value:
                body += f'<p><span class="k">{label}:</span> {inline(value)}</p>'
        cards.append(
            f'<article class="paper" data-tags="{" ".join(tags)}">'
            f'<h3><a href="{esc(p["link"])}">{esc(p["title"])}</a></h3>'
            f'<span class="src">{esc(p["journal"])} · {esc(p["date"])}{" · " + esc(p["authors"]) if p.get("authors") else ""}</span>'
            f'{"<p>" + facts + rel + "</p>" if facts or rel else ""}{body}{news}</article>')

    chips = "".join(f'<button class="chip{" on" if key == "all" else ""}" data-f="{key}">{label} <span>{counts[key]}</span></button>'
                    for key, label in chips_wanted)
    intro = (f'<p class="src">New papers from economic-geology journals, read by {esc(G)} for what they mean for '
             f'exploration in the Lower 48 or Alaska. His notes on location and applicability draw on his background '
             f'knowledge; the findings come from each paper\'s abstract.'
             + (f" {waiting} more waiting to be read." if waiting else "") + "</p>")
    main = (f'<section class="papers"><h2>Research</h2>{intro}<div class="chips">{chips}</div>'
            + ("".join(cards) or '<p class="quiet">No papers yet. They appear after the next morning run.</p>')
            + "</section>")
    updated = f"{now.hour % 12 or 12}:{now:%M} {'AM' if now.hour < 12 else 'PM'}"
    page = TEMPLATE.substitute(
        DATE=esc(f"{now:%A, %B} {now.day}"), MAIN=main, EXTRAS="", NAV=NAV_RESEARCH,
        FOOTER=f"Updated {updated} Pacific · {len(papers)} papers · Sources via OpenAlex.",
        LIGHT_COLORS="", DARK_COLORS="")
    write_page(page, out_path)


def write_page(page, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")

    # Copy the home-screen icon next to the page, if there is one.
    if Path("icon.png").exists():
        shutil.copy("icon.png", out_path.parent / "icon.png")

    # Tells GitHub Pages to serve the folder as-is (skips its "Jekyll" processing).
    (out_path.parent / ".nojekyll").write_text("")
