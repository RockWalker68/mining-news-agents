"""
pipeline.py: the main program. Run it once an hour.

Every run, it fetches new headlines and saves them in an "inbox." Then it
checks the clock (Pacific time) and does whichever jobs are due:

  Every 6 hours  (4am, 10am, 4pm, 10pm):
      Meldrum (Strategist) and Yukon Cornelius (Geologist) each analyze the
      inbox, reply to each other, and a note-taker summarizes their findings.

  Every 12 hours (4am, 4pm):
      Dr I Simon (Chief Analyst) reviews their work, asks both of them
      questions, reads their answers, and writes big takeaways plus
      creative angles.

  Every morning  (4am):
      Meldrum and Yukon Cornelius each write a very short summary, then
      The Rat (poet) reads all three agents' work and writes the poem.
      The morning page is built at docs/index.html. Finally, The Rat (as
      historian) writes the day's log entry and updates the desk's
      long-term memory in data/history.json. The other three read that
      log before every analysis, so they remember past events and their
      own earlier conclusions and predictions.

  (Names live in prompts.py. Change them there.)

The 4am start leaves room for scheduling delays so the brief is ready by 6am.

How to run it:
    python pipeline.py                    normal hourly run
    python pipeline.py --now              do every job right now, ignoring the clock
    python pipeline.py --now --dry-run    same, but with fake agent replies and
                                          sample headlines (free; no API key needed).
                                          Writes to the preview/ folder instead.
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
from page import build_page

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("America/Los_Angeles")   # Handles daylight saving automatically

DISCUSSION_HOURS = [4, 10, 16, 22]   # Strategist + Geologist talk (every 6 hours)
CHIEF_HOURS = [4, 16]                # Chief Analyst session (every 12 hours)
BRIEF_HOURS = [4]                    # Morning brief and page

MODEL = "claude-haiku-5-5"           # Model for every agent
CHIEF_MODEL = MODEL                  # Change to "claude-sonnet-5-5" for a stronger Chief

# Length limit for each reply, including the model's thinking. It's a ceiling,
# not a target: you only pay for what's actually written. Too low = blank replies.
MAX_TOKENS = 8000

HISTORY_DAYS = 7                     # Days of The Rat's daily log entries the agents read

MAX_ARTICLES = 40                   # Most headlines handed to agents in one discussion
POET_TANGENT_CHANCE = 1 / 3          # How often the poet gets the astrology/lizard/fish nudge


# ---------------------------------------------------------------------------
# Small helpers for saving and loading data
# ---------------------------------------------------------------------------

def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def stamp(now):
    """A friendly time string for prompts and the page, e.g. 'Fri Oct 16, 4:05 AM PDT'."""
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


DISPLAY_NAMES = {
    "strategist": prompts.STRATEGIST_NAME, "geologist": prompts.GEOLOGIST_NAME,
    "chief": prompts.CHIEF_NAME, "poet": prompts.POET_NAME, "scribe": "the note-taker",
    "historian": f"{prompts.POET_NAME} (historian)",
}


def format_articles(articles):
    lines = []
    for i, a in enumerate(articles, start=1):
        source = f" ({a['source']})" if a.get("source") else ""
        lines.append(f"{i}. {a['title']}{source}")
    return "\n".join(lines)


def section(text, label, all_labels):
    """Pull out the text after a label like 'BIG TAKEAWAYS:' up to the next label."""
    start = text.find(label)
    if start == -1:
        return ""
    start += len(label)
    ends = [text.find(other, start) for other in all_labels if other != label]
    ends = [e for e in ends if e != -1]
    return text[start:min(ends) if ends else len(text)].strip()


def bullets(text):
    """Turn '- item' lines into a list of strings."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ", "• ")):
            items.append(line[2:].strip())
    return items


# ---------------------------------------------------------------------------
# Job 1: Strategist and Geologist discuss (every 6 hours)
# ---------------------------------------------------------------------------

