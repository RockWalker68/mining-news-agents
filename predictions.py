"""
predictions.py: the desk's prediction ledger and scorecard.

Every prediction is saved in data/predictions.json with who made it, when,
its deadline (BY date) and its confidence. Each morning Dr I Simon acts as
referee for Meldrum's and Yukon Cornelius's predictions: he notes evidence
from the day's headlines, and once a prediction is settled or its deadline
has passed, he marks it:

    came true        the news showed it happened
    did not happen   the news showed it didn't (or the deadline passed with
                     clear evidence against it)
    no evidence      the deadline passed and the desk's news never settled it

"No evidence" is kept separate and never counted as a miss.

Dr I Simon's own predictions are kept in the ledger too, with his stated
confidence, but they aren't scored: he is the referee.
"""

import re
from datetime import date

SCORED = ("strategist", "geologist")       # Whose predictions are scored
STATUSES = ("came true", "did not happen", "no evidence")

PREDICTION_LINE = re.compile(
    r"PREDICTION:\s*(?P<text>.+?)\s*\|\s*BY:\s*(?P<by>\d{4}-\d{2}-\d{2})\s*\|\s*CONFIDENCE:\s*(?P<conf>low|medium|high)",
    re.IGNORECASE)


def empty_ledger():
    return {"next_id": 1, "items": []}


def parse(text):
    """Find every prediction line in an agent's reply."""
    found = []
    for m in PREDICTION_LINE.finditer(text or ""):
        found.append({"text": m["text"].strip(" *"), "by": m["by"], "confidence": m["conf"].lower()})
    return found


def strip_predictions(text):
    """The reply with its prediction lines removed (for showing the summary on its own)."""
    lines = [l for l in (text or "").splitlines() if not PREDICTION_LINE.search(l)
             and l.strip().upper().rstrip(":") not in ("PREDICTIONS", "PREDICTION")]
    return "\n".join(lines).strip()


def _same(a, b):
    """Two predictions are the same if their wording is almost identical."""
    wa, wb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.8


def add(ledger, author, found, today):
    """Add new predictions, skipping any the same person already made. Returns the new items."""
    added = []
    for p in found:
        if any(i["author"] == author and _same(i["text"], p["text"]) for i in ledger["items"]):
            continue
        item = {"id": f"P{ledger['next_id']}", "author": author, "text": p["text"], "by": p["by"],
                "confidence": p["confidence"], "made": str(today), "status": "open", "evidence": []}
        ledger["next_id"] += 1
        ledger["items"].append(item)
        added.append(item)
    return added


def open_items(ledger, author=None):
    return [i for i in ledger["items"] if i["status"] == "open" and (author is None or i["author"] == author)]


def as_lines(items):
    return "\n".join(f"{i['id']}: {i['text']} | BY: {i['by']} | CONFIDENCE: {i['confidence']}"
                     + (f" | EVIDENCE SO FAR: {'; '.join(e['note'] for e in i['evidence'][-3:])}" if i["evidence"] else "")
                     for i in items) or "None."


def apply_referee(ledger, reply, today):
    """Read the referee's lines, e.g. 'P12 | came true | Headline 31 reports the permit was issued'."""
    by_id = {i["id"]: i for i in ledger["items"]}
    changed = 0
    for m in re.finditer(r"^\s*(P\d+)\s*\|\s*([a-z ]+?)\s*\|\s*(.+)$", reply or "", re.MULTILINE | re.IGNORECASE):
        item, verdict, note = by_id.get(m[1].upper()), m[2].strip().lower(), m[3].strip()
        if not item or item["status"] != "open":
            continue
        if verdict == "evidence":
            item["evidence"].append({"date": str(today), "note": note})
            changed += 1
        elif verdict in STATUSES:
            # A deadline that hasn't passed can only be settled by evidence, never by "no evidence".
            if verdict == "no evidence" and item["by"] >= str(today):
                continue
            item["status"], item["resolved"], item["verdict_note"] = verdict, str(today), note
            changed += 1
    return changed


def expire_quietly(ledger, today, grace_days=7):
    """Safety net: a scored prediction more than a week past its deadline that the
    referee never ruled on is closed as 'no evidence'."""
    for i in open_items(ledger):
        if i["author"] in SCORED and (date.fromisoformat(i["by"]) - today).days < -grace_days:
            i["status"], i["resolved"], i["verdict_note"] = "no evidence", str(today), "Closed automatically after the deadline."


def scorecard(ledger):
    """Per-person tallies, overall and by confidence level."""
    card = {}
    for author in SCORED:
        mine = [i for i in ledger["items"] if i["author"] == author]
        row = {"made": len(mine), "open": sum(i["status"] == "open" for i in mine)}
        for s in STATUSES:
            row[s] = sum(i["status"] == s for i in mine)
        decided = row["came true"] + row["did not happen"]
        row["hit_rate"] = round(100 * row["came true"] / decided) if decided else None
        row["by_confidence"] = {}
        for level in ("high", "medium", "low"):
            hits = sum(i["status"] == "came true" and i["confidence"] == level for i in mine)
            misses = sum(i["status"] == "did not happen" and i["confidence"] == level for i in mine)
            row["by_confidence"][level] = (hits, misses)
        card[author] = row
    return card


def memory_block(ledger, names, today):
    """A short block for the agents' memory: open predictions and recent results."""
    lines = ["OPEN PREDICTIONS (from the desk's ledger):"]
    for i in open_items(ledger)[-15:]:
        lines.append(f"- {names[i['author']]}: {i['text']} | BY: {i['by']} | CONFIDENCE: {i['confidence']} (made {i['made']})")
    if len(lines) == 1:
        lines.append("- None.")
    done = [i for i in ledger["items"] if i["status"] != "open"][-8:]
    lines.append("RECENT RESULTS:")
    for i in done:
        lines.append(f"- {names[i['author']]}: {i['text']} -> {i['status']} ({i.get('resolved', '')})")
    if not done:
        lines.append("- None yet.")
    return "\n".join(lines)
