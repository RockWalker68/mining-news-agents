"""
gov_watch.py: watches government records for the desk.

  Claim packages     New lode claims on federal land (BLM, all states including
                     Alaska) and on Alaska state land (Alaska DNR). Claims filed
                     together are grouped into packages; packages of more than
                     100 claims are flagged.
  Exploration notices  New BLM notices for small exploration disturbances
                     (under 5 acres).
  Environmental reviews  Federal mining reviews nationwide (Federal Register),
                     plus state reviews and comment periods in Washington,
                     Oregon, Idaho and Montana.
  SEC 8-K filings    Major-event filings by US-listed mining companies,
                     filtered to the filing sections that matter.
  MSHA reports       Fatal and serious accidents and imminent-danger orders,
                     from MSHA's weekly data files.

pipeline.py calls these once a day (MSHA once a week). Each function remembers
what it has already reported in the data/gov folder, so only new records come
back.

To check which sources are working, run this file on its own:

    python gov_watch.py

It prints what each source returns and doesn't touch your data folder. The SEC
check only runs if you set SEC_CONTACT_EMAIL first (the SEC requires a contact
email on every request):

    set SEC_CONTACT_EMAIL=you@example.com      (Windows Command Prompt)
"""

import csv
import io
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import feedparser

socket.setdefaulttimeout(60)

BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------

PACKAGE_SIZE = 100          # A package needs MORE than this many claims to be flagged
PACKAGE_WINDOW_DAYS = 30    # Claims filed within this many days of each other can form one package

# BLM's national claims layers (the first address that answers is used).
BLM_CLAIMS = [
    "https://nlsdbpubgis.blm.gov/arcgishub/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0",
    "https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0",
]
BLM_NOTICES = [
    "https://nlsdbpubgis.blm.gov/arcgishub/rest/services/HUB/BLM_Natl_MLRS_Locatable_Notices/FeatureServer/0",
    "https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Locatable_Notices/FeatureServer/0",
]
ALASKA_CLAIMS = [
    "https://arcgis.dnr.alaska.gov/arcgis/rest/services/OpenData/NaturalResource_StateMiningClaim/MapServer/0",
]

# Federal Register searches for mining environmental reviews (any agency, any state).
FEDERAL_REGISTER_SEARCHES = [
    'mine "environmental impact statement"',
    'mining "environmental impact statement"',
    'mine "environmental assessment"',
    'mining "plan of operations"',
    'mine "record of decision"',
    'exploration "plan of operations"',
]
MINING_WORDS = re.compile(
    r"\bmine\b|\bmines\b|mining|mineral|\blode\b|placer|exploration|drilling|\bore\b|tailings|heap leach"
    r"|cyanid|gold|silver|copper|lithium|uranium|antimony|tungsten|molybdenum|zinc|nickel|cobalt|rare earth|phosphate",
    re.IGNORECASE)
REVIEW_WORDS = re.compile(
    r"environmental impact statement|environmental assessment|record of decision|plan of operations"
    r"|scoping|notice of intent|\bNEPA\b|decision record|finding of no significant impact|public comment",
    re.IGNORECASE)
# Words that mark a state record as mining (stricter than MINING_WORDS, which also
# matches general planning documents that mention "mineral resource lands").
MINING_TITLE = re.compile(r"\bmines?\b|mining|mineral exploration|exploration drilling|metallic|tailings|heap leach|ore\b",
                          re.IGNORECASE)
NOT_HARDROCK = re.compile(r"gravel|\bsand\b|aggregate|borrow pit|crushed rock|rock quarry|cinder", re.IGNORECASE)

# State pages watched for new mining reviews and comment periods.
STATE_PAGES = [
    # (state, name, url, which links count)
    ("MT", "Montana DEQ news", "https://news.mt.gov/Department-of-Environmental-Quality/index",
     r"/Department-of-Environmental-Quality/"),
    ("MT", "Montana DEQ hard rock comment", "https://deq.mt.gov/public/hardrock-public", r"publiccomment|/News/"),
    ("MT", "Montana DEQ MEPA comment", "https://deq.mt.gov/public/mepa", r"publiccomment|/News/"),
    ("OR", "Oregon DOGAMI chemical process mining", "https://www.oregon.gov/dogami/mlrr/Pages/chemicalprocessmining-noi.aspx",
     r"chemicalprocess_|/mlrr/"),
    ("OR", "Oregon DOGAMI permitting news", "https://www.oregon.gov/dogami/mlrr/Pages/depsnews.aspx", r"/dogami/"),
]
IDAHO_DEQ_FEED = "https://www.deq.idaho.gov/category/public-comment/feed/"
WASHINGTON_SEPA = "https://data.wa.gov/resource/mmcb-z6jf.json"

