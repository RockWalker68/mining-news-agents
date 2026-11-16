"""
fetch_news.py: collects headlines from every news source.

Each source belongs to a "lane", which decides who reads it:

  industry   mining news                     -> Meldrum and Yukon Cornelius
  juniors    junior-company press releases   -> Meldrum and Yukon Cornelius
  tech       technology and demand news      -> Meldrum and Yukon Cornelius
  world      general world news              -> Dr I Simon only

pipeline.py calls fetch_new_articles() about once an hour.

To check which sources are working, run this file on its own:

    python fetch_news.py

It prints one line per source, marked "ok", "used backup" or "FAILED". This
check doesn't touch your data folder, so it is safe to run at any time.
"""

import json
import re
import socket
import tempfile
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import feedparser  # Reads RSS feeds. Installed via requirements.txt

socket.setdefaulttimeout(25)   # Never wait more than 25 seconds on a slow site


def google_news(query):
    """Build a Google News search feed. Works for any topic or website."""
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


# ---------------------------------------------------------------------------
# Sources you can change
# ---------------------------------------------------------------------------
# name:   shown in the status report
# lane:   who reads it (see the top of this file)
# url:    an RSS feed, or a web page when kind is "page"
# backup: tried automatically if the main url fails
# limit:  the most articles to take from this source per run

