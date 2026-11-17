"""
pipeline.py: the main program. GitHub runs it three times an hour.

Most runs do nothing but check the clock. About once an hour it fetches new
headlines. Then it does whichever jobs are due (Pacific time):

  Every 6 hours  (3am, 9am, 3pm, 9pm):
      Meldrum (Strategist) and Yukon Cornelius (Geologist) each analyze the
      mining, junior-company, and technology news, reply to each other, and
      a note-taker summarizes their findings. If fewer than 5 new headlines
      have come in, they wait for the next slot instead.

  Every 12 hours (3am, 3pm):
      Dr I Simon (Chief Analyst) reads the world news, reviews the desk's
      work, asks both analysts questions, reads their answers, and writes
      conclusions under four headings, plus a global briefing the analysts
      read before their next discussions.

  Every morning  (3am):
      A news editor sorts the day's headlines into colored groups and picks
      the top stories. Meldrum and Yukon Cornelius each write a very short
      summary, and The Rat (poet) writes the poem. The morning page is built
      at docs/index.html, with Dr I Simon's two three-sentence updates on
      the Quick Look page (docs/quick.html). The Rat also builds Dan's Tab
      (docs/dan.html; see dan.py). Finally The Rat (as historian) writes the
      day's log entry in data/history.json. Each Monday he rolls the past
      week into a weekly summary, at each month's end the weeks into a
      monthly summary, and the months into an account of the year. The
      other three read that memory before every analysis.

  Every morning  (2am):
      Government records: new claim packages (more than 100 claims) and BLM
      exploration notices, environmental reviews, and SEC 8-K filings from
      mining companies. Once a week (Saturday) also MSHA's safety reports.
      Meldrum and Yukon Cornelius screen the filings and safety reports, and
      Dr I Simon decides which reach the brief. Yukon Cornelius notes the
      likely district and deposit types for each new claim package.

  With the morning brief, Dr I Simon referees Meldrum's and Yukon
  Cornelius's predictions (data/predictions.json), and Yukon Cornelius reads
  new economic-geology papers for the Research page (docs/research.html).

The 3am start leaves room for GitHub's scheduling delays, so the brief is
ready by 6am. If a run is late or skipped, the next one catches up.

(Names live in prompts.py. News sources live in fetch_news.py.)

How to run it:
    python pipeline.py                    normal run
    python pipeline.py --now              do every job right now, ignoring the clock
    python pipeline.py --now --dry-run    same, but with fake agent replies
                                          (free; no API key needed). Writes to
                                          the preview/ folder instead.
"""

import argparse
import json
import os
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import dan
import fetch_news
import gov_watch
import predictions
import prompts
import research
from page import GROUPS, REGIONS, build_dan_page, build_page, build_quick_page, build_research_page

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("America/Los_Angeles")   # Handles daylight saving automatically

DISCUSSION_HOURS = [3, 9, 15, 21]    # Meldrum + Yukon Cornelius talk (every 6 hours)
CHIEF_HOURS = [3, 15]                # Dr I Simon's session (every 12 hours)
BRIEF_HOURS = [3]                    # Morning brief and page
GOV_HOURS = [2]                      # Government records (claims, reviews, filings)
MSHA_DAY = 5                         # MSHA's files update Friday afternoon, so read them Saturday (Mon=0)

MODEL = "claude-haiku-5-5"           # Model for every agent
CHIEF_MODEL = MODEL                  # Change to "claude-sonnet-5-5" for a stronger Chief

# Length limit for each reply, including the model's thinking. It's a ceiling,
# not a target: you only pay for what's actually written. Too low = blank replies.
MAX_TOKENS = 8000
LONG_TOKENS = 16000                  # For the jobs with long replies: sorting headlines, research, the history log

FETCH_EVERY_MINUTES = 50             # How often to check the news sources
MIN_ARTICLES = 5                     # Fewer new headlines than this: wait for the next slot

# The most headlines of each kind handed to the analysts in one discussion.
# When there are more, North American stories and the three priority groups
# (below) are kept first.
MAX_INDUSTRY = 80
MAX_JUNIORS = 30                     # Junior-company press releases
MAX_TECH = 20
MAX_WORLD = 60                       # World headlines Dr I Simon reads per session

MAX_TRIAGE = 40                      # Most filings and safety reports screened in one day
RESEARCH_PER_DAY = 10                # Papers Yukon Cornelius reads each morning

POET_TANGENT_CHANCE = 2 / 3          # How often The Rat gets the astrology/lizard/fish nudge
HISTORY_DAYS = 7                     # Most daily log entries the agents read (days not yet in a weekly summary)
HISTORY_WEEKS = 4                    # Most weekly summaries the agents read
HISTORY_MONTHS = 2                   # Most monthly summaries the agents read

def empty_day():
    """A fresh, empty record for a new day."""
    return {"articles": [], "discussions": [], "chief_sessions": [], "world": [],
            "claims_new": [], "claims_active": [], "notices": [], "reviews": [], "filings": [],
            "triage_log": [], "gov_notes": []}


# ---------------------------------------------------------------------------
# Small helpers for saving and loading data
# ---------------------------------------------------------------------------

def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def stamp(now):
    """A friendly time string for prompts, e.g. 'Fri Oct 16, 3:05 AM PDT'."""
    # Built by hand because some strftime codes don't work on Windows.
    hour = now.hour % 12 or 12
    return f"{now:%a %b} {now.day}, {hour}:{now:%M} {'AM' if now.hour < 12 else 'PM'} {now:%Z}"


# ---------------------------------------------------------------------------
# Scheduling: is a job due?
# ---------------------------------------------------------------------------

def latest_slot(now, hours):
    """The most recent scheduled time (today or yesterday) that has already passed."""
    slots = []
    for days_back in (0, 1):
        day = (now - timedelta(days=days_back)).date()
        for hour in hours:
            slot = datetime(day.year, day.month, day.day, hour, tzinfo=TIMEZONE)
            if slot <= now:
                slots.append(slot)
    return max(slots)


def is_due(state, job, now, hours):
    """A job is due if it hasn't run since its most recent scheduled time.
    This way, if a run is late or skipped, the next run catches up."""
    last = state.get(job)
    return last is None or datetime.fromisoformat(last) < latest_slot(now, hours)


# ---------------------------------------------------------------------------
# Talking to the agents
# ---------------------------------------------------------------------------

DISPLAY_NAMES = {
    "strategist": prompts.STRATEGIST_NAME, "geologist": prompts.GEOLOGIST_NAME,
    "chief": prompts.CHIEF_NAME, "poet": prompts.POET_NAME,
    "historian": f"{prompts.POET_NAME} (historian)",
    "curator": f"{prompts.POET_NAME} (Dan's Tab)",
    "scribe": "the note-taker", "editor": "the news editor",
}