# SEC: companies coded as mining, and the 8-K items worth reading.
MINING_SIC = {"1000", "1040", "1090", "1220", "1221", "1222", "1400", "6795"}
KEEP_ITEMS = {
    "1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy",
    "1.04": "Mine safety: shutdown or pattern of violations", "2.01": "Acquisition or disposal of assets",
    "2.05": "Closure or layoff costs", "2.06": "Write-down (impairment)", "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}

# MSHA weekly data files.
MSHA = "https://arlweb.msha.gov/OpenGovernmentData/DataSets/"
MLRS_LOOKUP = "https://reports.blm.gov/report/mlrs/106/Mining-Claims-Serial-Number-Index/"
MSHA_LOOKUP = "https://www.msha.gov/data-and-reports/mine-data-retrieval-system"   # Search by mine ID
MSHA_LOOKBACK_DAYS = 45     # MSHA records arrive late, so look back this far for new ones


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def fetch(url, params=None, headers=None, timeout=60):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url, params=None, headers=None):
    return json.loads(fetch(url, params, headers).decode("utf-8", "replace"))


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def ms_to_date(value):
    """ArcGIS dates are milliseconds since 1970. Returns 'YYYY-MM-DD' or ''."""
    if value in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)[:10]


def centroid(geometry):
    """A rough middle point of an ArcGIS shape, as (lat, lon)."""
    points = []
    for key in ("rings", "paths"):
        for part in (geometry or {}).get(key, []):
            points += part
    if not points and geometry and "x" in geometry:
        points = [[geometry["x"], geometry["y"]]]
    if not points:
        return None
    return (round(sum(p[1] for p in points) / len(points), 4), round(sum(p[0] for p in points) / len(points), 4))


# Towns of 1,000+ people (GeoNames, via us_towns.csv) for "nearest town" notes.
MAJOR_TOWN = 5000       # A "major" town has at least this many people
_TOWNS = None


def _towns():
    global _TOWNS
    if _TOWNS is None:
        path = Path(__file__).with_name("us_towns.csv")
        _TOWNS = []
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    _TOWNS.append((row["name"], row["state"], float(row["lat"]), float(row["lon"]), int(row["population"] or 0)))
    return _TOWNS


