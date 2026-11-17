"""
page.py: turns the desk's work into four phone-friendly pages, with tabs at
the top to switch between them.

  Brief (index.html)       Dr I Simon's takeaways and the top stories, open.
                           Everything else is folded up until you tap it:
                           today's headlines in colored groups, the analysts'
                           short takes, claims / notices / reviews, filings &
                           safety, the prediction scorecard, the desk's memory,
                           and the full discussions.
  Quick Look (quick.html)  The Rat's poem, Dr I Simon's three-sentence world
                           and mining updates, high-relevance research papers,
                           and new claim packages as a short list.
  Research (research.html) New economic-geology papers with Yukon Cornelius's notes.
  Dan's Tab (dan.html)     The Rat's personal page: animal and quote of the
                           day, community events, and Dan's other interests,
                           with rating buttons and a message box.

Names come from prompts.py. Groups, colors and regions are set just below.
"""

import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from string import Template

import vault
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

# Quick Look: claim packages in these states are listed first, in this order.
CLAIM_STATE_ORDER = ["WA", "ID", "OR", "WY"]

# The app opens on the first tab (index.html is the address saved on your home screen).
TABS = [("index.html", "Quick Look"), ("brief.html", "Brief"), ("research.html", "Research"), ("dan.html", "Dan's Tab")]


def nav(current):
    links = ""
    for page, label in TABS:
        on = ' class="on"' if page == current else ""
        links += f'<a{on} href="{page}">{esc(label)}</a>'
    return links


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


def fold(title, body, open=False, note="", anchor=""):
    """A main section of the Brief that folds up. `note` is a short grey remark beside the title."""
    if not body:
        return ""
    note = f' <span class="n">{esc(note)}</span>' if note else ""
    return (f'<details class="sec"{" open" if open else ""}{f" id={anchor!r}" if anchor else ""}>'
            f'<summary><h2>{esc(title)}{note}</h2></summary><div class="sec-body">{body}</div></details>')


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

def top_section(brief):
    if not brief.get("top"):
        return ""
    items = "".join(headline_item(a, show_group=True) for a in brief["top"])
    return fold("Top stories", f'<ul class="links">{items}</ul>', open=True)


def headlines_section(brief):
    """Every headline sorted into its colored group."""
    articles = [a for a in brief.get("articles", []) if a.get("group", "production") in GROUPS]
    if not articles:
        return ""
    articles = articles[::-1]   # Newest first

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

    return fold("Today's headlines", f'<div class="headlines"><div class="chips">{"".join(chips)}</div>{"".join(groups)}</div>',
                note=str(len(articles)))


def takeaways_section(brief):
    """Dr I Simon's takeaways under the four headings."""
    blocks = "".join(
        f'<h3 class="th">{esc(heading)}</h3><ul class="takeaways">'
        + "".join(f"<li>{inline(b)}</li>" for b in items) + "</ul>"
        for heading, items in brief.get("sections", []) if items)
    if not blocks:   # The headings couldn't be read: show the conclusions as written
        blocks = to_html(brief.get("chief_conclusion", ""))
    return fold(f"{C}'s takeaways", blocks, open=True)


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

    remark = ", ".join(x for x in (f"{len(new)} new package{'s' if len(new) != 1 else ''}" if new else "",
                                   f"{len(notices)} notice{'s' if len(notices) != 1 else ''}" if notices else "",
                                   f"{len(reviews)} review{'s' if len(reviews) != 1 else ''}" if reviews else "") if x)
    return fold("Claims, notices & reviews", "".join(parts), note=remark, anchor="claims")


