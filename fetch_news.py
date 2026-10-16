"""
fetch_news.py: fetch mining-industry headlines from RSS feeds.

pipeline.py calls fetch_new_articles() every hour. You can also run this
file on its own to check the feeds are working:

    python fetch_news.py

It skips any article already seen (tracked in data/seen.json), so a second
run right away should find 0 new articles.
"""

import json
from pathlib import Path

import feedparser  # Reads RSS feeds. Installed via requirements.txt

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------

# RSS feeds to check. Add or remove any you like.
# For Google News, change the q= part of the URL to search other keywords.
FEEDS = [
    "https://news.google.com/rss/search?q=mining+industry&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=copper+OR+lithium+OR+gold+mining&hl=en-US&gl=US&ceid=US:en",
    "https://www.mining.com/feed/",
]

# If a feed fails, try its backup instead. Mining.com sometimes blocks scripts,
# so its backup pulls Mining.com stories through Google News.
BACKUPS = {
    "https://www.mining.com/feed/":
        "https://news.google.com/rss/search?q=site:mining.com&hl=en-US&gl=US&ceid=US:en",
}

# Some sites refuse requests that don't look like a web browser, and send back
# a web page instead of the feed (that's the "mismatched tag" error).
# Identifying as a normal browser avoids that.
BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Only keep this many articles from each feed per run.
MAX_PER_FEED = 15

# How many seen links to remember. Old ones are dropped so the file stays small.
MAX_SEEN = 3000


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def fetch_feed(url):
    """Download one RSS feed and return a list of simple article dicts."""
    feed = feedparser.parse(url, agent=BROWSER_AGENT)

    # feedparser doesn't crash on network errors; it just returns no entries.
    # So we check for that ourselves and raise an error with a clear message.
    if not feed.entries:
        problem = feed.get("bozo_exception", "no articles returned")
        raise RuntimeError(problem)

    articles = []
    for entry in feed.entries[:MAX_PER_FEED]:
        articles.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            # Google News puts the outlet name in entry.source; other feeds use the feed title
            "source": entry.get("source", {}).get("title") or feed.feed.get("title", ""),
        })
    return articles


def fetch_new_articles(data_dir=Path("data")):
    """Fetch every feed and return only articles we haven't seen before."""
    data_dir.mkdir(parents=True, exist_ok=True)
    seen_file = data_dir / "seen.json"
    seen = json.loads(seen_file.read_text()) if seen_file.exists() else []
    seen_set = set(seen)

    new_articles = []
    for url in FEEDS:
        try:
            articles = fetch_feed(url)
        except Exception as error:
            # If one feed fails, report it, try its backup, and keep going.
            print(f"Could not read {url}: {error}")
            backup = BACKUPS.get(url)
            if not backup:
                continue
            try:
                articles = fetch_feed(backup)
                print("  ...used the backup feed instead")
            except Exception as error:
                print(f"  ...backup failed too: {error}")
                continue

        for article in articles:
            link = article["link"]
            if link and link not in seen_set:
                new_articles.append(article)
                seen_set.add(link)
                seen.append(link)

    # Remember what we've seen (keeping only the most recent links).
    seen_file.write_text(json.dumps(seen[-MAX_SEEN:], indent=2))
    return new_articles


# Run on its own: print the new headlines.
if __name__ == "__main__":
    found = fetch_new_articles()
    print(f"\nFound {len(found)} new article(s):\n")
    for article in found:
        print(f"- {article['title']}  ({article['source']})")