def _miles_and_direction(lat1, lon1, lat2, lon2):
    """Distance in miles from point 2 to point 1, and the compass direction of point 1 from point 2."""
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p1 - p2, math.radians(lon1 - lon2)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    miles = 3958.8 * 2 * math.asin(math.sqrt(a))
    bearing = math.degrees(math.atan2(math.sin(dlon) * math.cos(p1),
                                      math.cos(p2) * math.sin(p1) - math.sin(p2) * math.cos(p1) * math.cos(dlon))) % 360
    return miles, ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((bearing + 22.5) // 45) % 8]


def nearest_town(lat, lon):
    """E.g. '18 mi NE of Battle Mountain, NV; nearest major town Elko, NV (70 mi)'."""
    if lat is None or lon is None or not _towns():
        return ""
    def closest(min_pop):
        best = None
        for name, state, tlat, tlon, pop in _towns():
            if pop < min_pop or abs(tlat - lat) > 6:
                continue
            miles, way = _miles_and_direction(lat, lon, tlat, tlon)
            if best is None or miles < best[0]:
                best = (miles, way, f"{name}, {state}", pop)
        return best
    near, major = closest(1000), closest(MAJOR_TOWN)
    if not near:
        return ""
    text = f"{near[0]:.0f} mi {near[1]} of {near[2]}" if near[0] >= 1 else f"at {near[2]}"
    if major and major[2] != near[2]:
        text += f"; nearest major town {major[2]} ({major[0]:.0f} mi)"
    return text


def map_link(lat, lon):
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


class LinkReader(HTMLParser):
    """Collects (link, text) pairs from a web page."""
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


# ---------------------------------------------------------------------------
# ArcGIS map services (BLM and Alaska DNR)
# ---------------------------------------------------------------------------

def arcgis_query(urls, where, fields="*", order=None, geometry=False, max_pages=25, stop=None):
    """Run a query against the first map service that answers. Returns
    (features, service_url). `stop(attributes)` ends paging early once it
    returns True (used when results are sorted newest first)."""
    last_error = None
    for url in urls:
        features, offset = [], 0
        try:
            for _ in range(max_pages):
                params = {"where": where, "outFields": fields, "f": "json",
                          "returnGeometry": "true" if geometry else "false",
                          "resultOffset": offset, "resultRecordCount": 2000}
                if geometry:
                    params.update(outSR=4326, geometryPrecision=4)
                if order:
                    params["orderByFields"] = order
                data = fetch_json(url + "/query", params)
                if "error" in data:
                    raise RuntimeError(data["error"].get("message", "query error"))
                batch = data.get("features", [])
                features += batch
                if not batch or (stop and stop(batch[-1]["attributes"])) or not data.get("exceededTransferLimit"):
                    break
                offset += len(batch)
            return features, url
        except Exception as error:   # Try the next address
            last_error = error
    raise RuntimeError(f"no claims service answered ({last_error})")


def blm_since(urls, since, fields="*"):
    """BLM records created on or after `since` (a date), newest first."""
    try:
        features, url = arcgis_query(urls, f"Created >= DATE '{since}'", fields, order="Created DESC", geometry=True)
    except RuntimeError:
        # Some servers reject date queries: read newest-first and stop at `since`.
        cutoff = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000
        features, url = arcgis_query(urls, "1=1", fields, order="Created DESC", geometry=True,
                                     stop=lambda a: (a.get("Created") or 0) < cutoff)
    return features, url


CLAIM_TYPES = [("lode", "lode"), ("placer", "placer"), ("mill", "mill site"), ("tunnel", "tunnel site")]
# MLRS case type numbers. If BLM publishes other codes, the claim type is read from the text fields instead.
CLAIM_CODES = {"384101": "lode", "384201": "placer", "384301": "mill site", "384401": "tunnel site"}


def claim_type(attrs):
    code = str(attrs.get("CSE_TYPE_NR") or "").strip()
    if code in CLAIM_CODES:
        return CLAIM_CODES[code]
    text = " ".join(str(attrs.get(k) or "") for k in ("BLM_PROD", "CSE_META", "CSE_TYPE_NR")).lower()
    for word, label in CLAIM_TYPES:
        if word in text:
            return label
    return "unknown"


def base_name(name):
    """'BIG MOUNTAIN 117' and 'BIG MOUNTAIN #3' both become 'BIG MOUNTAIN'."""
    name = re.sub(r"[\s#\-_.]*\d+[A-Z]?$", "", (name or "").upper().strip())
    return re.sub(r"\s+", " ", name).strip() or "(unnamed)"


# ---------------------------------------------------------------------------
# 1. Claim packages
# ---------------------------------------------------------------------------

def watch_claims(data_dir, report=False):
    """Returns {'new': packages first seen today, 'active': all packages in the
    window, 'notes': status lines}."""
    store_file = data_dir / "gov" / "claims.json"
    store = load(store_file, {"blm": {}, "alaska": {}, "alerted": [], "last_blm": None, "last_alaska": None})
    today = date.today()
    window_start = today - timedelta(days=PACKAGE_WINDOW_DAYS)
    notes = []

    # Federal claims (BLM), every state including Alaska.
    since = max(window_start, date.fromisoformat(store["last_blm"]) - timedelta(days=3)) if store["last_blm"] else window_start
    try:
        features, _ = blm_since(BLM_CLAIMS, since)
        types = {}
        for f in features:
            a = f["attributes"]
            serial = a.get("CSE_NR") or a.get("ID")
            if not serial:
                continue
            kind = claim_type(a)
            types[kind] = types.get(kind, 0) + 1
            spot = centroid(f.get("geometry"))
            store["blm"][serial] = {
                "name": a.get("CSE_NAME") or "", "state": serial[:2].upper(), "type": kind,
                "created": ms_to_date(a.get("Created")), "acres": a.get("RCRD_ACRS") or 0,
                "status": a.get("CSE_DISP") or "", "lat": spot[0] if spot else None, "lon": spot[1] if spot else None}
        store["last_blm"] = str(today)
        notes.append(f"BLM federal claims: {len(features)} created since {since} (types: {types or 'none'})")
    except Exception as error:
        notes.append(f"BLM federal claims: FAILED ({str(error)[:120]})")

    # Alaska state claims (DNR).
    try:
        ak_since = since if store["last_alaska"] else window_start
        date_fields = ("NTPSTDT", "DATE_ALF")
        where = " OR ".join(f"{f} >= DATE '{ak_since}'" for f in date_fields)
        try:
            features, _ = arcgis_query(ALASKA_CLAIMS, where, geometry=True)
        except RuntimeError:
            features, _ = arcgis_query(ALASKA_CLAIMS, "1=1", order="OBJECTID DESC", geometry=True, max_pages=3)
        for f in features:
            a = f["attributes"]
            key = str(a.get("FILENUMBER") or a.get("CASE_ID") or a.get("OBJECTID"))
            spot = centroid(f.get("geometry"))
            dated = max(ms_to_date(a.get(k)) for k in date_fields)
            store["alaska"][key] = {
                "name": a.get("CLAIM_NAME") or "", "claimant": (a.get("CSTMRNM") or "").strip(),
                "state": "AK", "type": (a.get("CSTYPDSCRP") or "state claim").lower(),
                "created": dated, "acres": a.get("TOT_ACRES") or 0,   # Undated records never form packages
                "status": a.get("CASESTATUS") or a.get("CSSTTSDSCR") or "",
                "lat": spot[0] if spot else None, "lon": spot[1] if spot else None,
                "link": a.get("INFO_LINK") or ""}
        store["last_alaska"] = str(today)
        notes.append(f"Alaska state claims: {len(features)} record(s) read")
    except Exception as error:
        notes.append(f"Alaska state claims: FAILED ({str(error)[:120]})")

    # Forget claims older than the window (plus a margin).
    cutoff = str(today - timedelta(days=PACKAGE_WINDOW_DAYS + 15))
    for source in ("blm", "alaska"):
        store[source] = {k: v for k, v in store[source].items() if (v.get("created") or "0000") >= cutoff}

    # Group claims into packages.
    packages = []
    groups = {}
    for serial, c in store["blm"].items():
        if c["type"] in ("placer", "mill site", "tunnel site") or (c.get("created") or "") < str(window_start):
            continue
        groups.setdefault(("BLM", c["state"], base_name(c["name"])), []).append((serial, c))
    for key, c in store["alaska"].items():
        if (c.get("created") or "") < str(window_start):
            continue
        owner = c["claimant"].upper() or base_name(c["name"])
        groups.setdefault(("Alaska DNR", "AK", owner), []).append((key, c))

    for (source, state, name), members in groups.items():
        if len(members) <= PACKAGE_SIZE:
            continue
        spots = [(c["lat"], c["lon"]) for _, c in members if c.get("lat") is not None]
        lat = round(sum(s[0] for s in spots) / len(spots), 3) if spots else None
        lon = round(sum(s[1] for s in spots) / len(spots), 3) if spots else None
        dates = sorted(c["created"] for _, c in members if c.get("created"))
        serials = sorted(s for s, _ in members)
        first = members[0][1]
        packages.append({
            "key": f"{source}|{state}|{name}|{dates[0] if dates else ''}",
            "source": "BLM (federal land)" if source == "BLM" else "Alaska DNR (state land)",
            "state": state, "name": name.title() if source == "BLM" else base_name(first["name"] or name).title(),
            "claimant": name.title() if source != "BLM" else "",
            "count": len(members), "acres": round(sum(float(c.get("acres") or 0) for _, c in members)),
            "first": dates[0] if dates else "", "last": dates[-1] if dates else "",
            "lat": lat, "lon": lon, "map": map_link(lat, lon) if lat is not None else "",
            "serials": f"{serials[0]} to {serials[-1]}",
            "town": nearest_town(lat, lon),
            # BLM's map data doesn't publish claimant names. They're in MLRS's
            # Serial Number Index report: paste any serial from the package there.
            "lookup": MLRS_LOOKUP if source == "BLM" else first.get("link", ""),
            "type": first["type"]})

    new = [p for p in packages if p["key"] not in store["alerted"]]
    store["alerted"] = (store["alerted"] + [p["key"] for p in new])[-500:]
    save(store_file, store)
    if report:
        for line in notes:
            print("  " + line)
        print(f"  Packages over {PACKAGE_SIZE} claims in the last {PACKAGE_WINDOW_DAYS} days: {len(packages)} ({len(new)} new)")
    packages.sort(key=lambda p: -p["count"])
    return {"new": new, "active": packages, "notes": notes}


# ---------------------------------------------------------------------------
# 2. Exploration notices (BLM, under 5 acres)
# ---------------------------------------------------------------------------

def watch_notices(data_dir, report=False):
    store_file = data_dir / "gov" / "notices.json"
    store = load(store_file, {"seen": [], "last": None})
    today = date.today()
    since = date.fromisoformat(store["last"]) - timedelta(days=3) if store["last"] else today - timedelta(days=14)
    new = []
    try:
        features, _ = blm_since(BLM_NOTICES, since)
        seen = set(store["seen"])
        for f in features:
            a = f["attributes"]
            serial = a.get("CSE_NR") or a.get("ID") or str(a.get("OBJECTID"))
            if serial in seen:
                continue
            seen.add(serial)
            spot = centroid(f.get("geometry"))
            new.append({"serial": serial, "name": (a.get("CSE_NAME") or "").title(), "state": serial[:2].upper(),
                        "town": nearest_town(*spot) if spot else "",
                        "created": ms_to_date(a.get("Created")), "status": a.get("CSE_DISP") or "",
                        "acres": a.get("RCRD_ACRS") or "",
                        "map": map_link(*spot) if spot else ""})
            store["seen"].append(serial)
        store["seen"] = store["seen"][-5000:]
        store["last"] = str(today)
        save(store_file, store)
        note = f"BLM exploration notices: {len(features)} since {since}, {len(new)} new"
    except Exception as error:
        note = f"BLM exploration notices: FAILED ({str(error)[:120]})"
    if report:
        print("  " + note)
    return {"new": new, "notes": [note]}


# ---------------------------------------------------------------------------
# 3. Environmental reviews
# ---------------------------------------------------------------------------

US_STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
             "Florida": "FL", "Georgia": "GA", "Idaho": "ID", "Michigan": "MI", "Minnesota": "MN", "Missouri": "MO",
             "Montana": "MT", "Nevada": "NV", "New Mexico": "NM", "North Carolina": "NC", "Oregon": "OR",
             "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
             "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
             "Kentucky": "KY", "Pennsylvania": "PA", "Ohio": "OH", "Illinois": "IL", "Indiana": "IN", "Maine": "ME"}