def filings_section(brief):
    picks = brief.get("filings", [])
    if not picks:
        return ""
    rows = "".join(f'<li class="hl" data-g="{"safety" if f["id"].startswith(("ACC-", "107A-")) else "deals_policy"}">'
                   f'<a href="{esc(f["link"])}">{esc(f["title"])}</a> <span class="src">{esc(f["date"])}'
                   + (f' · MSHA mine ID {esc(f["mine_id"])}' if f.get("mine_id") else "")
                   + f'</span><p class="note"><strong>{esc(C)}:</strong> {inline(f.get("takeaway", ""))}</p></li>'
                   for f in picks)
    return fold("Filings & safety", f'<ul class="links">{rows}</ul>', note=str(len(picks)))


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
    return fold("Prediction scorecard", table + note + extra)


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
    return (fold("More from the desk", "".join(extras), note="discussions, screening, sources")
            + themes_section(brief) + memory_section(brief))


THEME_MARKS = {"new": ("✦", "new"), "building": ("▲", "building"), "steady": ("●", "steady"),
               "fading": ("▼", "fading"), "resolved": ("✓", "resolved"), "": ("·", "not mentioned")}


def themes_section(brief):
    """The theme tracker: each theme the weekly summaries named, week by week."""
    tracker = brief.get("themes") or {}
    rows = tracker.get("rows", [])
    due = (brief.get("memory") or {}).get("first_week_due")
    if not rows:
        body = ('<p class="src">The tracker fills in from the weekly summaries. '
                + (f"The first is written on {esc(due)}." if due else "") + "</p>")
        return fold("Theme tracker", body)
    weeks = tracker["weeks"]
    out = ""
    for r in rows:
        marks = "".join(f'<span class="mk mk-{m or "none"}" title="{esc(weeks[n])}: {THEME_MARKS.get(m, THEME_MARKS[""])[1]}">'
                        f'{THEME_MARKS.get(m, THEME_MARKS[""])[0]}</span>' for n, m in enumerate(r["marks"]))
        out += (f'<li><strong>{esc(r["name"])}</strong> <span class="src">{r["weeks"]} week{"s" if r["weeks"] != 1 else ""} · '
                f'{esc(r["state"])}</span><div class="marks">{marks}</div>'
                + (f'<p class="note">{inline(r["latest"])}</p>' if r.get("latest") else "") + "</li>")
    key = " ".join(f'<span class="mk mk-{k or "none"}">{v[0]}</span> {v[1]}' for k, v in THEME_MARKS.items())
    body = (f'<p class="src">One mark per week, oldest on the left ({esc(weeks[0])} to {esc(weeks[-1])}).</p>'
            f'<ul class="links plain themes">{out}</ul><p class="src">{key}</p>')
    return fold("Theme tracker", body, note=f"{len(rows)} themes")