class Desk:
    """Sends a task to one of the agents and returns its reply."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.calls = 0
        if not dry_run:
            import anthropic  # Reads your key from the ANTHROPIC_API_KEY environment variable
            self.client = anthropic.Anthropic()

    def ask(self, agent, task, model=MODEL, max_tokens=MAX_TOKENS):
        self.calls += 1
        name = DISPLAY_NAMES[agent]
        print(f"  asking {name}...")
        if self.dry_run:
            return fake_reply(agent, task)

        # Current Claude models think before they write, and that thinking
        # counts toward max_tokens. If a reply comes back empty or gets cut
        # off (stop_reason "max_tokens"), try once more with double the room.
        text = ""
        for attempt in (1, 2):
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=prompts.PERSONAS[agent],
                messages=[{"role": "user", "content": task}],
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if text and response.stop_reason != "max_tokens":
                return text
            problem = f"    {name}'s reply was {'cut off' if text else 'empty'} (limit {max_tokens} tokens)"
            print(problem + (", retrying with more room..." if attempt == 1 else ", giving up for this step."))
            max_tokens *= 2

        # Still nothing: return a clear note so the next agent isn't confused by a blank.
        return text or f"[{name} didn't send a reply this time.]"


def fill(template, **blanks):
    """Fill a task template's {blanks}, including the agents' names."""
    return template.format(**prompts.NAMES, **blanks)


def numbered(articles, start=1):
    lines = []
    for i, a in enumerate(articles, start=start):
        source = f" ({a['source']})" if a.get("source") else ""
        lines.append(f"{i}. {a['title']}{source}")
    return "\n".join(lines)


# The headline groups that come first, on the page and in the analysts' lists.
PRIORITY_GROUPS = ("production", "projects", "exploration")

# Spots a North American headline before the news editor has tagged it.
NORTH_AMERICA = re.compile(
    r"\bcanad|ontario|quebec|british columbia|\bb\.c\.|yukon|nunavut|saskatchewan|manitoba|alberta"
    r"|newfoundland|labrador|northwest territories|nova scotia|new brunswick|\btsx"
    r"|united states|\bu\.s\.(?!\$)|\bus\b(?!\$)|(?<!latin )(?<!south )(?<!central )america|nevada|arizona|alaska|idaho|montana"
    r"|utah|minnesota|wyoming|colorado|michigan|south dakota|new mexico|appalachia|west virginia"
    r"|kentucky|washington|\bnyse",
    re.IGNORECASE)


def priority(article):
    """Higher numbers come first: North America, then the priority groups."""
    if article.get("region"):   # Tagged by the news editor
        north_america = article["region"] == "na"
    else:
        north_america = bool(NORTH_AMERICA.search(article["title"]))
    group = article.get("group") or guess_group(article)
    return 2 * north_america + (group in PRIORITY_GROUPS)


def first_by_priority(articles, limit):
    """The most important `limit` headlines, highest priority first (newest
    first within each level)."""
    return sorted(articles[::-1], key=priority, reverse=True)[:limit]


def desk_news(articles):
    """The news as the analysts see it: three labelled lists."""
    def lane(name):
        return [a for a in articles if a.get("lane", "industry") == name]

    parts = []
    industry = first_by_priority(lane("industry"), MAX_INDUSTRY)
    juniors = first_by_priority(lane("juniors"), MAX_JUNIORS)
    tech = lane("tech")[-MAX_TECH:]
    gov = lane("gov")[-40:]
    if industry:
        parts.append("MINING INDUSTRY NEWS:\n" + numbered(industry))
    if juniors:
        parts.append("JUNIOR-COMPANY PRESS RELEASES (read these as a set, for patterns in where "
                     "drilling and money are going, not one by one):\n" + numbered(juniors, len(industry) + 1))
    if tech:
        parts.append("TECHNOLOGY AND DEMAND NEWS:\n" + numbered(tech, len(industry) + len(juniors) + 1))
    if gov:
        parts.append("GOVERNMENT RECORDS (new claim packages, exploration notices, environmental reviews, "
                     "and the company filings and safety reports " + prompts.CHIEF_NAME + " chose for the brief):\n"
                     + numbered(gov, len(industry) + len(juniors) + len(tech) + 1))
    return "\n\n".join(parts)


def section(text, label, all_labels):
    """Pull out the text after a label like 'ON THE HORIZON:' up to the next label."""
    lower = text.lower()
    start = lower.find(label.lower())
    if start == -1:
        return ""
    start += len(label)
    ends = [lower.find(other.lower(), start) for other in all_labels if other != label]
    ends = [e for e in ends if e != -1]
    body = text[start:min(ends) if ends else len(text)]
    body = re.sub(r"^\*+", "", body)          # Bold marks closing the label itself
    return re.sub(r"[\s*#]+$", "", body).strip()   # Bold marks opening the next label


def bullets(text):
    """Turn '- item' lines into a list of strings."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ", "• ")):
            items.append(line[2:].strip())
    if not items:   # No bullet marks: treat each line as an item
        items = [line.strip() for line in text.splitlines() if line.strip()]
    return [i for i in items if i.lower().rstrip(".") not in ("nothing new", "none", "none yet")]


# ---------------------------------------------------------------------------
# Job 1: Meldrum and Yukon Cornelius discuss (every 6 hours)
# ---------------------------------------------------------------------------