def guess_state(text):
    for name, code in US_STATES.items():
        if re.search(rf"\b{name}\b", text or ""):
            return code
    return ""


def federal_register(since):
    found = {}
    for term in FEDERAL_REGISTER_SEARCHES:
        params = {"conditions[term]": term, "conditions[publication_date][gte]": str(since),
                  "conditions[type][]": "NOTICE",
                  "order": "newest", "per_page": 50,
                  "fields[]": ["title", "abstract", "html_url", "publication_date", "agencies", "document_number", "type"]}
        data = fetch_json("https://www.federalregister.gov/api/v1/documents.json", params)
        for d in data.get("results", []):
            text = f"{d.get('title', '')} {d.get('abstract') or ''}"
            if not MINING_WORDS.search(d.get("title", "")) or NOT_HARDROCK.search(d.get("title", "")):
                continue
            if not REVIEW_WORDS.search(text):
                continue   # A safety rule or other notice, not an environmental review
            agencies = ", ".join(a.get("name", "") for a in d.get("agencies", []) if a.get("name"))
            if "Mine Safety and Health" in agencies:
                continue
            found[d["document_number"]] = {
                "id": "FR-" + d["document_number"], "title": d["title"], "link": d["html_url"],
                "date": d["publication_date"], "source": f"Federal Register · {agencies}" if agencies else "Federal Register",
                "state": guess_state(text), "summary": (d.get("abstract") or "")[:400]}
        time.sleep(0.5)
    return list(found.values())


