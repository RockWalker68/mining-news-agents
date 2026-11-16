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
      at docs/index.html. Finally The Rat (as historian) writes the day's
      log entry and updates the desk's long-term memory in data/history.json,
      which the other three read before every analysis.

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
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import fetch_news
import prompts
from page import GROUPS, REGIONS, build_page

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("America/Los_Angeles")   # Handles daylight saving automatically

DISCUSSION_HOURS = [3, 9, 15, 21]    # Meldrum + Yukon Cornelius talk (every 6 hours)
CHIEF_HOURS = [3, 15]                # Dr I Simon's session (every 12 hours)
BRIEF_HOURS = [3]                    # Morning brief and page

MODEL = "claude-haiku-5-5"           # Model for every agent
CHIEF_MODEL = MODEL                  # Change to "claude-sonnet-5-5" for a stronger Chief

# Length limit for each reply, including the model's thinking. It's a ceiling,
# not a target: you only pay for what's actually written. Too low = blank replies.
MAX_TOKENS = 8000

FETCH_EVERY_MINUTES = 50             # How often to check the news sources
MIN_ARTICLES = 5                     # Fewer new headlines than this: wait for the next slot

# The most headlines of each kind handed to the analysts in one discussion.
# When there are more, North American stories and the three priority groups
# (below) are kept first.
MAX_INDUSTRY = 80
MAX_JUNIORS = 30                     # Junior-company press releases
MAX_TECH = 20
MAX_WORLD = 60                       # World headlines Dr I Simon reads per session

POET_TANGENT_CHANCE = 2 / 3          # How often The Rat gets the astrology/lizard/fish nudge
HISTORY_DAYS = 7                     # Days of The Rat's daily log entries the agents read

def empty_day():
    """A fresh, empty record for a new day."""
    return {"articles": [], "discussions": [], "chief_sessions": [], "world": []}


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
    if industry:
        parts.append("MINING INDUSTRY NEWS:\n" + numbered(industry))
    if juniors:
        parts.append("JUNIOR-COMPANY PRESS RELEASES (read these as a set, for patterns in where "
                     "drilling and money are going, not one by one):\n" + numbered(juniors, len(industry) + 1))
    if tech:
        parts.append("TECHNOLOGY AND DEMAND NEWS:\n" + numbered(tech, len(industry) + len(juniors) + 1))
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
    reply = desk.ask("editor", fill(prompts.SORT_HEADLINES, headlines=numbered(batch)))

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


def run_brief(desk, day, now, docs_dir):
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
        brief["top"] = sort_headlines(desk, day["articles"])

        # Meldrum and Yukon Cornelius each write a very short summary...
        brief["strategist_short"] = desk.ask("strategist", fill(prompts.SHORT_SUMMARY,
            now=when, summaries=summaries))
        brief["geologist_short"] = desk.ask("geologist", fill(prompts.SHORT_SUMMARY,
            now=when, summaries=summaries))

        # ...then The Rat reads all three agents' work and writes the poem.
        tangent = random.random() < POET_TANGENT_CHANCE
        brief["poem"] = desk.ask("poet", fill(prompts.POEM,
            strategist_short=brief["strategist_short"],
            geologist_short=brief["geologist_short"],
            chief_conclusion=chief.get("conclusion", "(No review today.)"),
            tangent=prompts.POEM_TANGENT if tangent else ""))

        # Dr I Simon's takeaways under the four headings. (An older-format
        # session only has a plain list, shown under one heading.)
        brief["sections"] = chief.get("sections") or [["Big takeaways", chief.get("takeaways", [])]]
        brief["creative"] = chief.get("creative", [])
        brief["global_view"] = chief.get("global_view", "")
        brief["chief_conclusion"] = chief.get("conclusion", "")

    brief["discussions"] = day["discussions"]
    brief["chief_sessions"] = day["chief_sessions"]
    brief["articles"] = day["articles"]
    brief["world"] = day.get("world", [])

    build_page(brief, docs_dir / "index.html")
    return brief


# ---------------------------------------------------------------------------
# Job 4: The Rat keeps the history log (every morning, after the brief)
# ---------------------------------------------------------------------------

def history_text(history):
    """Turn The Rat's log into the memory block the other agents read."""
    if not history["long_term"] and not history["entries"]:
        return fill(prompts.HISTORY_EMPTY)
    parts = []
    if history["long_term"]:
        parts.append("LONG-TERM MEMORY:\n" + history["long_term"])
    for e in history["entries"][-HISTORY_DAYS:]:
        parts.append(f"--- Log entry for {e['date_label']} ---\n{e['entry']}")
    return fill(prompts.HISTORY_BLOCK, log="\n\n".join(parts))


def restart_history(desk, history, now, history_file):
    """One-time step: restart the long-term memory in the new format.

    The old log is kept, untouched, in data/history_v1_archive.json. The Rat
    reads it once and writes a fresh long-term memory from it. Old daily
    entries and old predictions are not carried over."""
    if history.get("format") == 2 and not history.get("rebuild_pending"):
        return history   # Already done

    archive_file = history_file.parent / "history_v1_archive.json"
    if history.get("format") != 2:
        if not history.get("long_term") and not history.get("entries"):
            return {"format": 2, "long_term": "", "entries": []}   # Nothing to carry over
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
            old_memory=old.get("long_term") or "(empty)", old_events=old_events or "(none)"))
        if memory.startswith("["):
            print("  The rebuild didn't work this time. It will be tried again on the next run.")
        else:
            history["long_term"] = memory
            history.pop("rebuild_pending", None)
    else:
        history.pop("rebuild_pending", None)

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
        geologist_short=brief.get("geologist_short", "")))
    if entry.startswith("["):        # The Rat didn't reply; keep the log as it was
        return history
    history["entries"].append({"date": str(now.date()), "date_label": brief["date_label"], "entry": entry})
    history["entries"] = history["entries"][-60:]   # Two months of daily entries is plenty

    # 2. Fold today into the long-term memory (kept short so it never grows out of hand).
    memory = desk.ask("historian", fill(prompts.MEMORY_UPDATE,
        now=when, memory=history["long_term"] or "(Empty: this is the first entry.)", entry=entry))
    if not memory.startswith("["):
        history["long_term"] = memory
    return history


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


def fake_reply(agent, task):
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
        return f"(Dry run) A very short summary from the {agent} would go here: two or three sentences on the day's news."
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

    if not (discussion_due or chief_due or brief_due):
        print("No jobs due.")
        return

    # The Rat's history log: the desk's memory of past days.
    history = {"long_term": "", "entries": [], **load_json(history_file, {})}
    history = restart_history(desk, history, now, history_file)
    memory = history_text(history)

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
        brief = run_brief(desk, day, now, docs_dir)
        save_json(data_dir / "archive" / f"{now.date()}.json", brief)
        if not brief.get("quiet"):
            history = run_history(desk, day, brief, now, history)
            save_json(history_file, history)
        save_json(day_file, empty_day())
        state["brief"] = now.isoformat()
        save_json(state_file, state)
        print(f"Morning page written to {docs_dir / 'index.html'}")

    print(f"Done. {desk.calls} agent call(s) this run.")


if __name__ == "__main__":
    main()