def run_discussion(desk, articles, now, history):
    print(f"Discussion: {len(articles)} new article(s)")
    news = format_articles(articles[-MAX_ARTICLES:])
    when = stamp(now)

    s1 = desk.ask("strategist", fill(prompts.FIRST_TAKE, now=when, news=news, history=history))
    g1 = desk.ask("geologist", fill(prompts.FIRST_TAKE, now=when, news=news, history=history))

    s2 = desk.ask("strategist", fill(prompts.REPLY, now=when, news=news, own=s1, theirs=g1,
        other=prompts.GEOLOGIST_NAME, other_upper=prompts.GEOLOGIST_NAME.upper()))
    g2 = desk.ask("geologist", fill(prompts.REPLY, now=when, news=news, own=g1, theirs=s1,
        other=prompts.STRATEGIST_NAME, other_upper=prompts.STRATEGIST_NAME.upper()))

    summary = desk.ask("scribe", fill(prompts.DISCUSSION_SUMMARY, s1=s1, g1=g1, s2=s2, g2=g2))

    return {
        "time": now.isoformat(),
        "article_count": len(articles),
        "strategist": s1, "geologist": g1,
        "strategist_reply": s2, "geologist_reply": g2,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Job 2: Chief Analyst talks with both (every 12 hours)
# ---------------------------------------------------------------------------

def run_chief(desk, day, now, history):
    print(f"{prompts.CHIEF_NAME} session")
    S, G = prompts.STRATEGIST_NAME.upper(), prompts.GEOLOGIST_NAME.upper()
    when = stamp(now)

    # Give the Chief the discussions since its last session.
    last_chief = day["chief_sessions"][-1]["time"] if day["chief_sessions"] else ""
    recent = [d for d in day["discussions"] if d["time"] > last_chief] or day["discussions"][-1:]

    material = "\n\n".join(
        f"--- Discussion at {d['time'][:16]} ---\n"
        f"{S} (Strategist):\n{d['strategist']}\n\n{G} (Geologist):\n{d['geologist']}\n\n"
        f"{S}'S REPLY:\n{d['strategist_reply']}\n\n{G}'S REPLY:\n{d['geologist_reply']}"
        for d in recent
    )
    summaries = "\n\n".join(d["summary"] for d in recent)

    previous = ""
    if day["chief_sessions"]:
        previous = ("\nFor continuity, here are your conclusions from your previous session:\n"
                    + day["chief_sessions"][-1]["conclusion"] + "\n")

    opening = desk.ask("chief", fill(prompts.CHIEF_OPENING,
        now=when, material=material, previous=previous, history=history), model=CHIEF_MODEL)

    s_answer = desk.ask("strategist", fill(prompts.ANSWER_CHIEF,
        now=when, summaries=summaries, opening=opening, role="STRATEGIST"))
    g_answer = desk.ask("geologist", fill(prompts.ANSWER_CHIEF,
        now=when, summaries=summaries, opening=opening, role="GEOLOGIST"))

    conclusion = desk.ask("chief", fill(prompts.CHIEF_CONCLUSION,
        now=when, material=material, opening=opening, s_answer=s_answer, g_answer=g_answer),
        model=CHIEF_MODEL)

    labels = ["BIG TAKEAWAYS:", "CREATIVE ANGLES:", "PREDICTIONS:"]
    return {
        "time": now.isoformat(),
        "opening": opening,
        "strategist_answer": s_answer,
        "geologist_answer": g_answer,
        "conclusion": conclusion,
        "takeaways": bullets(section(conclusion, labels[0], labels)),
        "creative": bullets(section(conclusion, labels[1], labels)),
    }


# ---------------------------------------------------------------------------
# Job 3: the morning brief (every morning)
# ---------------------------------------------------------------------------

def run_brief(desk, day, now, docs_dir):
    print("Morning brief")
    when = stamp(now)
    brief = {"time": now.isoformat(), "date_label": f"{now:%A, %B} {now.day}",
             "article_count": len(day["articles"])}

    if not day["discussions"]:
        brief["quiet"] = True
    else:
        summaries = "\n\n".join(d["summary"] for d in day["discussions"])
        chief = day["chief_sessions"][-1] if day["chief_sessions"] else None

        # Meldrum and Yukon Cornelius each write a very short summary first...
        brief["strategist_short"] = desk.ask("strategist", fill(prompts.SHORT_SUMMARY,
            now=when, summaries=summaries))
        brief["geologist_short"] = desk.ask("geologist", fill(prompts.SHORT_SUMMARY,
            now=when, summaries=summaries))

        # ...then The Rat reads all three agents' work and writes the poem.
        tangent = random.random() < POET_TANGENT_CHANCE
        brief["poem"] = desk.ask("poet", fill(prompts.POEM,
            strategist_short=brief["strategist_short"],
            geologist_short=brief["geologist_short"],
            chief_conclusion=chief["conclusion"] if chief else "(No review today.)",
            tangent=prompts.POEM_TANGENT if tangent else ""))

        brief["takeaways"] = chief["takeaways"] if chief else []
        brief["creative"] = chief["creative"] if chief else []
        brief["chief_conclusion"] = chief["conclusion"] if chief else ""

    brief["discussions"] = day["discussions"]
    brief["chief_sessions"] = day["chief_sessions"]
    brief["articles"] = day["articles"]

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


def run_history(desk, day, brief, now, history):
    print(f"{prompts.POET_NAME} updates the history log")
    when = stamp(now)

    # 1. Today's log entry, from the day's headlines and the desk's work.
    entry = desk.ask("historian", fill(prompts.HISTORY_ENTRY,
        now=when,
        headlines=format_articles(day["articles"][-100:]),
        summaries="\n\n".join(d["summary"] for d in day["discussions"]),
        chief_conclusions="\n\n".join(c["conclusion"] for c in day["chief_sessions"]) or "(No review today.)",
        strategist_short=brief.get("strategist_short", ""),
        geologist_short=brief.get("geologist_short", "")))
    if entry.startswith("["):        # The Rat didn't reply; keep the log as it was
        return history
    history["entries"].append({"date": str(now.date()), "date_label": brief["date_label"], "entry": entry})

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
    {"title": "Copper climbs as Chilean mine strike enters second week", "source": "Sample News", "link": "#"},
    {"title": "Junior explorer reports 42m of 'bonanza grade' gold in first drill hole", "source": "Sample Wire", "link": "#"},
    {"title": "Indonesia weighs new export levy on nickel products", "source": "Sample Times", "link": "#"},
    {"title": "Lithium prices steady as battery makers rebuild inventories", "source": "Sample Daily", "link": "#"},
]