def washington_sepa(since):
    """Washington's statewide SEPA register (Department of Ecology), via data.wa.gov."""
    try:
        rows = fetch_json(WASHINGTON_SEPA, {"$limit": 1000, "$order": ":created_at DESC",
                                            "$where": f":created_at >= '{since}T00:00:00'"})
        dated_query = True
    except urllib.error.HTTPError:
        rows = fetch_json(WASHINGTON_SEPA, {"$limit": 2000})   # Plain read; dates are checked below
        dated_query = False
    items = []
    for row in rows:
        text = " ".join(str(v) for v in row.values() if isinstance(v, (str, int, float)))
        if not MINING_WORDS.search(text) or NOT_HARDROCK.search(text):
            continue
        pick = lambda *words: next((str(v) for k, v in row.items() for w in words if w in k.lower() and v), "")
        title = pick("project", "proposal", "name", "title") or text[:120]
        if not MINING_TITLE.search(title + " " + pick("description", "desc")[:300]) or NOT_HARDROCK.search(title):
            continue
        link = row.get("link") or row.get("url") or {}
        link = link.get("url", "") if isinstance(link, dict) else str(link)
        if not dated_query and pick("date")[:10] < str(since):
            continue
        items.append({"id": "WA-" + str(row.get(":id") or row.get("sepa_number") or hash(text)),
                      "title": title[:200], "link": link or "https://ecology.wa.gov/regulations-permits/sepa/environmental-review/sepa-register",
                      "date": pick("date")[:10], "source": "Washington SEPA register",
                      "state": "WA", "summary": pick("description", "desc")[:400]})
    return items


