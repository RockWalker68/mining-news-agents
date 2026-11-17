"""
research.py: new economic-geology research papers, from OpenAlex.

OpenAlex is a free index of academic papers. Since February 2026 it needs a
free API key (sign up at openalex.org); save it as the OPENALEX_API_KEY secret
in GitHub. Its free daily allowance is far more than this needs.

Each day pipeline.py asks for papers published recently in the journals below,
and Yukon Cornelius reads a handful of the newest abstracts. For each he notes
the deposit type, commodities and geologic setting, which belts or districts in the
Lower 48 or Alaska it could apply to, what it means for exploration, and whether it
connects to anything in the recent news. The results go on the Research page.

To check the journals are coming through, run this file on its own:

    set OPENALEX_API_KEY=your-key      (Windows Command Prompt)
    python research.py
"""

import json
import os
import tempfile
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# Journal name -> ISSNs (print and online). Add or remove journals here.
JOURNALS = {
    "Economic Geology": ["0361-0128", "1554-0774"],
    "Mineralium Deposita": ["0026-4598", "1432-1866"],
    "Ore Geology Reviews": ["0169-1368"],
    "Journal of Geochemical Exploration": ["0375-6742"],
    "Geochemistry: Exploration, Environment, Analysis": ["1467-7873"],
    "Resource Geology": ["1344-1698"],
    "Natural Resources Research": ["1520-7439"],
    "SEG Discovery": ["2380-7431"],
}
LOOKBACK_DAYS = 21          # How far back to look for newly published papers
MAX_PENDING = 120           # Papers waiting for Yukon Cornelius; the oldest are dropped beyond this
KEEP_PAPERS = 400           # Papers kept on the Research page

ISSN_TO_JOURNAL = {issn: name for name, issns in JOURNALS.items() for issn in issns}


def abstract_text(inverted):
    """OpenAlex stores abstracts as {word: [positions]}. Put the words back in order."""
    if not inverted:
        return ""
    words = sorted((pos, word) for word, places in inverted.items() for pos in places)
    return " ".join(word for _, word in words)


def fetch_papers(api_key, since):
    issns = "|".join(issn for issns in JOURNALS.values() for issn in issns)
    params = {
        "filter": f"primary_location.source.issn:{issns},from_publication_date:{since}",
        "sort": "publication_date:desc", "per_page": 100,
        "select": "id,doi,title,publication_date,authorships,abstract_inverted_index,primary_location,type",
    }
    if api_key:
        params["api_key"] = api_key
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "MiningDesk research digest"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))

    papers = []
    for w in data.get("results", []):
        if w.get("type") not in (None, "article", "review", "letter"):
            continue
        source = (w.get("primary_location") or {}).get("source") or {}
        journal = next((ISSN_TO_JOURNAL[i] for i in source.get("issn") or [] if i in ISSN_TO_JOURNAL),
                       source.get("display_name", ""))
        authors = [a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")]
        papers.append({
            "id": w["id"].rsplit("/", 1)[-1], "title": w.get("title") or "(untitled)",
            "journal": journal, "date": w.get("publication_date", ""),
            "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            "link": w.get("doi") or w["id"], "abstract": abstract_text(w.get("abstract_inverted_index"))[:1800],
        })
    return papers


def collect(data_dir, api_key, report=False):
    """Add newly published papers to the waiting list in data/research.json.
    Returns (store, note)."""
    store_file = data_dir / "research.json"
    store = json.loads(store_file.read_text(encoding="utf-8")) if store_file.exists() else {"seen": [], "pending": [], "papers": []}
    since = date.today() - timedelta(days=LOOKBACK_DAYS)
    try:
        found = fetch_papers(api_key, since)
        seen = set(store["seen"])
        fresh = [p for p in found if p["id"] not in seen]
        # Papers without an abstract can't be read properly; keep them for the page, skip Yukon.
        store["pending"] = (store["pending"] + fresh)[-MAX_PENDING:]
        store["seen"] = (store["seen"] + [p["id"] for p in fresh])[-5000:]
        note = f"Research: {len(found)} recent paper(s) found, {len(fresh)} new, {len(store['pending'])} waiting"
        if report:
            by_journal = {}
            for p in found:
                by_journal[p["journal"]] = by_journal.get(p["journal"], 0) + 1
            for name in JOURNALS:
                print(f"  {name:50} {by_journal.get(name, 0):3} paper(s) since {since}")
            if not api_key:
                print("  (No OPENALEX_API_KEY set. If nothing came back, that's why.)")
    except Exception as error:
        note = f"Research: FAILED ({str(error)[:150]})"
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
    if report:
        print("  " + note)
    return store, note


def save(data_dir, store):
    store["papers"] = store["papers"][:KEEP_PAPERS]
    (data_dir / "research.json").write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    print("Checking the research journals...\n")
    store, _ = collect(Path(tempfile.mkdtemp()), os.environ.get("OPENALEX_API_KEY", ""), report=True)
    for p in store["pending"][:8]:
        print(f"\n  {p['date']}  {p['journal']}\n  {p['title'][:110]}\n  abstract: {len(p['abstract'])} characters")