SOURCES = [
    # --- Mining industry news ------------------------------------------------
    {"name": "Google News: mining industry", "lane": "industry", "limit": 25,
     "url": "https://news.google.com/rss/search?q=mining+industry&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Google News: copper, lithium, gold", "lane": "industry", "limit": 25,
     "url": "https://news.google.com/rss/search?q=copper+OR+lithium+OR+gold+mining&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Mining.com", "lane": "industry", "limit": 20,
     "url": "https://www.mining.com/feed/",
     "backup": google_news("site:mining.com when:2d")},
    {"name": "Mining Weekly", "lane": "industry", "limit": 20,
     "url": "https://www.miningweekly.com/page/latest-news/feed",
     "backup": google_news("site:miningweekly.com when:2d")},
    {"name": "Global Mining Review", "lane": "industry", "limit": 10,
     "url": google_news("site:globalminingreview.com when:2d")},

    # --- North America first: the reader is based here --------------------
    {"name": "The Northern Miner", "lane": "industry", "limit": 20,
     "url": "https://www.northernminer.com/feed/",
     "backup": google_news("site:northernminer.com when:2d")},
    {"name": "Canadian Mining Journal", "lane": "industry", "limit": 15,
     "url": "https://www.canadianminingjournal.com/feed/",
     "backup": google_news("site:canadianminingjournal.com when:3d")},
    {"name": "Google News: Canada mining", "lane": "industry", "limit": 20,
     "url": google_news('(mine OR mining) (Ontario OR Quebec OR "British Columbia" OR Yukon OR Nunavut OR Saskatchewan OR Manitoba OR Labrador OR "Northwest Territories" OR "Nova Scotia") when:2d')},
    {"name": "Google News: US mining", "lane": "industry", "limit": 20,
     "url": google_news('(mine OR mining) (Nevada OR Arizona OR Alaska OR Idaho OR Montana OR Utah OR Minnesota OR Wyoming OR Michigan OR "South Dakota") -bitcoin -crypto when:2d')},
    {"name": "Google News: North America permits and projects", "lane": "industry", "limit": 15,
     "url": google_news('(mine OR mining) (permit OR "record of decision" OR "environmental assessment" OR "feasibility study" OR "construction decision" OR "FAST-41") (Canada OR "United States" OR Nevada OR Arizona OR Alaska OR Ontario OR Quebec OR "British Columbia") when:3d')},
    {"name": "Google News: North America operations", "lane": "industry", "limit": 15,
     "url": google_news('(mine OR mining) ("commercial production" OR output OR guidance OR restart OR shutdown OR layoffs OR strike) (Canada OR "United States" OR Nevada OR Arizona OR Ontario OR Quebec OR "British Columbia") when:2d')},

    # Regional searches, so the desk hears about the rest of the world too.
    {"name": "Google News: Africa mining", "lane": "industry", "limit": 10,
     "url": google_news('mining (Africa OR Zambia OR Congo OR Ghana OR Mali OR Guinea OR "South Africa") when:2d')},
    {"name": "Google News: Latin America mining", "lane": "industry", "limit": 10,
     "url": google_news("mining (Chile OR Peru OR Brazil OR Argentina OR Mexico OR Ecuador OR Colombia) when:2d")},
    {"name": "Google News: Asia-Pacific mining", "lane": "industry", "limit": 10,
     "url": google_news("mining (Australia OR Indonesia OR China OR Mongolia OR India OR Philippines) when:2d")},
    {"name": "Google News: Europe and Central Asia mining", "lane": "industry", "limit": 8,
     "url": google_news("mining (Europe OR Sweden OR Serbia OR Kazakhstan OR Uzbekistan OR Saudi OR Greenland) when:2d")},

    # --- Junior Mining Network (company press releases) ----------------------
    # This site has no RSS feed, so we read the links off its news page.
    {"name": "Junior Mining Network", "lane": "juniors", "limit": 25, "kind": "page",
     "url": "https://www.juniorminingnetwork.com/junior-miner-news",
     "link_pattern": r"/junior-miner-news/.*\d+-[^/]+\.html$",
     "backup": google_news("site:juniorminingnetwork.com when:2d")},
    {"name": "Newsfile: drill results and resources", "lane": "juniors", "limit": 20,
     "url": google_news('site:newsfilecorp.com (drill OR drilling OR assays OR intersects OR discovery OR "mineral resource") when:2d')},
    {"name": "GlobeNewswire: drill results and resources", "lane": "juniors", "limit": 15,
     "url": google_news('site:globenewswire.com (drill OR drilling OR assays OR intersects OR "mineral resource") (gold OR copper OR silver OR lithium OR uranium OR nickel OR zinc) when:2d')},
    {"name": "Business Wire / PR Newswire: mining releases", "lane": "juniors", "limit": 10,
     "url": google_news('(site:businesswire.com OR site:prnewswire.com) (drill OR drilling OR assays OR "mineral resource" OR "feasibility study") (gold OR copper OR silver OR lithium OR uranium) when:2d')},

    # --- Technology and demand -----------------------------------------------
    {"name": "Google News: battery chemistry", "lane": "tech", "limit": 10,
     "url": google_news('"sodium-ion" OR "solid-state battery" OR "LFP battery" OR "battery chemistry" OR "cathode material" OR "battery recycling" when:2d')},
    {"name": "Google News: metal substitution", "lane": "tech", "limit": 10,
     "url": google_news('"rare-earth-free" OR "cobalt-free" OR "nickel-free" OR "copper substitution" OR "aluminium instead of copper" OR "aluminum instead of copper" OR "silver thrifting" OR "metal substitution" when:7d')},
    {"name": "Google News: AI and grid demand for metals", "lane": "tech", "limit": 10,
     "url": google_news('("data center" OR "data centre" OR "AI boom" OR "power grid" OR electrification) (copper OR "critical minerals" OR metals OR transformers) when:2d')},
    {"name": "Google News: energy transition minerals", "lane": "tech", "limit": 10,
     "url": google_news('"critical minerals" ("energy transition" OR "electric vehicles" OR batteries OR "clean energy") when:2d')},

    # --- World news (read by Dr I Simon) --------------------------------------
    {"name": "BBC World", "lane": "world", "limit": 10,
     "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
     "backup": google_news("site:bbc.com/news/world when:1d")},
    {"name": "Reuters World", "lane": "world", "limit": 10,
     "url": google_news("when:24h site:reuters.com/world")},
    {"name": "Associated Press", "lane": "world", "limit": 6,
     "url": google_news("site:apnews.com when:1d")},
    {"name": "Al Jazeera", "lane": "world", "limit": 6,
     "url": "https://www.aljazeera.com/xml/rss/all.xml",
     "backup": google_news("site:aljazeera.com/news when:1d")},
    {"name": "DW (Germany)", "lane": "world", "limit": 6,
     "url": "https://rss.dw.com/rdf/rss-en-world",
     "backup": google_news("site:dw.com/en when:1d")},
    {"name": "France 24", "lane": "world", "limit": 6,
     "url": "https://www.france24.com/en/rss",
     "backup": google_news("site:france24.com/en when:1d")},
    {"name": "The Guardian World", "lane": "world", "limit": 6,
     "url": "https://www.theguardian.com/world/rss",
     "backup": google_news("site:theguardian.com/world when:1d")},
    {"name": "NHK World (Japan)", "lane": "world", "limit": 5,
     "url": google_news("site:nhk.or.jp/nhkworld when:1d")},
    {"name": "ABC News (Australia)", "lane": "world", "limit": 5,
     "url": "https://www.abc.net.au/news/feed/51120/rss.xml",
     "backup": google_news("site:abc.net.au/news when:1d")},
    {"name": "CBC World (Canada)", "lane": "world", "limit": 5,
     "url": "https://www.cbc.ca/webfeed/rss/rss-world",
     "backup": google_news("site:cbc.ca/news when:1d")},
    {"name": "MercoPress (Latin America)", "lane": "world", "limit": 5,
     "url": google_news("site:en.mercopress.com when:1d")},
    {"name": "Daily Maverick (South Africa)", "lane": "world", "limit": 5,
     "url": google_news("site:dailymaverick.co.za when:1d")},
    {"name": "Nikkei Asia", "lane": "world", "limit": 5,
     "url": google_news("site:asia.nikkei.com when:1d")},
    {"name": "The Jakarta Post (Indonesia)", "lane": "world", "limit": 4,
     "url": google_news("site:thejakartapost.com when:1d")},
]