def idaho_deq():
    feed = feedparser.parse(IDAHO_DEQ_FEED, agent=BROWSER_AGENT)
    if not feed.entries:   # No feed: read the listing page instead
        return state_page("ID", "Idaho DEQ public comment", IDAHO_DEQ_FEED.replace("feed/", ""), r"deq\.idaho\.gov/deq-")
    items = []
    for e in feed.entries:
        if not MINING_WORDS.search(e.get("title", "")):
            continue
        items.append({"id": "ID-" + e.get("link", e.get("title", "")), "title": e.get("title", ""),
                      "link": e.get("link", ""), "date": (e.get("published") or "")[:16],
                      "source": "Idaho DEQ public comment", "state": "ID", "summary": ""})
    return items


def state_page(state, name, url, pattern):
    reader = LinkReader()
    reader.feed(fetch(url).decode("utf-8", "replace"))
    items = []
    for href, text in reader.links:
        if not text or len(text) < 12 or not re.search(pattern, href):
            continue
        if not MINING_WORDS.search(text + " " + href) or NOT_HARDROCK.search(text):
            continue
        link = urllib.parse.urljoin(url, href)
        items.append({"id": f"{state}-{link}", "title": text, "link": link, "date": "",
                      "source": name, "state": state, "summary": ""})
    return items


def watch_reviews(data_dir, report=False):
    store_file = data_dir / "gov" / "reviews.json"
    store = load(store_file, {"seen": [], "baselined": [], "last": None})
    today = date.today()
    since = date.fromisoformat(store["last"]) - timedelta(days=2) if store["last"] else today - timedelta(days=14)
    seen = set(store["seen"])
    new, notes = [], []

    sources = [("Federal Register", lambda: federal_register(since)),
               ("Washington SEPA register", lambda: washington_sepa(since)),
               ("Idaho DEQ public comment", idaho_deq)]
    sources += [(name, (lambda s=s, n=name, u=url, p=pat: state_page(s, n, u, p))) for s, name, url, pat in STATE_PAGES]

    for name, read in sources:
        try:
            items = read()
            fresh = [i for i in items if i["id"] not in seen]
            # Web pages have no dates, so the first visit only records what's already there.
            dated = name in ("Federal Register", "Washington SEPA register")
            if not dated and name not in store["baselined"]:
                store["baselined"].append(name)
                notes.append(f"{name}: {len(items)} mining item(s) on the page, recorded as already seen (first visit)")
            else:
                new += fresh
                notes.append(f"{name}: {len(items)} mining item(s), {len(fresh)} new")
            for i in fresh:
                seen.add(i["id"])
                store["seen"].append(i["id"])
        except Exception as error:
            notes.append(f"{name}: FAILED ({str(error)[:120]})")

    store["seen"] = store["seen"][-3000:]
    store["last"] = str(today)
    save(store_file, store)
    if report:
        for line in notes:
            print("  " + line)
    return {"new": new, "notes": notes}


# ---------------------------------------------------------------------------
# 4. SEC 8-K filings from mining companies
# ---------------------------------------------------------------------------

def strip_html(raw):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;|&#xa0;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#8217;|&rsquo;", "'", text)
    text = re.sub(r"&#?\w+;", " ", text)
    return " ".join(text.split())