def fake_reply(agent, task):
    if agent == "historian":
        return ("EVENTS:\n- (Dry run) The Rat's log of the day's events would go here.\n"
                "DESK VIEWS:\n- (Dry run) Each person's main conclusions.\n"
                "PREDICTIONS MADE:\n(Dry run) Predictions, copied exactly.\n"
                "THREADS TO WATCH:\n- (Dry run) Stories to follow up on.")
    if agent == "poet":
        return "Drill core, long and grey,\nwhispers copper to the dawn.\nThe lizard knows why."
    if "BIG TAKEAWAYS:" in task:
        return ("BIG TAKEAWAYS:\n- (Dry run) Supply disruptions are doing more for copper than demand is.\n"
                "- (Dry run) One spectacular drill hole is a story, not yet a deposit.\n"
                "- (Dry run) Resource nationalism keeps rising up the risk list.\n"
                "CREATIVE ANGLES:\n- (Dry run) Watch smelter treatment charges as an early signal. (speculation level: medium)\n"
                "PREDICTIONS:\nPREDICTION: (dry run example) | BY: 2027-01-31 | CONFIDENCE: low")
    if "60 words" in task:
        return f"(Dry run) A very short summary from the {agent} would go here: two or three sentences on the day's news."
    return f"(Dry run) The {agent}'s reply would go here.\n- A point\n- Another point"


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="run every job now, ignoring the schedule")
    parser.add_argument("--dry-run", action="store_true", help="fake replies, sample news, no API cost")
    args = parser.parse_args()

    # Dry runs use a separate folder so they never mix with your real data.
    data_dir = Path("preview/data") if args.dry_run else Path("data")
    docs_dir = Path("preview") if args.dry_run else Path("docs")

    state_file = data_dir / "state.json"      # When each job last ran
    inbox_file = data_dir / "inbox.json"      # Articles waiting for the next discussion
    day_file = data_dir / "day.json"          # Everything since the last morning brief

    now = datetime.now(TIMEZONE)
    state = load_json(state_file, {})
    inbox = load_json(inbox_file, [])
    day = load_json(day_file, {"articles": [], "discussions": [], "chief_sessions": []})
    desk = Desk(dry_run=args.dry_run)

    # The Rat's history log: the desk's memory of past days.
    history_file = data_dir / "history.json"
    history = load_json(history_file, {"long_term": "", "entries": []})
    memory = history_text(history)

    # Every hour: fetch news into the inbox.
    new = fetch_news.fetch_new_articles(data_dir)
    if args.dry_run and not new and not inbox:
        new = SAMPLE_ARTICLES
    inbox += new
    save_json(inbox_file, inbox)
    print(f"{stamp(now)}: {len(new)} new article(s), {len(inbox)} waiting in the inbox")

    # Every 6 hours: Strategist + Geologist discussion.
    if args.now or is_due(state, "discussion", now, DISCUSSION_HOURS):
        if inbox:
            day["discussions"].append(run_discussion(desk, inbox, now, memory))
            day["articles"] += inbox
            inbox = []
            save_json(inbox_file, inbox)
            save_json(day_file, day)
        else:
            print("Discussion: no new articles, skipping")
        state["discussion"] = now.isoformat()
        save_json(state_file, state)

    # Every 12 hours: Chief Analyst session.
    if args.now or is_due(state, "chief", now, CHIEF_HOURS):
        last_chief = day["chief_sessions"][-1]["time"] if day["chief_sessions"] else ""
        if any(d["time"] > last_chief for d in day["discussions"]):
            day["chief_sessions"].append(run_chief(desk, day, now, memory))
            save_json(day_file, day)
        else:
            print("Chief Analyst: nothing new to review, skipping")
        state["chief"] = now.isoformat()
        save_json(state_file, state)

    # Every morning: poem, short summaries, and the page. Then start a new day.
    if args.now or is_due(state, "brief", now, BRIEF_HOURS):
        brief = run_brief(desk, day, now, docs_dir)
        save_json(data_dir / "archive" / f"{now.date()}.json", brief)
        if not brief.get("quiet"):
            history = run_history(desk, day, brief, now, history)
            save_json(history_file, history)
        save_json(day_file, {"articles": [], "discussions": [], "chief_sessions": []})
        state["brief"] = now.isoformat()
        save_json(state_file, state)
        print(f"Morning page written to {docs_dir / 'index.html'}")

    print(f"Done. {desk.calls} agent call(s) this run.")


if __name__ == "__main__":
    main()