def run_discussion(desk, articles, now, history, briefing):
    print(f"Discussion: {len(articles)} new article(s)")
    news = desk_news(articles)
    when = stamp(now)
    S, G = prompts.STRATEGIST_NAME, prompts.GEOLOGIST_NAME

    first = dict(now=when, news=news, history=history, briefing=briefing)
    s1 = desk.ask("strategist", fill(prompts.FIRST_TAKE, **first))
    g1 = desk.ask("geologist", fill(prompts.FIRST_TAKE, **first))

    s2 = desk.ask("strategist", fill(prompts.REPLY, now=when, news=news, own=s1, theirs=g1,
                                     other=G, other_upper=G.upper()))
    g2 = desk.ask("geologist", fill(prompts.REPLY, now=when, news=news, own=g1, theirs=s1,
                                    other=S, other_upper=S.upper()))

    summary = desk.ask("scribe", fill(prompts.DISCUSSION_SUMMARY, s1=s1, g1=g1, s2=s2, g2=g2))

    return {
        "time": now.isoformat(),
        "article_count": len(articles),
        "strategist": s1, "geologist": g1,
        "strategist_reply": s2, "geologist_reply": g2,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Job 2: Dr I Simon reads the world news and talks with both (every 12 hours)
# ---------------------------------------------------------------------------

OPENING_LABELS = ["GLOBAL VIEW:", "DESK REVIEW:", "QUESTIONS FOR THE STRATEGIST:", "QUESTIONS FOR THE GEOLOGIST:"]
CONCLUSION_LABELS = prompts.HEADINGS + ["CREATIVE ANGLES:", "GLOBAL BRIEFING:", "PREDICTIONS:"]


def run_chief(desk, day, world, now, history, morning):
    print(f"{prompts.CHIEF_NAME} session ({len(world)} world headline(s))")
    when = stamp(now)
    S, G = prompts.STRATEGIST_NAME.upper(), prompts.GEOLOGIST_NAME.upper()

    # Give the Chief the discussions since its last session.
    last_chief = day["chief_sessions"][-1]["time"] if day["chief_sessions"] else ""
    recent = [d for d in day["discussions"] if d["time"] > last_chief] or day["discussions"][-1:]

    material = "\n\n".join(
        f"--- Discussion at {d['time'][:16]} ---\n"
        f"{S} (Strategist):\n{d['strategist']}\n\n{G} (Geologist):\n{d['geologist']}\n\n"
        f"{S}'s REPLY:\n{d['strategist_reply']}\n\n{G}'s REPLY:\n{d['geologist_reply']}"
        for d in recent
    )
    summaries = "\n\n".join(d["summary"] for d in recent)

    previous = ""
    if day["chief_sessions"]:
        previous = ("\nFor continuity, here are your conclusions from your previous review:\n"
                    + day["chief_sessions"][-1]["conclusion"] + "\n")

    opening = desk.ask("chief", fill(prompts.CHIEF_OPENING,
        now=when, history=history, material=material, previous=previous,
        world=numbered(world) or "(No world headlines came in.)"), model=CHIEF_MODEL)

    s_answer = desk.ask("strategist", fill(prompts.ANSWER_CHIEF,
        now=when, summaries=summaries, opening=opening, role="STRATEGIST"))
    g_answer = desk.ask("geologist", fill(prompts.ANSWER_CHIEF,
        now=when, summaries=summaries, opening=opening, role="GEOLOGIST"))

    conclusion = desk.ask("chief", fill(prompts.CHIEF_CONCLUSION,
        now=when, material=material, opening=opening, s_answer=s_answer, g_answer=g_answer,
        scope=prompts.SCOPE_MORNING if morning else prompts.SCOPE_MIDDAY), model=CHIEF_MODEL)

    return {
        "time": now.isoformat(),
        "world_count": len(world),
        "opening": opening,
        "global_view": section(opening, "GLOBAL VIEW:", OPENING_LABELS),
        "strategist_answer": s_answer,
        "geologist_answer": g_answer,
        "conclusion": conclusion,
        # The four headings shown on the morning page, each with its bullets.
        "sections": [[h.rstrip(":").capitalize(), bullets(section(conclusion, h, CONCLUSION_LABELS))]
                     for h in prompts.HEADINGS],
        "creative": bullets(section(conclusion, "CREATIVE ANGLES:", CONCLUSION_LABELS)),
        "briefing": section(conclusion, "GLOBAL BRIEFING:", CONCLUSION_LABELS),
    }


# ---------------------------------------------------------------------------
# Job 3: the morning brief (every morning)
# ---------------------------------------------------------------------------

# Used when the news editor doesn't tag a headline. First match wins.
GROUP_KEYWORDS = [
    ("safety", r"killed|dead|death|fatal|collapse|air ?strike|clash|illegal|artisanal|spill|toxic|accident|protest|unrest|militant"),
    ("markets", r"price|forecast|outlook|surplus|deficit|lme|inventor|rally|slump"),
    ("technology", r"battery|batteries|sodium|solid-state|lfp|cathode|recycl|substitut|rare-earth-free|cobalt-free|autonom|data cent|ai |grid|electrif|technolog"),
    ("exploration", r"g/t|drill|intercept|intersect|discover|assay|exploration|soil sampl|geophys"),
    ("projects", r"approv|expansion|construction|restart|first gold|commission|feasib|permit|plant|project"),
    ("deals_policy", r"acqui|takeover|merger|to buy|\bbuys?\b|\bsale\b|ipo|financing|placement|stake|bid|tax|royalt|court|law|ban|government|minister|tariff|export|policy|results|profit|earnings"),
    ("production", r"output|production|guidance|strike|halt|ramp|jobs|labou?r|cost"),
]
LANE_DEFAULTS = {"juniors": "exploration", "tech": "technology"}


def guess_group(article):
    if article.get("lane") in LANE_DEFAULTS:
        return LANE_DEFAULTS[article["lane"]]
    title = article["title"].lower()
    for group, pattern in GROUP_KEYWORDS:
        if re.search(pattern, title):
            return group
    return "production"


def sort_headlines(desk, articles):
    """Ask the news editor to tag each headline with a group and a region,
    and to pick the top stories. Returns the list of top stories."""
    batch = articles[-300:]   # The most recent 300 are plenty for one morning
    reply = desk.ask("editor", fill(prompts.SORT_HEADLINES, headlines=numbered(batch)), max_tokens=LONG_TOKENS)

    for match in re.finditer(r"^\s*(\d+)\s*\|\s*([a-z_]+)\s*\|\s*([a-z]+)", reply, re.MULTILINE | re.IGNORECASE):
        number, group, region = int(match[1]), match[2].lower(), match[3].lower()
        if 1 <= number <= len(batch):
            if group in GROUPS or group == "skip":
                batch[number - 1]["group"] = group
            if region in REGIONS:
                batch[number - 1]["region"] = region

    # Anything the editor missed gets a sensible default.
    for article in articles:
        article.setdefault("group", guess_group(article))
        article.setdefault("region", "global")

    top = []
    top_line = re.search(r"TOP:\s*([\d,\s]+)", reply, re.IGNORECASE)
    if top_line:
        for number in re.findall(r"\d+", top_line[1])[:5]:
            if 1 <= int(number) <= len(batch) and batch[int(number) - 1]["group"] != "skip":
                top.append(batch[int(number) - 1])
    return top


def news_only(articles):
    """Headlines without the government records (those have their own section)."""
    return [a for a in articles if a.get("lane") != "gov"]


def run_referee(desk, ledger, day, now):
    names = {"strategist": prompts.STRATEGIST_NAME, "geologist": prompts.GEOLOGIST_NAME}
    waiting = [i for i in predictions.open_items(ledger) if i["author"] in predictions.SCORED]
    if not waiting:
        return
    lines = "\n".join(f"{i['id']} ({names[i['author']]}, made {i['made']}): {i['text']} | BY: {i['by']} "
                       f"| CONFIDENCE: {i['confidence']}"
                       + (f" | EVIDENCE SO FAR: {'; '.join(e['note'] for e in i['evidence'][-3:])}" if i["evidence"] else "")
                       for i in waiting)
    reply = desk.ask("chief", fill(prompts.REFEREE, now=stamp(now), today=str(now.date()),
                                   open_predictions=lines, headlines=numbered(day["articles"][-250:])), model=CHIEF_MODEL)
    changed = predictions.apply_referee(ledger, reply, now.date())
    print(f"  {prompts.CHIEF_NAME} ruled on {changed} prediction(s)")


def run_brief(desk, day, now, docs_dir, ledger, history):
    print("Morning brief")
    when = stamp(now)
    brief = {"time": now.isoformat(), "date_label": f"{now:%A, %B} {now.day}",
             "article_count": len(day["articles"])}

    if not day["discussions"]:
        brief["quiet"] = True
    else:
        summaries = "\n\n".join(d["summary"] for d in day["discussions"])
        chief = day["chief_sessions"][-1] if day["chief_sessions"] else {}

        # The news editor sorts the headlines into groups and picks the top stories.
        brief["top"] = sort_headlines(desk, news_only(day["articles"]))

        # Dr I Simon referees the analysts' open predictions against today's news.
        run_referee(desk, ledger, day, now)

        # Meldrum and Yukon Cornelius each write a very short summary, and may
        # add up to two new predictions (tracked in the ledger).
        for agent in ("strategist", "geologist"):
            reply = desk.ask(agent, fill(prompts.SHORT_SUMMARY, now=when, summaries=summaries,
                                         own_open=predictions.as_lines(predictions.open_items(ledger, agent))))
            predictions.add(ledger, agent, predictions.parse(reply), now.date())
            brief[f"{agent}_short"] = predictions.strip_predictions(reply)

        # Dr I Simon's own predictions from today's sessions go in the ledger too.
        for session in day["chief_sessions"]:
            found = predictions.parse(section(session["conclusion"], "PREDICTIONS:", CONCLUSION_LABELS))
            predictions.add(ledger, "chief", found, now.date())

        # ...then The Rat reads all three agents' work and writes the poem.
        tangent = random.random() < POET_TANGENT_CHANCE
        claims_hint = ""
        if day["claims_new"]:
            claims_hint = prompts.POEM_CLAIMS_HINT.format(packages="\n".join(
                f"- {p['count']} claims in {p['state']} ({p['source']})" for p in day["claims_new"]))
        brief["poem"] = desk.ask("poet", fill(prompts.POEM,
            strategist_short=brief["strategist_short"],
            geologist_short=brief["geologist_short"],
            chief_conclusion=chief.get("conclusion", "(No review today.)"),
            tangent=prompts.POEM_TANGENT if tangent else "", claims_hint=claims_hint))

        # Dr I Simon's two three-sentence updates for the Quick Look page.
        quick = desk.ask("chief", fill(prompts.QUICK_LOOK, now=when,
            world=numbered(day.get("world", [])[-60:]) or "(No world headlines today.)",
            conclusion=(chief.get("global_view", "") + "\n\n" + chief.get("conclusion", "")).strip() or "(No review today.)",
            top=numbered(brief["top"]) or numbered(news_only(day["articles"])[:12])), model=CHIEF_MODEL)
        brief["quick_world"] = section(quick, "WORLD:", ["WORLD:", "MINING:"])
        brief["quick_mining"] = section(quick, "MINING:", ["WORLD:", "MINING:"])

        # Dr I Simon's takeaways under the four headings. (An older-format
        # session only has a plain list, shown under one heading.)
        brief["sections"] = chief.get("sections") or [["Big takeaways", chief.get("takeaways", [])]]
        brief["creative"] = chief.get("creative", [])
        brief["global_view"] = chief.get("global_view", "")
        brief["chief_conclusion"] = chief.get("conclusion", "")

    brief["discussions"] = day["discussions"]
    brief["chief_sessions"] = day["chief_sessions"]
    brief["articles"] = news_only(day["articles"])
    brief["world"] = day.get("world", [])

    # Government records, filings and safety reports.
    for key in ("claims_new", "claims_active", "notices", "reviews", "filings", "triage_log", "gov_notes"):
        brief[key] = day[key]

    # The prediction scorecard.
    predictions.expire_quietly(ledger, now.date())
    brief["scorecard"] = predictions.scorecard(ledger)
    brief["open_predictions"] = [i for i in predictions.open_items(ledger) if i["author"] in predictions.SCORED]
    brief["chief_predictions"] = predictions.open_items(ledger, "chief")
    brief["results"] = [i for i in ledger["items"] if i["status"] != "open" and i["author"] in predictions.SCORED][-15:][::-1]
    brief["predictions_today"] = [i for i in ledger["items"] if i["made"] == str(now.date())]

    # The desk's memory, for the "memory" section at the bottom of the Brief.
    years = history.get("years", {})
    brief["memory"] = {"year": [max(years), years[max(years)]] if years else None,
                       "months": [[m["label"], m["text"]] for m in history.get("months", [])[-12:]][::-1],
                       "weeks": [[w["label"], w["text"]] for w in history.get("weeks", [])[-12:]][::-1],
                       "first_week_due": None if history.get("weeks") else
                       str(week_of(now.date())[1] + timedelta(days=1))}
    brief["themes"] = theme_tracker(history)

    build_page(brief, docs_dir / "brief.html")
    return brief


# ---------------------------------------------------------------------------
# Job 4: The Rat keeps the history log (every morning, after the brief)
# ---------------------------------------------------------------------------

def history_text(history, ledger, today):
    """The memory block the agents read before every analysis, from the long
    view to the recent: the year so far, the latest monthly summaries, the
    weekly summaries since then, and the days not yet in a weekly summary."""
    names = {k: DISPLAY_NAMES[k] for k in ("strategist", "geologist", "chief")}
    if not any(history.get(k) for k in ("background", "years", "months", "weeks", "entries")) and not ledger["items"]:
        return fill(prompts.HISTORY_EMPTY)
    parts = []
    years = history.get("years", {})
    if years:
        year = max(years)
        parts.append(f"THE YEAR SO FAR ({year}):\n{years[year]}")
    elif history.get("background"):
        parts.append("BACKGROUND (the desk's memory from before weekly summaries began):\n" + history["background"])
    months = history.get("months", [])[-HISTORY_MONTHS:]
    for m in months:
        parts.append(f"--- Monthly summary: {m['label']} ---\n{m['text']}")
    last_month_end = months[-1]["end"] if months else ""
    weeks = [w for w in history.get("weeks", []) if w["end"] > last_month_end][-HISTORY_WEEKS:]
    if not weeks:
        weeks = history.get("weeks", [])[-1:]
    for w in weeks:
        parts.append(f"--- Weekly summary: week of {w['label']} ---\n{w['text']}")
    last_week_end = history["weeks"][-1]["end"] if history.get("weeks") else ""
    days = [e for e in history.get("entries", []) if e["date"] > last_week_end][-HISTORY_DAYS:]
    for e in days or history.get("entries", [])[-2:]:
        parts.append(f"--- Daily log entry: {e['date_label']} ---\n{e['entry']}")
    parts.append(predictions.memory_block(ledger, names, today))
    return fill(prompts.HISTORY_BLOCK, log="\n\n".join(parts))


def restart_history(desk, history, now, history_file):
    """One-time step: restart the long-term memory in the new format.

    The old log is kept, untouched, in data/history_v1_archive.json. The Rat
    reads it once and writes a fresh long-term memory from it. Old daily
    entries and old predictions are not carried over."""
    if history.get("format") == 3:
        return history   # Already done
    if history.get("format") == 2 and not history.get("rebuild_pending"):
        return upgrade_history(history, history_file)

    archive_file = history_file.parent / "history_v1_archive.json"
    if history.get("format") != 2:
        if not history.get("long_term") and not history.get("entries"):
            return upgrade_history({"long_term": "", "entries": []}, history_file)   # Nothing to carry over
        if not archive_file.exists():
            save_json(archive_file, history)
        history = {"format": 2, "long_term": "", "entries": [], "rebuild_pending": True}

    old = load_json(archive_file, None)
    if old:
        print(f"{prompts.POET_NAME} restarts the long-term memory in the new format")
        old_labels = ["EVENTS:", "DESK VIEWS:", "PREDICTIONS MADE:", "THREADS TO WATCH:"]
        old_events = "\n\n".join(
            f"--- {e['date']} ---\n{section(e['entry'], 'EVENTS:', old_labels)[:2500]}"
            for e in old.get("entries", [])[-31:])
        memory = desk.ask("historian", fill(prompts.MEMORY_REBUILD,
            now=stamp(now), today=str(now.date()),
            old_memory=old.get("long_term") or "(empty)", old_events=old_events or "(none)"),
            max_tokens=LONG_TOKENS)
        if memory.startswith("["):
            print("  The rebuild didn't work this time. It will be tried again on the next run.")
        else:
            history["long_term"] = memory
            history.pop("rebuild_pending", None)
    else:
        history.pop("rebuild_pending", None)

    if history.get("rebuild_pending"):
        save_json(history_file, history)
        return history
    return upgrade_history(history, history_file)


def upgrade_history(history, history_file):
    """One-time step: move to the week / month / year memory. The old long-term
    memory becomes the 'background' the first yearly account is built from."""
    history = {"format": 3, "background": history.get("long_term", ""), "entries": history.get("entries", []),
               "weeks": [], "months": [], "years": {}}
    save_json(history_file, history)
    return history


def run_history(desk, day, brief, now, history):
    print(f"{prompts.POET_NAME} updates the history log")
    when = stamp(now)

    # 1. Today's log entry, from the day's headlines and the desk's work.
    entry = desk.ask("historian", fill(prompts.HISTORY_ENTRY,
        now=when,
        headlines=desk_news(day["articles"]),
        summaries="\n\n".join(d["summary"] for d in day["discussions"]),
        chief_conclusions="\n\n".join(c["conclusion"] for c in day["chief_sessions"]) or "(No review today.)",
        strategist_short=brief.get("strategist_short", ""),
        geologist_short=brief.get("geologist_short", ""),
        predictions_today="\n".join(
            f"- {DISPLAY_NAMES[i['author']]}: PREDICTION: {i['text']} | BY: {i['by']} | CONFIDENCE: {i['confidence']}"
            for i in brief.get("predictions_today", [])) or "None."), max_tokens=LONG_TOKENS)
    if entry.startswith("["):        # The Rat didn't reply; keep the log as it was
        return history
    history["entries"] = [e for e in history["entries"] if e["date"] != str(now.date())]
    history["entries"].append({"date": str(now.date()), "date_label": brief["date_label"], "entry": entry})

    # 2. Roll finished weeks into weekly summaries, finished months into monthly
    #    summaries, and the months into the account of the year.
    roll_up(desk, history, now)
    return history


THEME_STATES = ("new", "building", "steady", "fading", "resolved")


def theme_lines(text):
    """The themes in one weekly summary: [(name, what happened, state)]."""
    found = []
    for line in section(text, "MAIN THEMES:", ["MAIN THEMES:", "REGIONS:", "TECHNOLOGY & DEMAND:", "DESK VIEWS:", "AHEAD:"]).splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or line.lower().startswith("none"):
            continue
        state = re.search(r"\[(\w+)\]\s*\.?$", line)
        line = line[:state.start()].strip() if state else line
        split = re.split(r"\s*(?::|—|–| - )\s*", line, maxsplit=1)
        name, what = (split + [""])[:2]
        found.append((name.strip(" *").rstrip("."), what.strip(), state[1].lower() if state else ""))
    return found


def theme_tracker(history):
    """Every theme the weekly summaries have named, with its state each week."""
    weeks = history.get("weeks", [])[-26:]
    themes = {}
    for n, w in enumerate(weeks):
        for name, what, state in theme_lines(w["text"]):
            key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
            t = themes.setdefault(key, {"name": name, "weeks": {}, "latest": "", "first": w["start"]})
            t["weeks"][n] = state or "steady"
            t["latest"], t["name"], t["last"] = what, name, w["start"]
    rows = []
    for t in themes.values():
        marks = [t["weeks"].get(n, "") for n in range(len(weeks))]
        rows.append({"name": t["name"], "marks": marks, "weeks": len(t["weeks"]), "state": marks[-1] or "not seen this week",
                     "latest": t["latest"], "first": t["first"], "last": t["last"]})
    rows.sort(key=lambda r: (r["last"], r["weeks"]), reverse=True)
    return {"weeks": [w["label"] for w in weeks], "rows": rows[:20]}


def week_of(day):
    """The Monday and Sunday of the week containing `day`."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def roll_up(desk, history, now):
    today, when = now.date(), stamp(now)
    weeks, months = history.setdefault("weeks", []), history.setdefault("months", [])
    years = history.setdefault("years", {})

    # Weeks: every finished week (Monday to Sunday) that has daily entries and no summary yet.
    done = {w["start"] for w in weeks}
    pending = {}
    for e in history["entries"]:
        monday, sunday = week_of(datetime.fromisoformat(e["date"]).date())
        if sunday < today and str(monday) not in done:
            pending.setdefault((monday, sunday), []).append(e)
    for (monday, sunday), entries in sorted(pending.items())[:2]:      # At most two per run
        label = f"{monday:%b} {monday.day} to {sunday:%b} {sunday.day}, {sunday.year}"
        known = sorted({name for w in weeks[-8:] for name, _, _ in theme_lines(w["text"])})
        text = desk.ask("historian", fill(prompts.WEEK_SUMMARY, now=when, label=label,
            entries="\n\n".join(f"--- {e['date_label']} ---\n{e['entry']}" for e in entries),
            previous=weeks[-1]["text"] if weeks else "(None: this is the first weekly summary.)",
            known="\n".join("- " + k for k in known) or "- None yet."),
            max_tokens=LONG_TOKENS)
        if not text.startswith("["):
            weeks.append({"start": str(monday), "end": str(sunday), "label": label, "text": text})
            print(f"  weekly summary written: {label}")
    weeks.sort(key=lambda w: w["start"])

    # Months: every finished month that has weekly summaries (by the week's last day) and no summary yet.
    done = {m["month"] for m in months}
    this_month = f"{today:%Y-%m}"
    waiting = sorted({w["end"][:7] for w in weeks if w["end"][:7] < this_month and w["end"][:7] not in done})
    if len(pending) > 2:
        waiting = []          # Weekly summaries are still catching up; the month waits for them
    for month in waiting[:1]:                                          # At most one per run
        first = datetime.fromisoformat(month + "-01")
        label = f"{first:%B %Y}"
        in_month = [w for w in weeks if w["end"][:7] == month]
        text = desk.ask("historian", fill(prompts.MONTH_SUMMARY, now=when, label=label,
            weeks="\n\n".join(f"--- Week of {w['label']} ---\n{w['text']}" for w in in_month)),
            max_tokens=LONG_TOKENS)
        if text.startswith("["):
            break
        months.append({"month": month, "label": label, "end": in_month[-1]["end"], "text": text})
        months.sort(key=lambda m: m["month"])
        print(f"  monthly summary written: {label}")

        # The year: rewritten from all of that year's monthly summaries.
        year = month[:4]
        background = history.get("background", "")
        text = desk.ask("historian", fill(prompts.YEAR_TREND, now=when, year=year,
            background=fill(prompts.YEAR_BACKGROUND, background=background) if background else "",
            months="\n\n".join(f"--- {m['label']} ---\n{m['text']}" for m in months if m["month"][:4] == year)),
            max_tokens=LONG_TOKENS)
        if not text.startswith("["):
            years[year] = text
            history["background"] = ""        # Folded into the account of the year
            print(f"  account of {year} rewritten")

    history["entries"] = history["entries"][-45:]
    history["weeks"] = weeks[-60:]


# ---------------------------------------------------------------------------
# Job 5: government records (every morning, before the discussion)
# ---------------------------------------------------------------------------

def numbered_replies(reply, words=None):
    """Read lines like '3 | KEEP | reason' -> {3: ('KEEP', 'reason')}, or '3 | note' -> {3: ('', 'note')}."""
    found = {}
    for line in (reply or "").splitlines():
        parts = [p.strip() for p in line.strip().lstrip("-* ").split("|")]
        if len(parts) < 2 or not parts[0].rstrip(".").isdigit():
            continue
        number = int(parts[0].rstrip("."))
        if words:
            verdict = parts[1].upper()
            if verdict in words:
                found[number] = (verdict, " | ".join(parts[2:]))
        else:
            found[number] = ("", " | ".join(parts[1:]))
    return found


def gov_article(title, source, link, kind):
    """A government record, in the same shape as a headline, for the analysts' discussion."""
    return {"title": title, "source": source, "link": link or "#", "lane": "gov", "kind": kind}


def describe_record(number, item):
    if item["id"].startswith("SEC-"):
        return (f"{number}. SEC 8-K: {item['company']} ({item.get('sic_name', '')}), filed {item['date']}. "
                f"Items: {item['items']}.\n   Excerpt: {item.get('text', '')[:1200]}")
    return (f"{number}. MSHA {item['kind']}, {item['date']}: {item['mine']} mine ({item.get('commodity', '')}, "
            f"{item['state']}), operator {item['operator']}. {item.get('detail', '')[:400]}")


def record_title(item):
    if item["id"].startswith("SEC-"):
        return f"{item['company']}: 8-K filing ({item['items']})"
    return f"{item['kind']} at {item['mine']} ({item['state']})"


def run_triage(desk, items, now, day, inbox):
    """Meldrum and Yukon Cornelius screen the filings and safety reports;
    Dr I Simon decides which go in the brief."""
    print(f"Filings and safety reports: {len(items)} to screen")
    S, G = prompts.STRATEGIST_NAME, prompts.GEOLOGIST_NAME
    listing = "\n\n".join(describe_record(n, item) for n, item in enumerate(items, 1))
    s = desk.ask("strategist", fill(prompts.TRIAGE, now=stamp(now), items=listing))
    g = desk.ask("geologist", fill(prompts.TRIAGE, now=stamp(now), items=listing))
    sv, gv = numbered_replies(s, ("KEEP", "DROP")), numbered_replies(g, ("KEEP", "DROP"))
    kept = [n for n in range(1, len(items) + 1) if "KEEP" in (sv.get(n, ("",))[0], gv.get(n, ("",))[0])]

    c = ""
    if kept:
        listing = "\n\n".join(
            describe_record(n, items[n - 1])
            + f"\n   {S}: {' - '.join(sv.get(n, ('no view', '')))}\n   {G}: {' - '.join(gv.get(n, ('no view', '')))}"
            for n in kept)
        c = desk.ask("chief", fill(prompts.FILINGS_DECISION, now=stamp(now), items=listing), model=CHIEF_MODEL)
        cv = numbered_replies(c, ("PAGE", "DROP"))
        for n in kept:
            verdict, takeaway = cv.get(n, ("", ""))
            if verdict == "PAGE":
                item = dict(items[n - 1], takeaway=takeaway, title=record_title(items[n - 1]))
                item.pop("text", None)
                day["filings"].append(item)
                inbox.append(gov_article(f"{item['title']}. {prompts.CHIEF_NAME}: {takeaway}",
                                         "SEC" if item["id"].startswith("SEC-") else "MSHA", item["link"], "filing"))
    print(f"  kept by the analysts: {len(kept)}, chosen for the brief: {sum(1 for f in day['filings'] if f.get('takeaway'))}")
    day["triage_log"].append({"time": now.isoformat(), "count": len(items), "listing": listing,
                              "strategist": s, "geologist": g, "chief": c})


def run_gov(desk, data_dir, day, inbox, now, msha_due, dry_run):
    print("Government records")
    if dry_run:
        claims, notices, reviews, filings, msha = sample_gov()
    else:
        claims = gov_watch.watch_claims(data_dir)
        notices = gov_watch.watch_notices(data_dir)
        reviews = gov_watch.watch_reviews(data_dir)
        filings = gov_watch.watch_8k(data_dir, os.environ.get("SEC_CONTACT_EMAIL", "").strip())
        msha = gov_watch.watch_msha(data_dir) if msha_due else {"new": [], "notes": ["MSHA: checked on Saturdays"]}
    notes = claims["notes"] + notices["notes"] + reviews["notes"] + filings["notes"] + msha["notes"]
    for note in notes:
        print("  " + note)
    day["gov_notes"] = notes

    # New claim packages: Yukon Cornelius notes the likely district and deposit types.
    new = claims["new"]
    if new:
        listing = "\n".join(
            f"{n}. {p['source']}, {p['state']}: claims named '{p['name']}'"
            + (f" (claimant: {p['claimant']})" if p.get("claimant") else "")
            + f", {p['count']} claims, about {p['acres']} acres, filed {p['first']} to {p['last']}"
            + (f", centred near latitude {p['lat']}, longitude {p['lon']}" if p.get("lat") is not None else "")
            + (f" ({p['town']})" if p.get("town") else "")
            for n, p in enumerate(new, 1))
        notes_by_number = numbered_replies(desk.ask("geologist", fill(prompts.CLAIMS_NOTE, packages=listing)))
        for n, p in enumerate(new, 1):
            p["note"] = notes_by_number.get(n, ("", ""))[1]
            p["found"] = str(now.date())
        day["claims_new"] += new
        inbox += [gov_article(f"New claim package: {p['count']} claims named '{p['name']}' in {p['state']} "
                              f"({p['source']}, filed {p['first']} to {p['last']})", "Government records", p["map"], "claims")
                  for p in new]
    day["claims_active"] = claims["active"]

    # Exploration notices: one line for the analysts, the full list for the page.
    if notices["new"]:
        day["notices"] += notices["new"]
        by_state = {}
        for n in notices["new"]:
            by_state[n["state"]] = by_state.get(n["state"], 0) + 1
        inbox.append(gov_article("New BLM exploration notices (under 5 acres): " + ", ".join(
            f"{count} in {state}" for state, count in sorted(by_state.items(), key=lambda x: -x[1])),
            "Government records", "", "notices"))

    # Environmental reviews.
    for r in reviews["new"]:
        day["reviews"].append(r)
        inbox.append(gov_article(f"Environmental review ({r['state'] or 'US'}): {r['title']}", r["source"], r["link"], "review"))

    # Filings and safety reports: screened by the analysts, decided by Dr I Simon.
    items = (filings["new"] + msha["new"])[:MAX_TRIAGE]
    if items:
        run_triage(desk, items, now, day, inbox)


# ---------------------------------------------------------------------------
# Job 6: Yukon Cornelius reads new research papers (every morning)
# ---------------------------------------------------------------------------

RESEARCH_LABELS = ["SUMMARY:", "DEPOSIT TYPE:", "COMMODITIES:", "SETTING:", "WHERE IT APPLIES:", "EXPLORATION:", "NEWS LINK:", "RELEVANCE:"]


def run_research(desk, data_dir, docs_dir, day, now, dry_run):
    print("Research digest")
    if dry_run:
        store = {"seen": [], "pending": [dict(p) for p in SAMPLE_PAPERS], "papers": []}
        note = f"Research (dry run): {len(store['pending'])} sample paper(s)"
    else:
        store, note = research.collect(data_dir, os.environ.get("OPENALEX_API_KEY", "").strip())
    print("  " + note)

    waiting = sorted(store["pending"], key=lambda p: p.get("date", ""), reverse=True)
    # Papers with abstracts first; many publishers don't share abstracts, so those are read from the title.
    batch = ([p for p in waiting if p.get("abstract")] + [p for p in waiting if not p.get("abstract")])[:RESEARCH_PER_DAY]
    no_abstract = []

    if batch:
        headlines = first_by_priority([a for a in day["articles"] if a.get("lane") in ("industry", "juniors")], 80)
        papers = "\n\n".join(f"{n}. {p['title']} ({p['journal']}, {p['date']})\n"
                              f"ABSTRACT: {p['abstract'] or '(not available: judge from the title only)'}"
                              for n, p in enumerate(batch, 1))
        reply = desk.ask("geologist", fill(prompts.RESEARCH_READ, now=stamp(now), papers=papers,
                                           headlines=numbered(headlines) or "(No headlines today.)"),
                         max_tokens=LONG_TOKENS)
        blocks = re.split(r"(?im)^\s*\**PAPER:\s*", reply)
        for block in blocks[1:]:
            number = re.match(r"\s*(\d+)", block)
            if not number or not 1 <= int(number[1]) <= len(batch):
                continue
            p = batch[int(number[1]) - 1]
            for label in RESEARCH_LABELS:
                p[label.rstrip(":").lower().replace(" & ", "_").replace(" ", "_")] = section(block, label, RESEARCH_LABELS)
            p["relevance"] = (p.get("relevance") or "").split()[0].lower().strip(".") if p.get("relevance") else ""
            link = re.search(r"\d+", p.get("news_link", "")) if p.get("news_link", "").lower() not in ("", "none", "none.") else None
            if link and 1 <= int(link[0]) <= len(headlines):
                article = headlines[int(link[0]) - 1]
                p["news"] = {"title": article["title"], "link": article.get("link", "#"),
                             "why": re.sub(r"(?i)^\s*(headline\s*)?(number\s*)?#?\d+\s*[-–—:,.)]*\s*", "", p["news_link"]).strip()}
        for p in batch:
            p["read"] = str(now.date())
            p["title_only"] = not p.get("abstract")

    done = {p["id"] for p in batch + no_abstract}
    store["papers"] = [p for p in batch + no_abstract] + [p for p in store["papers"] if p["id"] not in done]
    store["pending"] = [p for p in store["pending"] if p["id"] not in done]
    research.save(data_dir, store)
    build_research_page(store["papers"], docs_dir / "research.html", now, len(store["pending"]))
    return store["papers"]


# ---------------------------------------------------------------------------
# Dry run: fake replies so you can test for free
# ---------------------------------------------------------------------------

SAMPLE_ARTICLES = [
    {"title": "Copper climbs as Chilean mine strike enters second week", "source": "Sample News", "link": "#", "lane": "industry"},
    {"title": "Indonesia weighs new export levy on nickel products", "source": "Sample Times", "link": "#", "lane": "industry"},
    {"title": "Zambia approves $2bn copper mine expansion", "source": "Sample Weekly", "link": "#", "lane": "industry"},
    {"title": "Lithium prices steady as battery makers rebuild inventories", "source": "Sample Daily", "link": "#", "lane": "industry"},
    {"title": "Three killed in collapse at illegal gold mine in Colombia", "source": "Sample Wire", "link": "#", "lane": "industry"},
    {"title": "Major miner agrees to buy Nevada gold producer", "source": "Sample Post", "link": "#", "lane": "industry"},
    {"title": "Example Gold Corp. Intersects 4.2 g/t Gold over 31 Metres in British Columbia", "source": "Junior Mining Network", "link": "#", "lane": "juniors"},
    {"title": "Sample Lithium Closes $8 Million Private Placement", "source": "Junior Mining Network", "link": "#", "lane": "juniors"},
    {"title": "Carmaker to launch sodium-ion battery cars in 2027", "source": "Sample Tech", "link": "#", "lane": "tech"},
    {"title": "Data centre boom strains copper supply for power grids", "source": "Sample Energy", "link": "#", "lane": "tech"},
    {"title": "Presidential election in Peru heads to runoff", "source": "Sample World", "link": "#", "lane": "world"},
    {"title": "Shipping costs jump as Red Sea attacks resume", "source": "Sample World", "link": "#", "lane": "world"},
]


SAMPLE_PAPERS = [
    {"id": "W1", "title": "Sample: Fluid evolution in a Carlin-type gold system, north-central Nevada", "journal": "Economic Geology",
     "date": "2026-11-10", "authors": "A. Sample, B. Example", "link": "#", "abstract": "Sample abstract about Carlin-type gold."},
    {"id": "W2", "title": "Sample: Porphyry copper fertility indicators in zircon from a continental arc", "journal": "Mineralium Deposita",
     "date": "2026-11-08", "authors": "C. Sample", "link": "#", "abstract": "Sample abstract about porphyry copper."},
]


def sample_gov():
    claims = {"new": [{"key": "dry1", "source": "BLM (federal land)", "state": "NV", "name": "Sample Ridge", "claimant": "",
                       "count": 164, "acres": 3388, "first": "2026-11-02", "last": "2026-11-09", "lat": 40.95, "lon": -116.2,
                       "map": "#", "serials": "NV105000001 to NV105000164", "type": "lode"}],
              "notes": ["BLM federal claims (dry run): sample data"]}
    claims["active"] = list(claims["new"]) + [{"key": "dry2", "source": "Alaska DNR (state land)", "state": "AK",
        "name": "Sample Creek", "claimant": "Sample Exploration Llc", "count": 212, "acres": 33920, "first": "2026-10-20",
        "last": "2026-10-28", "lat": 63.1, "lon": -150.3, "map": "#", "serials": "", "type": "state claim"}]
    for pkg in claims["active"]:
        pkg["town"] = gov_watch.nearest_town(pkg["lat"], pkg["lon"])
        pkg["lookup"] = gov_watch.MLRS_LOOKUP if pkg["source"].startswith("BLM") else ""
    notices = {"new": [{"serial": "NV105099999", "name": "Sample Notice", "state": "NV", "created": "2026-11-12",
                        "status": "Filed", "acres": 4.5, "map": "#",
                        "town": gov_watch.nearest_town(39.5, -117.1)}], "notes": ["BLM notices (dry run)"]}
    reviews = {"new": [{"id": "FR-dry", "title": "Notice of Intent To Prepare an Environmental Impact Statement for the Sample Gold Mine",
                        "link": "#", "date": "2026-11-12", "source": "Federal Register · Bureau of Land Management", "state": "NV",
                        "summary": ""}], "notes": ["Reviews (dry run)"]}
    filings = {"new": [{"id": "SEC-dry", "company": "Sample Mining Corp", "cik": "1", "date": "2026-11-13", "sic": "1040",
                        "sic_name": "Gold and Silver Ores", "items": "2.05 Closure or layoff costs", "link": "#",
                        "text": "Item 2.05 The company will place its Sample mine on care and maintenance."}], "notes": ["SEC (dry run)"]}
    msha = {"new": [{"id": "ACC-dry", "kind": "Fatal accident", "date": "2026-11-05", "mine": "Sample", "operator": "Sample Operator",
                     "state": "NV", "commodity": "Gold Ore", "coal_metal": "M", "detail": "Powered haulage. Sample narrative.",
                     "mine_id": "2600000", "link": "#"}], "notes": ["MSHA (dry run)"]}
    return claims, notices, reviews, filings, msha


def fake_reply(agent, task):
    if "KEEP or DROP" in task:
        return "1 | KEEP | (dry run) A closure matters for the region.\n2 | KEEP | (dry run) A fatality is significant."
    if "PAGE or DROP" in task:
        return "1 | PAGE | (dry run) The closure removes a mid-sized Nevada producer.\n2 | PAGE | (dry run) A haulage fatality."
    if "district, belt or trend" in task:
        return "1 | (Dry run) This location most likely sits on the Battle Mountain-Eureka trend, known for Carlin-type gold."
    if "PAPER: number" in task:
        return ("PAPER: 1\nSUMMARY: (dry run) Fluid inclusions trace the gold-bearing fluids to a deep magmatic source.\nDEPOSIT TYPE: Carlin-type gold\nCOMMODITIES: Gold\nSETTING: (dry run) Paleozoic carbonates\n"
                "WHERE IT APPLIES: (dry run) Nevada's Carlin and Battle Mountain-Eureka trends.\nEXPLORATION: (dry run) Use the fluid signature as a vector.\n"
                "NEWS LINK: 6 - (dry run) A Nevada gold deal in the news.\nRELEVANCE: high\n\n"
                "PAPER: 2\nDEPOSIT TYPE: Porphyry copper\nCOMMODITIES: Copper, molybdenum\nSETTING: (dry run) Continental arc\n"
                "WHERE IT APPLIES: (dry run) Arizona porphyry belt; Alaska's Pebble district.\nEXPLORATION: (dry run) Screen intrusions with zircon chemistry.\n"
                "NEWS LINK: None\nRELEVANCE: medium")
    if "opens the reader's Quick Look page" in task or "open the reader's Quick Look page" in task:
        return ("WORLD:\n(Dry run) First sentence on the world. Second sentence on the world. Third sentence on the world.\n"
                "MINING:\n(Dry run) First sentence on mining. Second sentence on mining. Third sentence on mining.")
    if agent == "curator":
        return ("1 | (dry run) A trail work party he could join this weekend.\n2 | (dry run) A brewery fundraiser downtown.\n"
                "4 | (dry run) A local story worth knowing.\n7 | (dry run) A new middleweight ADV bike review.\n"
                "10 | (dry run) A fisheries decision.\n13 | (dry run) A strong trial result.\n"
                "ANIMAL | (dry run) A giraffe's shy cousin with a zebra's stockings: my kind of mix-up.\n"
                "PLACE | (dry run) An island whose dragon's blood trees look like upturned umbrellas.\n"
                "READ | R1 | (dry run) Two sentences on why this long read is worth his time.\n"
                "VOLUNTEER | (dry run) Two sentences on what they do and how to help.")
    if agent == "historian" and "summary for the week of" in task:
        return "MAIN THEMES:\n- (Dry run) Chile copper: strikes spread [building]\nREGIONS:\n- None.\nTECHNOLOGY & DEMAND:\n- None.\nDESK VIEWS:\n- None.\nAHEAD:\n- None."
    if agent == "historian" and ("THEMES OF THE MONTH" in task or "THE YEAR'S MAIN TRENDS" in task):
        return "(Dry run) A monthly or yearly summary would go here."
    if "You are refereeing" in task:
        return "P1 | evidence | (dry run) Headline 3 shows progress but doesn't settle it."
    if agent == "poet":
        return ("The grid wants copper, the miners want rain,\nand Chile's strike rolls on again.\n"
                "A chameleon's tongue is twice its size,\nlike every junior's drill-hole prize.")
    if agent == "editor":
        return "TOP: 1, 3, 9"   # No tags, so the dry run sorts headlines by keyword instead
    if agent == "historian":
        if "restarting its long-term memory" in task:
            return ("INDUSTRY HEALTH:\n- (Dry run) Rebuilt from the old log.\nREGIONAL STORYLINES:\n- (Dry run) Latin America, Chile: ...\n"
                    "TECHNOLOGY TRACKER:\n- (Dry run) ...\nEVENTS CALENDAR:\n- (Dry run) ...\n"
                    "THE DESK'S VIEWS:\n- Restarted.\nOPEN PREDICTIONS:\n- None yet.\nRESULTS:\n- None yet.")
        return ("EVENTS:\n- (Dry run) Latin America, Chile: the day's events would go here.\n"
                "DESK VIEWS:\n- (Dry run) Each person's main conclusions.\nTECHNOLOGY NOTES:\n- (Dry run) Sodium-ion: less lithium.\n"
                "UPCOMING EVENTS:\n- (Dry run) Events ahead.\nPREDICTIONS MADE:\nNone.")
    if agent == "chief" and "Now write your conclusions" not in task:
        return ("GLOBAL VIEW:\n(Dry run) Dr I Simon's read of the world news would go here.\n"
                "DESK REVIEW:\n(Dry run) The review of the analysts' work.\n"
                "QUESTIONS FOR THE STRATEGIST:\n- (Dry run) A question.\nQUESTIONS FOR THE GEOLOGIST:\n- (Dry run) A question.")
    if agent == "chief":
        return ("INDUSTRY HEALTH:\n- (Dry run) Supply disruptions are doing more for copper than demand is.\n"
                "REGIONAL TRENDS:\n- Latin America: (dry run) labour disputes are spreading through Chile's copper belt.\n"
                "- Africa: (dry run) Zambia is courting new copper investment.\n"
                "TECHNOLOGY & DEMAND:\n- (Dry run) Sodium-ion cars would take demand from lithium; grid buildout adds copper.\n"
                "ON THE HORIZON:\n- (Dry run) Peru's runoff election could reshape mining policy within months.\n"
                "CREATIVE ANGLES:\n- (Dry run) Watch smelter fees as an early signal. (speculation level: medium)\n"
                "GLOBAL BRIEFING:\n(Dry run) World events the analysts should keep in mind before their next discussions.\n"
                "PREDICTIONS:\nPREDICTION: (dry run example) | BY: 2027-01-31 | CONFIDENCE: low")
    if "60 words" in task:
        return (f"(Dry run) A very short summary from the {agent} would go here: two or three sentences on the day's news.\n"
                f"PREDICTION: (dry run) A {agent} prediction | BY: 2027-03-31 | CONFIDENCE: medium")
    return (f"INDUSTRY HEALTH:\n(Dry run) The {agent}'s reply would go here.\nREGIONAL TRENDS:\n- A point\n"
            "TECHNOLOGY & DEMAND:\n- Another point\nON THE HORIZON:\n- A third point")


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="run every job now, ignoring the schedule")
    parser.add_argument("--dry-run", action="store_true", help="fake replies, no API cost")
    args = parser.parse_args()

    # Dry runs use a separate folder so they never mix with your real data.
    data_dir = Path("preview/data") if args.dry_run else Path("data")
    docs_dir = Path("preview") if args.dry_run else Path("docs")

    state_file = data_dir / "state.json"      # When each job last ran, and the latest global briefing
    inbox_file = data_dir / "inbox.json"      # Mining headlines waiting for the next discussion
    world_file = data_dir / "world.json"      # World headlines waiting for Dr I Simon
    day_file = data_dir / "day.json"          # Everything since the last morning brief
    history_file = data_dir / "history.json"  # The Rat's log
    ledger_file = data_dir / "predictions.json"   # Every prediction, and how it turned out

    now = datetime.now(TIMEZONE)
    state = load_json(state_file, {})
    inbox = load_json(inbox_file, [])
    world = load_json(world_file, [])
    day = {**empty_day(), **load_json(day_file, {})}
    desk = Desk(dry_run=args.dry_run)

    # Which jobs are due this run?
    discussion_due = args.now or is_due(state, "discussion", now, DISCUSSION_HOURS)
    chief_due = args.now or is_due(state, "chief", now, CHIEF_HOURS)
    brief_due = args.now or is_due(state, "brief", now, BRIEF_HOURS)
    gov_due = args.now or is_due(state, "gov", now, GOV_HOURS)
    last_msha = state.get("msha")
    msha_due = gov_due and (args.now or not last_msha or (now.weekday() == MSHA_DAY and last_msha[:10] != str(now.date()))
                            or now - datetime.fromisoformat(last_msha) > timedelta(days=8))

    # About once an hour: fetch news. Mining news goes to the inbox, world news to Dr I Simon's pile.
    last_fetch = state.get("fetch")
    if args.now or not last_fetch or now - datetime.fromisoformat(last_fetch) >= timedelta(minutes=FETCH_EVERY_MINUTES):
        new = fetch_news.fetch_new_articles(data_dir)
        if args.dry_run and not new and not inbox:
            new = [dict(a) for a in SAMPLE_ARTICLES]
        inbox += [a for a in new if a["lane"] != "world"]
        world = (world + [a for a in new if a["lane"] == "world"])[-MAX_WORLD:]
        save_json(inbox_file, inbox)
        save_json(world_file, world)
        state["fetch"] = now.isoformat()
        save_json(state_file, state)
        print(f"{stamp(now)}: {len(new)} new article(s). Waiting: {len(inbox)} mining, {len(world)} world.")
    else:
        print(f"{stamp(now)}: news was checked recently. Waiting: {len(inbox)} mining, {len(world)} world.")

    if not (discussion_due or chief_due or brief_due or gov_due):
        print("No jobs due.")
        return

    # Every morning (2am): government records. New items join the inbox for the next discussion.
    if gov_due:
        run_gov(desk, data_dir, day, inbox, now, msha_due, args.dry_run)
        save_json(inbox_file, inbox)
        save_json(day_file, day)
        state["gov"] = now.isoformat()
        if msha_due:
            state["msha"] = now.isoformat()
        save_json(state_file, state)
        if not (discussion_due or chief_due or brief_due):
            print(f"Done. {desk.calls} agent call(s) this run.")
            return

    # The Rat's history log: the desk's memory of past days.
    history = {"long_term": "", "entries": [], **load_json(history_file, {"format": 3, "background": ""})}
    history = restart_history(desk, history, now, history_file)
    ledger = load_json(ledger_file, predictions.empty_ledger())
    predictions.repair(ledger)
    memory = history_text(history, ledger, now.date())

    # Every 6 hours: Meldrum + Yukon Cornelius discussion.
    if discussion_due:
        # A handful of headlines isn't worth a discussion, unless the morning brief needs it.
        if len(inbox) >= MIN_ARTICLES or (inbox and brief_due):
            briefing = (fill(prompts.BRIEFING_BLOCK, briefing=state["global_briefing"])
                        if state.get("global_briefing") else fill(prompts.BRIEFING_EMPTY))
            day["discussions"].append(run_discussion(desk, inbox, now, memory, briefing))
            day["articles"] += inbox
            inbox = []
            save_json(inbox_file, inbox)
            save_json(day_file, day)
        elif inbox:
            print(f"Discussion: only {len(inbox)} new article(s), holding them for the next slot")
        else:
            print("Discussion: no new articles, skipping")
        state["discussion"] = now.isoformat()
        save_json(state_file, state)

    # Every 12 hours: Dr I Simon's session.
    if chief_due:
        last_chief = day["chief_sessions"][-1]["time"] if day["chief_sessions"] else ""
        if any(d["time"] > last_chief for d in day["discussions"]):
            session = run_chief(desk, day, world, now, memory, morning=brief_due)
            day["chief_sessions"].append(session)
            day["world"] = (day["world"] + world)[-120:]
            world = []
            save_json(world_file, world)
            save_json(day_file, day)
            if session["briefing"]:
                state["global_briefing"] = session["briefing"]
        else:
            print(f"{prompts.CHIEF_NAME}: nothing new to review, skipping")
        state["chief"] = now.isoformat()
        save_json(state_file, state)

    # Every morning: sorted headlines, short summaries, poem, and the page. Then start a new day.
    if brief_due:
        brief = run_brief(desk, day, now, docs_dir, ledger, history)
        save_json(ledger_file, ledger)
        save_json(data_dir / "archive" / f"{now.date()}.json", brief)
        try:
            papers = run_research(desk, data_dir, docs_dir, day, now, args.dry_run)
        except Exception as error:   # The research page is a bonus: never let it stop the brief
            print(f"Research digest: FAILED ({error})")
            papers = load_json(data_dir / "research.json", {}).get("papers", [])
        build_quick_page(brief, papers, docs_dir / "index.html", now)
        password = "dry-run" if args.dry_run else os.environ.get("DAN_PASSWORD", "").strip()
        if not password:
            print("Dan's Tab: skipped (add the DAN_PASSWORD secret to turn it on; it is never published unlocked)")
        else:
            try:
                print("Dan's Tab")
                page = dan.build(desk.ask, fill, prompts, data_dir, now, password, args.dry_run)
                # The status lines stay inside the locked page, not in GitHub's public run log.
                print(f"  built ({sum(len(s['items']) for s in page['sections'])} items, "
                      f"{sum('FAILED' in n for n in page['status'])} source(s) failed)")
                build_dan_page(page, docs_dir / "dan.html", now, password)
            except Exception as error:   # Dan's Tab is separate from the desk: never let it stop the brief
                print(f"Dan's Tab: FAILED ({type(error).__name__})")
        if not brief.get("quiet"):
            history = run_history(desk, day, brief, now, history)
            save_json(history_file, history)
        save_json(day_file, empty_day())
        state["brief"] = now.isoformat()
        save_json(state_file, state)
        print(f"Pages written to {docs_dir}: index.html (Quick Look), brief.html, research.html, dan.html")

    print(f"Done. {desk.calls} agent call(s) this run.")


if __name__ == "__main__":
    main()