# Headlines matching any of these are dropped before the agents see them:
# stock tips, market-research adverts, crypto "mining", and TV shows.
NOISE = re.compile(
    r"bitcoin|crypto|blockchain|ethereum|hashrate|data mining|text mining"
    r"|stocks? to (buy|watch)|top \d+ .*stocks|is it too late to buy|should you buy|price target"
    r"|motley fool|zacks|stock screen"
    r"|market (size|report|to 20\d\d|forecast to)|market research|cagr|indexbox"
    r"|minecraft|sponsored content"
    # Junior-miner stock pages and investor-education pieces, not news
    r"|stock quote|list of mining stocks|^press releases\s|investing news network",
    re.IGNORECASE)

# Entertainment items are dropped from the world lane only.
ENTERTAINMENT = re.compile(
    r"\bmovies?\b|\bfilms?\b|box office|\bcinema|\bactors?\b|\bactress|\bcelebrit"
    r"|\boscars?\b|\bgrammys?\b|\bemmys?\b|golden globe|\bbafta|film festival"
    r"|\bstreaming\b|\bnetflix\b|\bdisney\+|\bhbo\b|\bbox-office\b|\bTV series\b|\bsitcom\b"
    r"|\balbum\b|\bconcert\b|\btour dates?\b|taylor swift|beyonc|\bpop star\b"
    r"|james bond|\bbond (film|movie|actor|star)s?\b|robert pattinson|\breality (show|tv)\b"
    r"|\bcelebrity\b|\bmet gala\b|\broyal (baby|wedding)\b",
    re.IGNORECASE)

# Some sites refuse requests that don't look like a web browser.
BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# How many seen articles to remember. Old ones are dropped so the file stays small.
MAX_SEEN = 12000


# ---------------------------------------------------------------------------
# Reading one source
# ---------------------------------------------------------------------------

def read_feed(url, limit):
    """Read an RSS feed and return a list of article dicts."""
    # Google News sometimes answers with a short error page ("unusual traffic")
    # instead of the feed when many searches arrive at once. Waiting and asking
    # again usually works, so try up to three times before giving up.
    waits = (0, 5, 15) if "news.google.com" in url else (0,)
    for wait in waits:
        if wait:
            time.sleep(wait)
        feed = feedparser.parse(url, agent=BROWSER_AGENT)
        if feed.entries:
            break

    # feedparser doesn't crash on errors; it just returns no entries.
    if not feed.entries:
        raise RuntimeError(feed.get("bozo_exception", "no articles returned"))

    articles = []
    for entry in feed.entries[:limit]:
        source = entry.get("source", {}).get("title") or feed.feed.get("title", "")
        articles.append({
            "title": clean_title(entry.get("title", ""), source),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            "source": source,
        })
    return articles


class _LinkCollector(HTMLParser):
    """Collects every link on a web page as (address, link text)."""

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
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def links_from_html(page_html, base_url, link_pattern, limit, source_name):
    """Pick the news links out of a web page's HTML."""
    collector = _LinkCollector()
    collector.feed(page_html)

    pattern = re.compile(link_pattern)
    titles = {}   # address -> best headline found (dicts keep page order)
    for href, text in collector.links:
        if not href or not pattern.search(href.split("?")[0]):
            continue
        address = urljoin(base_url, href.split("?")[0])
        if text.lower() in ("", "read more", "read more...", "more"):
            text = ""
        if len(text) > len(titles.get(address, "")) or address not in titles:
            titles[address] = text or titles.get(address, "")

    articles = []
    for address, title in list(titles.items())[:limit]:
        if not title:   # No usable link text: build a headline from the address
            slug = address.rsplit("/", 1)[-1].removesuffix(".html")
            title = re.sub(r"^\d+-", "", slug).replace("-", " ").title()
        articles.append({"title": title, "link": address, "published": "", "source": source_name})
    return articles