def watch_8k(data_dir, contact, report=False):
    """New 8-Ks from mining companies (by SEC industry code), filtered to the
    items in KEEP_ITEMS. Reads the SEC's daily index for each business day not
    yet checked (up to 5 days back)."""
    if not contact:
        note = "SEC 8-K filings: skipped (add your contact email as the SEC_CONTACT_EMAIL secret)"
        if report:
            print("  " + note)
        return {"new": [], "notes": [note]}

    headers = {"User-Agent": f"MiningDesk personal research {contact}", "Accept-Encoding": "identity"}
    store_file = data_dir / "gov" / "sec.json"
    store = load(store_file, {"done_days": [], "sic": {}, "seen": []})
    today = date.today()
    new, notes, checked = [], [], 0

    for back in range(5, 0, -1):
        day = today - timedelta(days=back)
        if day.weekday() >= 5 or str(day) in store["done_days"]:
            continue
        quarter = (day.month - 1) // 3 + 1
        url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{quarter}/form.{day:%Y%m%d}.idx"
        try:
            index = fetch(url, headers=headers).decode("latin-1")
        except urllib.error.HTTPError as error:
            if error.code in (403, 404):
                notes.append(f"SEC index for {day}: not available ({error.code})")
                if error.code == 404 and back > 2:
                    store["done_days"].append(str(day))   # A holiday: nothing was filed
                continue
            raise
        filings = []
        for line in index.splitlines():
            m = re.match(r"^8-K\s+(.+?)\s{2,}(\d+)\s+(\d{8}|\d{4}-\d{2}-\d{2})\s+(\S+)\s*$", line)
            if m:
                filings.append((m[1].strip(), m[2], m[4]))
        checked += len(filings)

        for company, cik, path in filings:
            accession = path.rsplit("/", 1)[-1].replace(".txt", "")
            if accession in store["seen"]:
                continue
            sic = store["sic"].get(cik)
            submissions = None
            if sic is None:
                time.sleep(0.12)   # The SEC allows 10 requests a second
                try:
                    submissions = fetch_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=headers)
                    sic = str(submissions.get("sic") or "")
                except Exception:
                    sic = ""
                store["sic"][cik] = sic
            if sic not in MINING_SIC:
                continue
            if submissions is None:
                time.sleep(0.12)
                submissions = fetch_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=headers)
            recent = submissions.get("filings", {}).get("recent", {})
            try:
                at = recent["accessionNumber"].index(accession)
            except (KeyError, ValueError):
                continue
            items = [i.strip() for i in (recent.get("items", [""] * (at + 1))[at] or "").split(",") if i.strip()]
            wanted = [i for i in items if i in KEEP_ITEMS]
            store["seen"].append(accession)
            if not wanted:
                continue
            folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
            document = recent.get("primaryDocument", [""] * (at + 1))[at]
            text = ""
            try:
                time.sleep(0.12)
                text = strip_html(fetch(folder + document, headers=headers).decode("utf-8", "replace"))
                start = text.find("Item ")
                text = text[start if start > 0 else 0:][:2500]
            except Exception:
                pass
            new.append({"id": "SEC-" + accession, "company": submissions.get("name", company), "cik": cik,
                        "date": recent.get("filingDate", [""] * (at + 1))[at], "sic": sic,
                        "sic_name": submissions.get("sicDescription", ""),
                        "items": ", ".join(f"{i} {KEEP_ITEMS[i]}" for i in wanted),
                        "link": folder + document, "text": text})
        store["done_days"].append(str(day))

    store["done_days"] = store["done_days"][-30:]
    store["seen"] = store["seen"][-3000:]
    save(store_file, store)
    notes.append(f"SEC 8-K filings: {checked} 8-K(s) checked, {len(new)} from mining companies with items that matter")
    if report:
        for line in notes:
            print("  " + line)
    return {"new": new, "notes": notes}


# ---------------------------------------------------------------------------
# 5. MSHA: fatal and serious accidents, imminent-danger orders (weekly)
# ---------------------------------------------------------------------------

def read_msha(name):
    """Download one MSHA zip file and yield each row as a dict with tidy column names."""
    raw = fetch(MSHA + name, timeout=300)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        inner = next(n for n in z.namelist() if n.lower().endswith((".txt", ".csv")))
        with z.open(inner) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter="|")
            header = [re.sub(r"[^A-Z0-9]+", "_", h.strip().upper()).strip("_") for h in next(reader)]
            for row in reader:
                yield dict(zip(header, (v.strip() for v in row)))


def msha_date(text):
    for pattern in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except (ValueError, TypeError):
            pass
    return None


def skip_mine(coal_flag, mine):
    """Coal mines and sand-and-gravel operations are left out of the MSHA reports."""
    if coal_flag.upper().startswith("C") or mine.get("coal"):
        return True
    return bool(re.search(r"sand|gravel", mine.get("commodity", ""), re.IGNORECASE))