def memory_section(brief):
    """The desk's memory as a timeline: the year, then months, then weeks."""
    memory = brief.get("memory") or {}
    parts = []
    if memory.get("year"):
        parts.append(details(f"{memory['year'][0]} so far", to_html(memory["year"][1])))
    for label, text in memory.get("months", []):
        parts.append(details(label, to_html(text)))
    for label, text in memory.get("weeks", []):
        parts.append(details(f"Week of {label}", to_html(text)))
    if not parts:
        due = memory.get("first_week_due")
        parts.append('<p class="src">The desk keeps a daily log, rolls each week into a weekly summary, each month into a '
                     'monthly summary, and the months into an account of the year. '
                     + (f"The first weekly summary is written on {esc(due)}." if due else "") + "</p>")
    return fold("The desk's memory", "".join(parts), note="year, months, weeks")


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

  /* Tabs between the pages */
  .tabs { display: flex; gap: 18px; border-bottom: 1px solid var(--line); overflow-x: auto; }
  .tabs a { color: var(--muted); text-decoration: none; font-size: 15px; padding: 10px 0 8px;
            border-bottom: 2px solid transparent; white-space: nowrap; }

  /* Main sections that fold up */
  .sec > summary { list-style-position: outside; margin-left: 18px; }
  .sec > summary { padding: 14px 0; }
  .sec > summary h2 { display: inline; margin: 0; line-height: 1.6; }
  .sec-body { padding-bottom: 14px; }
  .sec .pair { margin: 4px 0 8px; }
  .quick { font-size: 18px; line-height: 1.55; margin: 0 0 6px; }

  /* Dan's Tab */
  .animal { display: flex; gap: 14px; align-items: flex-start; padding: 20px 0 16px; border-bottom: 1px solid var(--line); }
  .animal img { width: 116px; height: 116px; object-fit: cover; border-radius: 12px; flex: none; }
  .animal h2 { margin-top: 0; }
  .animal p { font-size: 15px; margin: 6px 0; }
  .rat { font-style: italic; }
  .quote { font-family: "Iowan Old Style", Georgia, serif; font-style: italic; font-size: 20px; line-height: 1.45;
           margin: 0; padding: 22px 0 20px; border-bottom: 1px solid var(--line); }
  .reply { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; font-size: 15px; }
  .rate { display: flex; align-items: center; gap: 6px; margin: 6px 0 14px; }
  .rate button { font: inherit; font-size: 13px; width: 34px; height: 30px; border-radius: 8px; cursor: pointer;
                 color: var(--muted); background: var(--card); border: 1px solid var(--line); }
  .rate button.on { color: var(--bg); background: var(--accent); border-color: var(--accent); }
  textarea, input[type=password] { width: 100%; font: inherit; font-size: 16px; color: var(--ink); background: var(--card);
             border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }
  .btn { display: inline-block; font: inherit; font-size: 15px; font-weight: 600; color: var(--bg); background: var(--accent);
         border: 1px solid var(--accent); border-radius: 10px; padding: 8px 14px; cursor: pointer; text-decoration: none; }
  .btn.ghost { color: var(--accent); background: none; }
  .lock { padding: 40px 0; }
  .lock p { margin: 0 0 12px; }
  .marks { display: flex; gap: 3px; margin: 4px 0 2px; flex-wrap: wrap; }
  .mk { display: inline-block; width: 18px; text-align: center; font-size: 13px; color: var(--muted); }
  .mk-new { color: #2f6fb3; } .mk-building { color: #2e7d4f; } .mk-fading { color: #c0392b; } .mk-resolved { color: var(--accent); }
  .themes li { margin-bottom: 14px; }
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

  // Dan's Tab is locked: the page holds only encrypted text until the password
  // is entered (once per phone). Ratings and messages are encrypted the same
  // way before they leave the phone.
  var b64 = {
    to: function (bytes) { var s = ""; new Uint8Array(bytes).forEach(function (c) { s += String.fromCharCode(c); }); return btoa(s); },
    from: function (text) { return Uint8Array.from(atob(text), function (c) { return c.charCodeAt(0); }); }
  };
  var deriveKey = function (pass, salt, rounds) {
    return crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]).then(function (base) {
      return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt, iterations: rounds, hash: "SHA-256" },
                                     base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    });
  };
  var openBox = function (box, pass) {
    return deriveKey(pass, b64.from(box.salt), box.rounds).then(function (key) {
      return crypto.subtle.decrypt({ name: "AES-GCM", iv: b64.from(box.iv) }, key, b64.from(box.ct));
    }).then(function (plain) { return new TextDecoder().decode(plain); });
  };
  var sealBox = function (text, pass) {
    var salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12)), rounds = 310000;
    return deriveKey(pass, salt, rounds).then(function (key) {
      return crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, key, new TextEncoder().encode(text));
    }).then(function (sealed) {
      return JSON.stringify({ v: 1, rounds: rounds, salt: b64.to(salt), iv: b64.to(iv), ct: b64.to(sealed) });
    });
  };
  var keep = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    drop: function (k) { try { localStorage.removeItem(k); } catch (e) {} }
  };

  var lockBox = document.getElementById("dan-lock");
  if (lockBox) {
    var sealedPage = JSON.parse(lockBox.dataset.box);
    var lockMsg = document.getElementById("dan-lock-msg");
    var tryOpen = function (pass, remembered) {
      lockMsg.textContent = "Unlocking...";
      openBox(sealedPage, pass).then(function (html) {
        keep.set("danPass", pass);
        lockBox.hidden = true;
        var content = document.getElementById("dan-content");
        content.innerHTML = html;
        startDan(content.querySelector("#dan"), pass);
      }).catch(function () {
        if (remembered) { keep.drop("danPass"); }
        lockMsg.textContent = remembered ? "The password has changed. Enter the new one." : "That password didn't work.";
      });
    };
    document.getElementById("dan-unlock").addEventListener("click", function () {
      var field = document.getElementById("dan-pass");
      if (field.value) { tryOpen(field.value, false); field.value = ""; }
    });
    document.getElementById("dan-pass").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { document.getElementById("dan-unlock").click(); }
    });
    if (!window.crypto || !crypto.subtle) {
      lockMsg.textContent = "This browser can't unlock the page. Open it from its https:// address.";
    } else if (keep.get("danPass", "")) {
      tryOpen(keep.get("danPass", ""), true);
    }
  }

  function startDan(danBox, pass) {
    if (!danBox) { return; }
    var repo = danBox.dataset.repo || (location.hostname.split(".")[0] + "/" + location.pathname.split("/")[1]);
    var queue = keep.get("danQueue", []), rated = keep.get("danRated", {}), timer = null;
    var statusLine = document.getElementById("dan-status"), manual = document.getElementById("dan-manual");
    var say = function (text) { statusLine.textContent = text; };

    var paint = function () {
      danBox.querySelectorAll(".rate").forEach(function (row) {
        var score = rated[row.dataset.key] || 0;
        row.querySelectorAll("button").forEach(function (b) { b.classList.toggle("on", +b.dataset.s <= score); });
      });
      var waiting = queue.length && !keep.get("danToken", "");
      manual.hidden = !waiting;
      if (waiting) {
        sealBox(JSON.stringify({ items: queue }), pass).then(function (body) {
          manual.href = "https://github.com/" + repo + "/issues/new?title=" + encodeURIComponent("[dan] feedback") +
                        "&body=" + encodeURIComponent(body);
        });
        say(queue.length + " item(s) waiting on this phone. Tap “Send via GitHub”, or save a token in Settings to send automatically.");
      }
    };

    var send = function (keepalive) {
      var token = keep.get("danToken", "");
      if (!queue.length || !token) { paint(); return; }
      var batch = queue.slice();
      sealBox(JSON.stringify({ items: batch }), pass).then(function (body) {
        return fetch("https://api.github.com/repos/" + repo + "/issues", {
          method: "POST", keepalive: !!keepalive,
          headers: { "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json" },
          body: JSON.stringify({ title: "[dan] feedback", body: body })
        });
      }).then(function (r) {
        if (!r.ok) { throw new Error("GitHub said " + r.status); }
        queue = queue.slice(batch.length); keep.set("danQueue", queue); paint();
        say("Sent. It will be applied on tomorrow morning's page.");
      }).catch(function (e) {
        say("Couldn't send (" + e.message + "). It's saved on this phone and will be tried again.");
      });
    };
    var sendSoon = function () { clearTimeout(timer); timer = setTimeout(send, 20000); };

    danBox.querySelectorAll(".rate button").forEach(function (button) {
      button.addEventListener("click", function () {
        var row = button.parentNode, score = +button.dataset.s;
        rated[row.dataset.key] = score; keep.set("danRated", rated);
        queue = queue.filter(function (q) { return !(q.t === "rate" && q.key === row.dataset.key); });
        queue.push({ t: "rate", key: row.dataset.key, topic: row.dataset.topic, title: row.dataset.title, score: score });
        keep.set("danQueue", queue); paint(); sendSoon();
      });
    });
    document.getElementById("dan-send").addEventListener("click", function () {
      var box = document.getElementById("dan-ask"), text = box.value.trim();
      if (!text) { return; }
      queue.push({ t: "ask", text: text }); keep.set("danQueue", queue); box.value = "";
      say("Saved."); send();
    });
    manual.addEventListener("click", function () {
      setTimeout(function () { queue = []; keep.set("danQueue", queue); manual.hidden = true;
                               say("Opened GitHub. Tap “Create” there to send it."); }, 500);
    });
    document.getElementById("dan-token-save").addEventListener("click", function () {
      var value = document.getElementById("dan-token").value.trim();
      if (value) { keep.set("danToken", value); document.getElementById("dan-token").value = ""; say("Token saved on this phone."); send(); }
    });
    document.getElementById("dan-token-clear").addEventListener("click", function () {
      keep.drop("danToken"); say("Token removed from this phone."); paint();
    });
    document.getElementById("dan-forget").addEventListener("click", function () {
      keep.drop("danPass"); location.reload();
    });
    document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") { send(true); } });
    paint();
    if (queue.length) { setTimeout(send, 3000); }
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
        takes = f"""
<div class="pair">
  <div class="card">
    <h3>{esc(S)} <span class="role">Strategist</span></h3>
    <p>{inline(brief.get('strategist_short', ''))}</p>
  </div>
  <div class="card">
    <h3>{esc(G)} <span class="role">Geologist</span></h3>
    <p>{inline(brief.get('geologist_short', ''))}</p>
  </div>
</div>"""
        main = (takeaways_section(brief) + top_section(brief) + headlines_section(brief)
                + fold(f"{S} & {G}: short takes", takes))
    main += gov_section(brief) + filings_section(brief) + scorecard_section(brief)

    # One CSS rule per group sets its color (--g) wherever data-g appears.
    light = " ".join(f'[data-g="{k}"] {{ --g: {v[1]}; }}' for k, v in GROUPS.items())
    dark = " ".join(f'[data-g="{k}"] {{ --g: {v[2]}; }}' for k, v in GROUPS.items())

    page = TEMPLATE.substitute(
        DATE=esc(brief.get("date_label", "")), MAIN=main, EXTRAS=extras_section(brief),
        NAV=nav("brief.html"), FOOTER=f"Updated {updated} Pacific · {brief.get('article_count', 0)} articles reviewed · "
                              "AI analysis for professional interest, not investment advice.",
        LIGHT_COLORS=light, DARK_COLORS=dark)
    write_page(page, out_path)


def clock(now):
    return f"{now.hour % 12 or 12}:{now:%M} {'AM' if now.hour < 12 else 'PM'}"


def build_quick_page(brief, papers, out_path, now):
    """The Quick Look tab: the poem, two three-sentence updates, high-relevance
    research, and new claim packages as a short list."""
    parts = []
    if brief.get("poem"):
        parts.append(f'<section class="poem">{esc(brief["poem"])}<span class="byline">— {esc(R)}</span></section>')
    if brief.get("quiet"):
        parts.append('<p class="quiet">No new mining news in the last 24 hours.</p>')
    for title, key in (("The world in three sentences", "quick_world"), ("Mining in three sentences", "quick_mining")):
        if brief.get(key):
            parts.append(f'<section><h2>{title}</h2><p class="quick">{inline(brief[key])}</p>'
                         f'<span class="src">— {esc(C)}</span></section>')

    week_ago = str(now.date().fromordinal(now.date().toordinal() - 7))
    high = [p for p in papers if p.get("relevance") == "high" and p.get("read", "") >= week_ago][:6]
    if high:
        rows = "".join(f'<li><a href="{esc(p["link"])}">{esc(p["title"])}</a> <span class="src">{esc(p["journal"])}</span>'
                       f'<p class="note">{inline(p.get("summary") or p.get("exploration", ""))}</p></li>' for p in high)
        parts.append(f'<section><h2>High-relevance research</h2><ul class="links plain">{rows}</ul>'
                     f'<a class="src" href="research.html">All papers on the Research tab</a></section>')

    new_keys = {p["key"] for p in brief.get("claims_new", [])}
    packages = {p["key"]: p for p in brief.get("claims_new", []) + brief.get("claims_active", [])}.values()
    order = lambda p: (CLAIM_STATE_ORDER.index(p["state"]) if p["state"] in CLAIM_STATE_ORDER else len(CLAIM_STATE_ORDER),
                       -p["count"])
    if packages:
        rows = "".join(
            f'<li>{"<span class=badge>New</span> " if p["key"] in new_keys else ""}<strong>{p["count"]} claims</strong> · {esc(p["state"])}'
            + (f' · <a href="{esc(p["map"])}">map</a>' if p.get("map") else "") + "</li>"
            for p in sorted(packages, key=order))
        parts.append(f'<section><h2>Claim packages over 100 claims <span class="n">last 30 days</span></h2>'
                     f'<ul class="links plain">{rows}</ul><a class="src" href="brief.html#claims">Details on the Brief tab</a></section>')
    else:
        parts.append('<section><h2>Claim packages over 100 claims</h2><p class="src">None filed in the last 30 days.</p></section>')

    page = TEMPLATE.substitute(
        DATE=esc(brief.get("date_label", "")), MAIN="".join(parts), EXTRAS="", NAV=nav("index.html"),
        FOOTER=f"Updated {clock(now)} Pacific · AI analysis for professional interest, not investment advice.",
        LIGHT_COLORS="", DARK_COLORS="")
    write_page(page, out_path)
    # The Quick Look used to live at quick.html: send any old bookmark to the front page.
    (out_path.parent / "quick.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=index.html">'
        '<a href="index.html">Quick Look</a>', encoding="utf-8")


def build_dan_page(data, out_path, now, password):
    """Dan's Tab: The Rat's personal page, published locked with `password`."""
    parts = []

    def wiki_card(thing, label):
        if not thing or not thing.get("name"):
            return ""
        image = f'<img src="{esc(thing["image"])}" alt="{esc(thing["name"])}" loading="lazy">' if thing.get("image") else ""
        extract = thing.get("extract", "")
        if len(extract) > 340:
            extract = extract[:340].rsplit(". ", 1)[0] + "."
        return (f'<section class="animal">{image}<div><h2>{label}</h2><h3>{esc(thing["name"])}'
                + (f' <span class="role">{esc(thing["tag"])}</span>' if thing.get("tag") else "") + "</h3>"
                + (f"<p>{esc(extract)}</p>" if extract else "")
                + (f'<p class="rat">“{esc(thing["rat"])}” <span class="src">— {esc(R)}</span></p>' if thing.get("rat") else "")
                + f'<a class="src" href="{esc(thing.get("link", "#"))}">Wikipedia</a></div></section>')

    parts.append(wiki_card(data.get("animal"), "Animal of the day"))
    quote = data.get("quote")
    if quote:
        parts.append(f'<blockquote class="quote">{esc(quote["text"])}<span class="byline">— {esc(quote["author"])}'
                     f' <span class="src">· {esc(quote["source"])}</span></span></blockquote>')
    reply = data.get("reply")
    if reply and reply.get("text"):
        parts.append(f'<p class="reply"><strong>{esc(R)}:</strong> {esc(reply["text"])}</p>')

    for section in data.get("sections", []):
        rows = ""
        for item in section["items"]:
            facts = " · ".join(esc(x) for x in (item.get("when"), item.get("place"), item.get("source")) if x)
            buttons = "".join(f'<button data-s="{n}" aria-label="Rate {n} of 5">{n}</button>' for n in range(1, 6))
            rows += (f'<li><a href="{esc(item["link"])}">{esc(item["title"])}</a><br><span class="src">{facts}</span>'
                     f'<p class="note">{esc(item.get("line", ""))}</p>'
                     f'<div class="rate" data-key="{esc(item["link"] + item["title"])[:200]}" data-topic="{esc(section["id"])}" '
                     f'data-title="{esc(item["title"])}"><span class="src">Care?</span>{buttons}</div></li>')
        body = f'<ul class="links plain">{rows}</ul>' if rows else '<p class="src">Nothing new today.</p>'
        if section.get("kind") == "events" and data.get("yearly"):
            yearly = "".join(f'<li><a href="{esc(e["link"])}">{esc(e["name"])}</a> <span class="src">{esc(e["where"])} · '
                             f'usually {esc(e["when"])}</span></li>' for e in data["yearly"])
            body += details("Big yearly events this month and next (usual timing; check the date)",
                            f'<ul class="links plain">{yearly}</ul>')
        parts.append(f'<section><h2>{esc(section["title"])}</h2>{body}</section>')

        # After the community events: a place on Earth, the week's long read, and a local cause.
        if section.get("kind") == "events":
            parts.append(wiki_card(data.get("place"), "One place on Earth"))
            read = data.get("long_read")
            if read:
                parts.append(f'<section><h2>Long read of the week</h2><ul class="links plain"><li>'
                             f'<a href="{esc(read["link"])}">{esc(read["title"])}</a> <span class="src">{esc(read.get("source", ""))}</span>'
                             f'<p class="note">{esc(read.get("why", ""))}</p></li></ul></section>')
            v = data.get("volunteer")
            if v:
                news = "".join(f'<li><a href="{esc(a["link"])}">{esc(a["title"])}</a> <span class="src">{esc(a["source"])}</span></li>'
                               for a in v.get("news", [])[:3])
                parts.append(f'<section><h2>Volunteer spotlight</h2><p><strong>{esc(v["name"])}</strong> '
                             f'<span class="src">{esc(v["where"])}</span></p>'
                             f'<p class="note">{esc(v.get("rat") or v["what"].capitalize() + ".")}</p>'
                             + (f'<ul class="links plain">{news}</ul>' if news else "")
                             + f'<a class="src" href="{esc(v["link"])}">Find out how to volunteer</a></section>')

    parts.append(f"""
<section><h2>Tell {esc(R)} what to change</h2>
  <textarea id="dan-ask" rows="3" placeholder="More of this, less of that, a new interest to add..."></textarea>
  <button id="dan-send" class="btn">Send to {esc(R)}</button>
  <a id="dan-manual" class="btn ghost" hidden target="_blank" rel="noopener">Send via GitHub</a>
  <p id="dan-status" class="src">Ratings and messages are applied on the next morning's page.</p>
</section>""")
    settings = f"""
<p>To send ratings and messages straight from this page, paste a GitHub token here once. It is kept only on this
phone. Without one, use “Send via GitHub”, which opens a GitHub page where you tap “Create”.</p>
<input id="dan-token" type="password" placeholder="GitHub token (starts with github_pat_)" autocomplete="off">
<button id="dan-token-save" class="btn">Save token</button> <button id="dan-token-clear" class="btn ghost">Remove</button>
<p>This page is locked with your password, and everything it sends is locked the same way.</p>
<button id="dan-forget" class="btn ghost">Forget the password on this phone</button>"""
    extras = details("Settings", settings)
    if data.get("status"):
        extras += details("Where today's items came from: status",
                          "<ul>" + "".join(f"<li>{esc(n)}</li>" for n in data["status"]) + "</ul>")

    inside = (f'<div id="dan" data-repo="{esc(data.get("repo", ""))}">{"".join(parts)}</div>{extras}'
              f'<p class="src">Chosen by {esc(R)} from public feeds and calendars. Animal and place from Wikipedia. '
              f'Check event details before you go.</p>')
    sealed = json.dumps(vault.lock(inside, password))
    main = (f'<div id="dan-lock" class="lock" data-box="{esc(sealed)}"><p>Dan\'s Tab is locked.</p>'
            f'<input id="dan-pass" type="password" placeholder="Password" autocomplete="current-password">'
            f'<button id="dan-unlock" class="btn">Unlock</button><p id="dan-lock-msg" class="src"></p></div>'
            f'<div id="dan-content"></div>')
    page = TEMPLATE.substitute(
        DATE=esc(f"{now:%A, %B} {now.day}"), MAIN=main, EXTRAS="", NAV=nav("dan.html"),
        FOOTER=f"Updated {clock(now)} Pacific", LIGHT_COLORS="", DARK_COLORS="")
    write_page(page, out_path)


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
        DATE=esc(f"{now:%A, %B} {now.day}"), MAIN=main, EXTRAS="", NAV=nav("research.html"),
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