def read_page(source):
    """Read a web page that has no RSS feed and return its news links."""
    request = urllib.request.Request(source["url"], headers={"User-Agent": BROWSER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        page_html = response.read().decode("utf-8", errors="replace")
    articles = links_from_html(page_html, source["url"], source["link_pattern"],
                               source["limit"], source["name"])
    if not articles:
        raise RuntimeError("no news links found on the page")
    return articles


def read_source(source):
    """Read one source, falling back to its backup. Returns (articles, note)."""
    if "news.google.com" in source["url"]:
        time.sleep(2)   # Space out Google News searches; a quick burst gets refused
    try:
        if source.get("kind") == "page":
            return read_page(source), "ok"
        return read_feed(source["url"], source["limit"]), "ok"
    except Exception as error:
        problem = str(error)[:70]
        if source.get("backup"):
            try:
                return read_feed(source["backup"], source["limit"]), f"used backup ({problem})"
            except Exception as backup_error:
                problem += f"; backup failed too: {str(backup_error)[:50]}"
        return [], f"FAILED: {problem}"


# ---------------------------------------------------------------------------
# Tidying and de-duplicating
# ---------------------------------------------------------------------------

def clean_title(title, source):
    """Google News adds ' - Source' to every headline. Remove it."""
    title = " ".join(title.split())
    if source and title.endswith(" - " + source):
        title = title[: -len(source) - 3]
    return title.strip()


def title_key(title):
    """A simplified headline, used to spot the same story from two outlets."""
    return "t:" + re.sub(r"[^a-z0-9]", "", title.lower())[:80]


STOP_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "is", "of",
              "on", "or", "the", "to", "with", "its", "new", "how", "why", "what", "after"}


def title_words(title):
    """The meaningful words in a headline, with hyphens split apart."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in STOP_WORDS}


def same_story(words_a, words_b):
    """True when two headlines are almost certainly about the same event."""
    shared = len(words_a & words_b)
    if shared < 4:
        return False
    smaller = min(len(words_a), len(words_b))
    union = len(words_a | words_b)
    return shared / smaller >= 0.6 or shared / union >= 0.5


# ---------------------------------------------------------------------------
# The function pipeline.py calls
# ---------------------------------------------------------------------------

def fetch_new_articles(data_dir=Path("data"), report=True):
    """Read every source and return only articles we haven't seen before."""
    data_dir.mkdir(parents=True, exist_ok=True)
    seen_file = data_dir / "seen.json"
    seen = json.loads(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else []
    seen_set = set(seen)

    new_articles = []
    for source in SOURCES:
        articles, note = read_source(source)

        fresh = 0
        for article in articles:
            link, key = article["link"], title_key(article["title"])
            if not link or not article["title"] or link in seen_set or key in seen_set:
                continue
            seen_set.update((link, key))
            seen += [link, key]
            text = f"{article['title']} {article['source']}"
            if source["lane"] != "world" and NOISE.search(text):
                continue   # Remembered as seen, but never shown to the agents
            if source["lane"] == "world" and ENTERTAINMENT.search(text):
                continue   # World news only: celebrity, film and TV items are dropped
            # The same story often arrives from several outlets in one run.
            # Keep the first version and drop the rest.
            words = title_words(article["title"])
            if any(other["lane"] == source["lane"] and same_story(words, other["_words"])
                   for other in new_articles):
                continue
            article["lane"] = source["lane"]
            article["_words"] = words
            new_articles.append(article)
            fresh += 1

        if report:
            print(f"  {source['lane']:8} {source['name'][:44]:44} {fresh:3} new of {len(articles):3}  {note}")

    seen_file.write_text(json.dumps(seen[-MAX_SEEN:], indent=1), encoding="utf-8")
    for article in new_articles:
        article.pop("_words", None)   # Only needed for the comparison above
    return new_articles


# Run on its own: show which sources work and what's new.
if __name__ == "__main__":
    print("Checking every news source...\n")
    found = fetch_new_articles(Path(tempfile.mkdtemp()))   # A scratch folder, so nothing is marked as seen
    print(f"\nFound {len(found)} article(s). A few from each lane:")
    for lane in ("industry", "juniors", "tech", "world"):
        print(f"\n[{lane}]")
        for article in [a for a in found if a["lane"] == lane][:5]:
            print(f"  - {article['title'][:100]}  ({article['source']})")