def watch_msha(data_dir, report=False):
    store_file = data_dir / "gov" / "msha.json"
    store = load(store_file, {"seen": []})
    seen = set(store["seen"])
    cutoff = date.today() - timedelta(days=MSHA_LOOKBACK_DAYS)
    new, notes = [], []

    try:
        mines = {}
        for row in read_msha("Mines.zip"):
            mines[row.get("MINE_ID", "")] = {
                "mine": row.get("CURRENT_MINE_NAME", "").title(), "state": row.get("STATE", ""),
                "operator": row.get("CURRENT_OPERATOR_NAME", "").title(),
                "commodity": row.get("PRIMARY_SIC", "").title(), "type": row.get("CURRENT_MINE_TYPE", "").title(),
                "coal": row.get("COAL_METAL_IND", "").upper().startswith("C"),
                "county": row.get("FIPS_CNTY_NM", "").title()}
        notes.append(f"MSHA mines list: {len(mines)} mines")
    except Exception as error:
        mines = {}
        notes.append(f"MSHA mines list: FAILED ({str(error)[:120]})")

    try:
        count = 0
        for row in read_msha("Accidents.zip"):
            when = msha_date(row.get("ACCIDENT_DT", ""))
            if not when or when < cutoff:
                continue
            degree = row.get("DEGREE_INJURY", "").upper()
            code = row.get("DEGREE_INJURY_CD", "").lstrip("0")
            serious = code in ("1", "2") or "FATAL" in degree or "PERM" in degree
            try:
                several = int(row.get("NO_INJURIES") or 0) >= 3
            except ValueError:
                several = False
            key = "ACC-" + row.get("DOCUMENT_NO", "")
            if not (serious or several) or key in seen:
                continue
            mine = mines.get(row.get("MINE_ID", ""), {})
            if skip_mine(row.get("COAL_METAL_IND", ""), mine):
                continue
            new.append({"id": key, "kind": "Fatal accident" if code == "1" or "FATAL" in degree else "Serious accident",
                        "date": str(when), "mine": mine.get("mine", ""), "operator": row.get("OPERATOR_NAME", "").title() or mine.get("operator", ""),
                        "state": mine.get("state", ""), "commodity": mine.get("commodity", ""),
                        "coal_metal": row.get("COAL_METAL_IND", ""), "detail": f"{row.get('ACCIDENT_TYPE', '').capitalize()}. {row.get('NARRATIVE', '')}".strip(),
                        "mine_id": row.get("MINE_ID", ""), "link": MSHA_LOOKUP})
            seen.add(key)
            count += 1
        notes.append(f"MSHA accidents: {count} new fatal or serious (coal and sand & gravel left out)")
    except Exception as error:
        notes.append(f"MSHA accidents: FAILED ({str(error)[:120]})")

    try:
        count = 0
        for row in read_msha("OrdersIssued.zip"):
            issued = msha_date(row.get("VIOLATION_DATE_ISSUED", "") or row.get("DATE_ISSUED", ""))
            number = row.get("VIOLATION_NO", "") or row.get("VIOLATION_NUMBER", "")
            key = "107A-" + number
            if not issued or issued < cutoff or key in seen:
                continue
            mine = mines.get(row.get("MINE_ID", ""), {})
            if skip_mine(row.get("COAL_OR_METAL", ""), mine):
                continue
            new.append({"id": key, "kind": "Imminent-danger order (107(a))", "date": str(issued),
                        "mine": row.get("MINE_NAME", "").title() or mine.get("mine", ""),
                        "operator": row.get("OPERATOR_NAME_VIOLATIONS", "").title() or mine.get("operator", ""),
                        "state": row.get("STATE_ABBREVIATION", "") or mine.get("state", ""),
                        "commodity": mine.get("commodity", ""), "coal_metal": row.get("COAL_OR_METAL", ""),
                        "detail": "Order issued" + (" and since lifted" if row.get("TERMINATION_DATE") else ", still in force"),
                        "mine_id": row.get("MINE_ID", ""), "link": MSHA_LOOKUP})
            seen.add(key)
            count += 1
        notes.append(f"MSHA imminent-danger orders: {count} new")
    except Exception as error:
        notes.append(f"MSHA imminent-danger orders: FAILED ({str(error)[:120]})")

    store["seen"] = (store["seen"] + [i["id"] for i in new])[-6000:]
    save(store_file, store)
    if report:
        for line in notes:
            print("  " + line)
    return {"new": new, "notes": notes}


# ---------------------------------------------------------------------------
# Run on its own: check every source (uses a scratch folder; your data is untouched)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scratch = Path(tempfile.mkdtemp())
    print("Claim packages (this can take a minute)...")
    claims = watch_claims(scratch, report=True)
    for p in claims["active"][:5]:
        print(f"    {p['source']}, {p['state']}: {p['name']} - {p['count']} claims, {p['first']} to {p['last']}"
              f"{' - ' + p['town'] if p.get('town') else ''}{' - claimant ' + p['claimant'] if p.get('claimant') else ''}")
    print("\nExploration notices...")
    notices = watch_notices(scratch, report=True)
    for n in notices["new"][:5]:
        print(f"    {n['state']} {n['serial']}: {n['name']} ({n['created']}) {n.get('town', '')}")
    print("\nEnvironmental reviews...")
    reviews = watch_reviews(scratch, report=True)
    for r in reviews["new"][:8]:
        print(f"    [{r['state'] or '--'}] {r['title'][:90]}  ({r['source']})")
    print("\nSEC 8-K filings...")
    filings = watch_8k(scratch, os.environ.get("SEC_CONTACT_EMAIL", ""), report=True)
    for f in filings["new"][:5]:
        print(f"    {f['date']} {f['company']}: {f['items']}")
    print("\nMSHA reports (downloads MSHA's data files; this can take a few minutes)...")
    msha = watch_msha(scratch, report=True)
    for m in msha["new"][:5]:
        print(f"    {m['date']} {m['kind']}: {m['mine']} ({m['state']}) - {m['detail'][:80]}")
