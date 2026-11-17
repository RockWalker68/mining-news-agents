"""
dan.py: gathers everything for Dan's Tab, the personal page The Rat builds
each morning. It has nothing to do with mining.

  Top of the page   An animal or sea creature of the day (uncommon, from
                    outside North America; photo and facts from Wikipedia)
                    and a quote of the day (from the sourced list below).
  Then              Community events in Spokane and Coeur d'Alene, followed
                    by Dan's other interests. The sections, their notes and
                    their news searches live in data/dan/profile.json, which
                    The Rat edits when Dan sends a message from the page.

Also on the page: a place on Earth each day, a long read each week, and a
local volunteer spotlight each week.

PRIVATE: everything personal is encrypted with the DAN_PASSWORD secret (see
vault.py): the published page, the profile, ratings and saved state (the
.lock files in data/dan), and the feedback the page sends. Without that
secret, Dan's Tab is skipped rather than published unlocked. The starting
profile below is part of this public code; after the first run The Rat works
from the encrypted copy.

Feedback from the page (ratings and messages) arrives encrypted, as GitHub
issues titled "[dan] feedback". Only issues opened by the repository's owner
are read; each is closed once it has been applied.

To check which sources are working, run this file on its own:

    python dan.py

It prints what each source returns and doesn't touch your data folder.
"""

import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import fetch_news
import vault
from gov_watch import LinkReader, fetch

# ---------------------------------------------------------------------------
# The starting profile. After the first run the live copy is
# data/dan/profile.json; The Rat changes that one when Dan asks.
# ---------------------------------------------------------------------------

G = fetch_news.google_news

DEFAULT_PROFILE = {
    "notes": [
        "Dan wants to widen his view of the world and know what's going on in his own community.",
        "He is in the 27-40 age group and lives in the Spokane / Coeur d'Alene area.",
    ],
    "topics": [
        {"id": "events", "title": "Community events: Spokane & Coeur d'Alene", "show": 8, "kind": "events",
         "note": "Events in the next few weeks: volunteering, the outdoors, arts and crafts, breweries, and the "
                 "big yearly events. Lean toward what someone aged 27-40 would go to. Skip children's events.",
         "ical": [["Downtown Spokane", "https://downtownspokane.org/calendar/list/?ical=1"],
                  ["Visit Coeur d'Alene", "https://coeurdalene.org/events/list/?ical=1"],
                  ["Downtown Coeur d'Alene", "https://cdadowntown.com/cda-events/list/?ical=1"]],
         "pages": [["Visit Spokane", "https://www.visitspokane.com/events/", r"/event/"],
                   ["Inlander events", "https://www.inlander.com/events", r"[Ee]vent"],
                   ["Washington State Parks (volunteer events)", "https://parks.wa.gov/find-activity/event-calendar",
                    r"/event-calendar/\d{4}/"]],
         "feeds": [],
         "searches": ['Spokane volunteer event when:7d', 'Spokane OR "Coeur d\'Alene" brewery event OR festival when:7d',
                      '"things to do" Spokane OR "Coeur d\'Alene" when:7d',
                      'Spokane OR "North Idaho" trail work party OR cleanup volunteers when:14d']},
        {"id": "local", "title": "Local news: Washington & North Idaho", "show": 5,
         "note": "What matters locally in Spokane, eastern Washington and North Idaho. Skip routine crime and sports scores.",
         "feeds": [["The Spokesman-Review", "https://www.spokesman.com/feeds/stories/"],
                   ["The Inlander", "https://www.inlander.com/spokane/Rss.xml"],
                   ["Washington State Standard", "https://washingtonstatestandard.com/feed/"],
                   ["Idaho Capital Sun", "https://idahocapitalsun.com/feed/"]],
         "searches": ['Spokane when:1d', '"Coeur d\'Alene" OR "North Idaho" OR Sandpoint OR "Kootenai County" when:2d',
                      'site:cdapress.com when:2d']},
        {"id": "moto", "title": "Adventure motorcycles (middleweight)", "show": 4,
         "note": "Adventure-touring bikes between Africa Twin size and 701 size: new models, long-term reviews, "
                 "recalls, travel stories, and Backcountry Discovery Routes in the Northwest.",
         "feeds": [["ADV Pulse", "https://www.advpulse.com/feed/"], ["ADVrider", "https://advrider.com/feed/"]],
         "searches": ['"Tenere 700" OR "Africa Twin" OR "KTM 890 Adventure" OR "Norden 901" OR "Tuareg 660" OR "Transalp" '
                      'OR "V-Strom 800DE" OR "701 Enduro" OR "F 900 GS" OR "DesertX" when:7d',
                      'middleweight adventure motorcycle when:7d',
                      '"Backcountry Discovery Route" OR "adventure motorcycle" Idaho OR Washington OR Oregon when:14d']},
        {"id": "ocean", "title": "The ocean & fisheries", "show": 4,
         "note": "Ocean news, especially fisheries: stocks, seasons, management decisions, science, and salmon and "
                 "steelhead in the Columbia and Snake rivers.",
         "feeds": [["Mongabay Oceans", "https://news.mongabay.com/list/oceans/feed/"]],
         "searches": ['fisheries when:3d', 'ocean science OR "marine biology" discovery when:7d',
                      'salmon OR steelhead "Columbia River" OR "Snake River" OR "Puget Sound" when:7d',
                      '"NOAA Fisheries" when:7d']},
        {"id": "health", "title": "Health breakthroughs", "show": 3,
         "note": "Genuinely important medical advances: strong trial results, first-of-a-kind approvals, major "
                 "findings. Skip wellness tips, supplements and company promotion.",
         "feeds": [["STAT", "https://www.statnews.com/feed/"],
                   ["ScienceDaily Health", "https://www.sciencedaily.com/rss/top/health.xml"]],
         "searches": ['"clinical trial" landmark OR breakthrough OR "first-ever" when:7d', '"FDA approves" first when:7d']},
    ],
}

EVENT_DAYS_AHEAD = 45       # How far ahead to list events
MAX_CANDIDATES = 30         # Most candidates per section shown to The Rat
RATINGS_KEPT = 400

# Big yearly events, with their USUAL timing (dates change: the page says so and links a search).
YEARLY = [
    ("Polar Bear Plunge, Sanders Beach", "Coeur d'Alene", [1], "New Year's Day"),
    ("Mac & Cheese Festival", "Coeur d'Alene", [1], "mid-January"),
    ("Sandpoint Winter Carnival", "Sandpoint", [2], "mid-February"),
    ("Inlander Restaurant Week", "Spokane & Coeur d'Alene", [2, 3], "late February to early March"),
    ("St. Patrick's Day Parade", "Coeur d'Alene and Spokane", [3], "mid-March"),
    ("Get Lit! literary festival", "Spokane", [4], "April"),
    ("Winefest", "Coeur d'Alene", [4], "mid-April"),
    ("Bloomsday run", "Spokane", [5], "first Sunday in May"),
    ("Lilac Festival and Torchlight Parade", "Spokane", [5], "third Saturday in May"),
    ("Lost in the '50s", "Sandpoint", [5], "mid-May"),
    ("Car d'Lane classic car weekend", "Coeur d'Alene", [6], "Father's Day weekend"),
    ("Ironman Coeur d'Alene", "Coeur d'Alene", [6], "late June"),
    ("Hoopfest", "Spokane", [6], "last weekend of June"),
    ("Fourth of July parade and fireworks", "Coeur d'Alene", [7], "July 4"),
    ("Festival at Sandpoint", "Sandpoint", [7, 8], "late July to early August"),
    ("Art on the Green, Street Fair and Taste of Coeur d'Alene", "Coeur d'Alene", [8], "first weekend of August"),
    ("Unity in the Community", "Spokane", [8], "mid-August"),
    ("North Idaho State Fair", "Coeur d'Alene", [8], "late August"),
    ("Pig Out in the Park", "Spokane", [8, 9], "Labor Day weekend"),
    ("Spokane County Interstate Fair", "Spokane Valley", [9], "mid-September"),
    ("SpokeFest", "Spokane", [9], "September"),
    ("Coeur d'Fondo", "Coeur d'Alene", [9], "late September"),
    ("Green Bluff Apple Festival", "Green Bluff", [9, 10], "weekends, late September through October"),
    ("Terrain arts night", "Spokane", [10], "early October"),
    ("Holiday Light Show lighting ceremony and parade", "Coeur d'Alene", [11], "the Friday after Thanksgiving"),
    ("Ski season openings (Schweitzer, Mt. Spokane, 49 Degrees North, Lookout Pass, Silver Mountain)",
     "Inland Northwest", [11, 12], "late November to mid-December, snow permitting"),
]

# Quotes of the day. Each has a source so you can check it. Add your own in the same shape.
QUOTES = [
    ("Winston Churchill", "I have nothing to offer but blood, toil, tears and sweat.", "House of Commons, 13 May 1940"),
    ("Winston Churchill", "Now this is not the end. It is not even the beginning of the end. But it is, perhaps, the end of the beginning.", "Mansion House, London, 10 November 1942"),
    ("Winston Churchill", "Never in the field of human conflict was so much owed by so many to so few.", "House of Commons, 20 August 1940"),
    ("Winston Churchill", "These are not dark days: these are great days, the greatest days our country has ever lived.", "Harrow School, 29 October 1941"),
    ("Winston Churchill", "We shape our buildings, and afterwards our buildings shape us.", "House of Commons, 28 October 1943"),
    ("Winston Churchill", "The empires of the future are the empires of the mind.", "Harvard University, 6 September 1943"),
    ("Winston Churchill", "Give us the tools, and we will finish the job.", "Radio broadcast, 9 February 1941"),
    ("George S. Patton", "A good plan violently executed now is better than a perfect plan next week.", "War As I Knew It (1947)"),
    ("George S. Patton", "Never tell people how to do things. Tell them what to do, and they will surprise you with their ingenuity.", "War As I Knew It (1947)"),
    ("George S. Patton", "Take calculated risks. That is quite different from being rash.", "Letter to his son, 6 June 1944"),
    ("George S. Patton", "A pint of sweat will save a gallon of blood.", "Letter of 3 March 1944, in War As I Knew It (1947)"),
    ("George S. Patton", "Wars may be fought with weapons, but they are won by men.", "Cavalry Journal, September 1933"),
    ("Daniel Patrick Moynihan", "Everyone is entitled to his own opinion, but not his own facts.", "The Washington Post, 1983"),
    ("Marie Curie", "One never notices what has been done; one can only see what remains to be done.", "Letter to her brother, 1894"),
    ("Marie Curie", "Life is not easy for any of us. But what of that? We must have perseverance and above all confidence in ourselves.", "Quoted in Eve Curie, Madame Curie (1937)"),
    ("Marie Curie", "Be less curious about people and more curious about ideas.", "Reply to a reporter, quoted in Living Adventures in Science (1972)"),
    ("Theodore Roosevelt", "It is not the critic who counts; not the man who points out how the strong man stumbles, or where the doer of deeds could have done them better. The credit belongs to the man who is actually in the arena.", "\"Citizenship in a Republic\", the Sorbonne, 23 April 1910"),
    ("Franklin D. Roosevelt", "The only thing we have to fear is fear itself.", "First Inaugural Address, 4 March 1933"),
    ("John F. Kennedy", "We choose to go to the Moon in this decade and do the other things, not because they are easy, but because they are hard.", "Rice University, 12 September 1962"),
    ("Dwight D. Eisenhower", "Plans are worthless, but planning is everything.", "Remarks at the National Defense Executive Reserve Conference, 14 November 1957"),
    ("Frederick Douglass", "If there is no struggle, there is no progress.", "West India Emancipation speech, 3 August 1857"),
    ("Martin Luther King Jr.", "Injustice anywhere is a threat to justice everywhere.", "Letter from Birmingham Jail, 16 April 1963"),
    ("Eleanor Roosevelt", "You must do the thing you think you cannot do.", "You Learn by Living (1960)"),
    ("Richard Feynman", "The first principle is that you must not fool yourself, and you are the easiest person to fool.", "Caltech commencement address, 1974"),
    ("George Orwell", "To see what is in front of one's nose needs a constant struggle.", "\"In Front of Your Nose\", Tribune, 22 March 1946"),
    ("Rachel Carson", "In nature nothing exists alone.", "Silent Spring (1962)"),
    ("Herman Melville", "It is not down in any map; true places never are.", "Moby-Dick (1851)"),
    ("Alfred, Lord Tennyson", "To strive, to seek, to find, and not to yield.", "\"Ulysses\" (1842)"),
    ("William Ernest Henley", "I am the master of my fate: I am the captain of my soul.", "\"Invictus\" (1888)"),
    ("John Muir", "The mountains are calling and I must go.", "Letter to his sister Sarah, 3 September 1873"),
    ("Chester W. Nimitz", "Among the Americans who served on Iwo Island, uncommon valor was a common virtue.", "Pacific Fleet communique, 16 March 1945"),
    ("Abraham Lincoln", "With malice toward none, with charity for all, with firmness in the right as God gives us to see the right, let us strive on to finish the work we are in.", "Second Inaugural Address, 4 March 1865"),
]

# Animals and sea creatures of the day: uncommon ones from outside North America.
# Each is the title of its Wikipedia article.
ANIMALS = [
    "Okapi", "Aye-aye", "Saiga antelope", "Shoebill", "Fossa (animal)", "Gerenuk", "Markhor", "North Sulawesi babirusa",
    "Maned wolf", "Kākāpō", "Leafy seadragon", "Dumbo octopus", "Vampire squid", "Goblin shark", "Frilled shark",
    "Sunda flying lemur", "Numbat", "Tasmanian devil", "Short-beaked echidna", "Platypus", "Southern cassowary",
    "Hoatzin", "Secretarybird", "Bearded vulture", "Snow leopard", "Clouded leopard", "Pallas's cat", "Caracal",
    "Serval", "Binturong", "Dhole", "Common raccoon dog", "Japanese macaque", "Proboscis monkey", "Gelada",
    "Mandrill", "Aardwolf", "Aardvark", "Bongo (antelope)", "Dik-dik", "Gharial", "Komodo dragon", "Tuatara",
    "Thorny devil", "Frilled lizard", "West Indian Ocean coelacanth", "Arapaima", "Queensland lungfish",
    "Giant oarfish", "Chambered nautilus", "Flamboyant cuttlefish", "Mimic octopus", "Greater blue-ringed octopus",
    "Giant isopod", "Kiwa hirsuta", "Scotoplanes", "Glaucus atlanticus", "Dugong", "Irrawaddy dolphin",
    "Baikal seal", "Leopard seal", "Emperor penguin", "Wandering albatross", "Kea", "North Island brown kiwi",
    "South Island takahē", "Superb lyrebird", "Satin bowerbird", "Wilson's bird-of-paradise", "Rhinoceros hornbill",
    "Sunda pangolin", "Sunda slow loris", "Saola", "Malayan tapir", "Capybara", "Giant anteater",
    "Pink fairy armadillo", "Pygmy marmoset", "Bald uakari", "Andean condor", "Vicuña", "Giant otter",
    "Amazon river dolphin", "Electric eel", "Pygmy three-toed sloth", "Long-eared jerboa", "Naked mole-rat",
    "Rock hyrax", "Lowland streaked tenrec", "Indri", "Coquerel's sifaka", "Zebra duiker", "Takin",
    "Tibetan antelope", "Przewalski's horse", "Wild Bactrian camel", "Red panda", "Chinese giant salamander",
    "Sun bear", "Sloth bear", "Spectacled bear", "Common wombat", "Sugar glider", "Matschie's tree-kangaroo",
    "Tiger quoll", "Greater bilby", "Quokka", "Philippine tarsier", "Philippine eagle", "Kagu", "Hooded pitohui",
    "Lilac-breasted roller", "Sociable weaver", "African wild dog", "Honey badger", "Ethiopian wolf", "Nyala",
    "Sitatunga", "Giant eland", "Black-footed cat", "Sand cat", "Fennec fox", "Bat-eared fox", "Tasselled wobbegong",
    "Epaulette shark", "Japanese spider crab", "Peacock mantis shrimp", "Mandarinfish", "Ocean sunfish",
    "Leafy seadragon", "Weedy seadragon", "Barreleye", "Humpback anglerfish", "Pelican eel", "Sarcastic fringehead",
    "Red-lipped batfish", "Mudskipper", "Archerfish", "Mekong giant catfish", "Giant freshwater stingray",
    "Irukandji jellyfish", "Turritopsis dohrnii", "Christmas Island red crab", "Coconut crab", "Giant clam",
    "Crown-of-thorns starfish", "Sea pen", "Bobbit worm", "Javan rhinoceros", "Pygmy hippopotamus", "Kinkajou",
    "Maleo", "Marabou stork", "Hamerkop", "Great hornbill", "Resplendent quetzal", "Sri Lanka frogmouth",
]

# One place on Earth each day: regions, cities, islands and natural wonders. Wikipedia titles.
PLACES = [
    "Socotra", "Svalbard", "Kamchatka Peninsula", "Atacama Desert", "Tsingy de Bemaraha National Park", "Danakil Depression",
    "Lake Baikal", "Faroe Islands", "Lofoten", "Wakhan Corridor", "Pamir Mountains", "Gobi Desert", "Altai Mountains",
    "Ladakh", "Bhutan", "Mustang, Nepal", "Hunza Valley", "Samarkand", "Bukhara", "Khiva", "Petra", "Wadi Rum",
    "Musandam Peninsula", "Isfahan", "Tbilisi", "Svaneti", "Lalibela", "Simien Mountains", "Okavango Delta",
    "Skeleton Coast", "Namib Desert", "Zanzibar", "Lamu", "Ngorongoro Conservation Area", "Rwenzori Mountains",
    "Timbuktu", "Djenné", "Chefchaouen", "Draa Valley", "Siwa Oasis", "Mount Sinai", "Cappadocia", "Kotor",
    "Lake Ohrid", "Transylvania", "Danube Delta", "Białowieża Forest", "Sápmi", "Westfjords", "Shetland",
    "Outer Hebrides", "Azores", "Madeira", "Canary Islands", "Sardinia", "Matera", "Dolomites", "Picos de Europa",
    "Patagonia", "Torres del Paine National Park", "Tierra del Fuego", "Chiloé Island", "Easter Island",
    "Galápagos Islands", "Salar de Uyuni", "Lake Titicaca", "Cusco", "Amazon rainforest", "Pantanal", "Lençóis Maranhenses National Park",
    "Fernando de Noronha", "Roraima Tepui", "Angel Falls", "Darién Gap", "Kuna Yala", "Tikal", "Copper Canyon",
    "Yucatán Peninsula", "Kyoto", "Shiretoko Peninsula", "Yakushima", "Okinawa Island", "Jeju Island", "Zhangjiajie",
    "Yunnan", "Guilin", "Lhasa", "Dunhuang", "Hội An", "Hạ Long Bay", "Luang Prabang", "Angkor", "Mergui Archipelago",
    "Borneo", "Raja Ampat Islands", "Komodo National Park", "Sulawesi", "Flores", "Bagan", "Siberut", "Palawan",
    "Banaue Rice Terraces", "Papua New Guinea Highlands", "Sepik River", "Vanuatu", "Tonga", "Kiribati", "Tuvalu",
    "Palau", "Fiordland National Park", "Stewart Island / Rakiura", "Kimberley (Western Australia)", "Uluṟu",
    "Kakadu National Park", "Great Barrier Reef", "Tasmanian Wilderness World Heritage Area", "Lord Howe Island",
    "Antarctic Peninsula", "South Georgia Island", "Kerguelen Islands", "Tristan da Cunha", "Saint Helena",
    "Réunion", "Mauritius", "Seychelles", "Maldives", "Sri Lanka", "Andaman Islands", "Meghalaya", "Sundarbans",
    "Rajasthan", "Kerala", "Spiti Valley", "Greenland", "Nunavut", "Kalahari Desert", "Madagascar", "Mozambique Channel",
]

# Where the week's long read comes from: magazines that publish deep, thoughtful pieces.
LONG_READS = [
    ["Aeon", "https://aeon.co/feed.rss"], ["Noema", "https://www.noemamag.com/feed/"],
    ["Longreads", "https://longreads.com/feed/"], ["Quanta Magazine", "https://www.quantamagazine.org/feed/"],
    ["Hakai Magazine", "https://hakaimagazine.com/feed/"], ["Smithsonian Magazine", "https://www.smithsonianmag.com/rss/latest_articles/"],
    ["Knowable Magazine", "https://knowablemagazine.org/rss"], ["Nautilus", "https://nautil.us/feed/"],
    ["The Public Domain Review", "https://publicdomainreview.org/rss.xml"], ["Atlas Obscura", "https://www.atlasobscura.com/feeds/latest"],
]

# Local non-profits for the weekly volunteer spotlight (name, where, what they do).
# Links go to a web search, so they never point at a wrong address.
VOLUNTEER_ORGS = [
    ("Second Harvest Inland Northwest", "Spokane", "the regional food bank for eastern Washington and North Idaho; volunteers sort and pack food"),
    ("Spokane Riverkeeper", "Spokane", "works for a clean Spokane River; runs river cleanups"),
    ("Inland Northwest Land Conservancy", "Spokane", "a land trust protecting local wild places; runs stewardship and trail days"),
    ("Dishman Hills Conservancy", "Spokane Valley", "protects the Dishman Hills natural area; trail and restoration work"),
    ("Friends of the Centennial Trail", "Spokane to Coeur d'Alene", "looks after the Spokane River Centennial Trail"),
    ("Washington Trails Association", "Eastern Washington", "builds and maintains hiking trails through volunteer work parties"),
    ("Spokane Mountaineers", "Spokane", "an outdoor club with climbing, hiking and skiing, plus trail work"),
    ("Habitat for Humanity Spokane", "Spokane", "builds and repairs homes with volunteer crews"),
    ("North Idaho Habitat for Humanity", "Coeur d'Alene", "builds and repairs homes with volunteer crews"),
    ("Kootenai Environmental Alliance", "Coeur d'Alene", "works on the health of Lake Coeur d'Alene and its watershed"),
    ("The Lands Council", "Spokane", "works on forests, water and wildlife in the Inland Northwest; tree planting and restoration"),
    ("Big Brothers Big Sisters of the Inland Northwest", "Spokane & North Idaho", "one-to-one mentoring for young people"),
    ("Northwest Museum of Arts & Culture", "Spokane", "the region's museum of art, history and culture; volunteer guides and helpers"),
    ("Friends of Manito", "Spokane", "supports Manito Park and its gardens"),
    ("Union Gospel Mission Inland Northwest", "Spokane & Coeur d'Alene", "shelter, meals and recovery programs"),
    ("St. Vincent de Paul North Idaho", "Coeur d'Alene", "help with housing, food and essentials in North Idaho"),
    ("Spokane Humane Society", "Spokane", "animal shelter; volunteers walk dogs and socialize cats"),
    ("Kootenai Humane Society", "Hayden", "animal shelter for Kootenai County"),
]

WIKI = "https://en.wikipedia.org/api/rest_v1/page/summary/"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load(folder, name, password, default):
    """Read one of Dan's encrypted files (data/dan/<name>.lock). A plain .json
    copy left by an earlier version is read once, then locked and removed."""
    plain = folder / f"{name}.json"
    if plain.exists():
        data = json.loads(plain.read_text(encoding="utf-8"))
        vault.save(folder / f"{name}.lock", data, password)
        plain.unlink()
        return data
    return vault.load(folder / f"{name}.lock", password, default)


def save(folder, name, data, password):
    vault.save(folder / f"{name}.lock", data, password)


def friendly_date(d):
    return f"{d:%a %b} {d.day}"


# ---------------------------------------------------------------------------
# Calendars (iCal), web pages and feeds
# ---------------------------------------------------------------------------

def read_ical(name, url, today):
    """Read an iCal calendar and return upcoming events as candidate items."""
    text = fetch(url).decode("utf-8", "replace").replace("\r\n", "\n")
    text = re.sub(r"\n[ \t]", "", text)                      # Long lines are folded: join them back up
    unescape = lambda v: v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()
    items = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        fields = {}
        for line in block.split("END:VEVENT")[0].splitlines():
            key, _, value = line.partition(":")
            fields.setdefault(key.split(";")[0].upper(), value)
        start = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", fields.get("DTSTART", ""))
        if not start or not fields.get("SUMMARY"):
            continue
        day = date(int(start[1]), int(start[2]), int(start[3]))
        end = re.match(r"(\d{4})(\d{2})(\d{2})", fields.get("DTEND", ""))
        last = date(int(end[1]), int(end[2]), int(end[3])) if end else day
        if last < today or day > today + timedelta(days=EVENT_DAYS_AHEAD):
            continue
        when = friendly_date(max(day, today)) if day < today else friendly_date(day)
        if day < today:
            when = f"now through {friendly_date(last)}"
        elif start[4] and (start[4], start[5]) != ("00", "00"):
            hour = int(start[4])
            when += f", {hour % 12 or 12}:{start[5]} {'AM' if hour < 12 else 'PM'}"
        place = unescape(fields.get("LOCATION", "")).split(",")[0]
        items.append({"title": unescape(fields["SUMMARY"]), "link": fields.get("URL", "").strip() or url,
                      "source": name, "when": when, "sort": str(max(day, today)), "place": place,
                      "about": unescape(fields.get("DESCRIPTION", ""))[:160]})
    return items


def read_page(name, url, pattern):
    reader = LinkReader()
    reader.feed(fetch(url).decode("utf-8", "replace"))
    items, seen = [], set()
    for href, text in reader.links:
        link = urllib.parse.urljoin(url, href or "")
        if not text or len(text) < 8 or link in seen or not re.search(pattern, href or ""):
            continue
        seen.add(link)
        items.append({"title": text[:140], "link": link, "source": name})
    return items


def gather(topic, today, seen, report=False):
    """Every candidate item for one section. Returns (items, status lines)."""
    items, notes = [], []

    def attempt(label, read):
        try:
            found = read()
            notes.append(f"{label}: {len(found)} item(s)")
            return found
        except Exception as error:
            notes.append(f"{label}: FAILED ({str(error)[:90]})")
            return []

    for name, url in topic.get("ical", []):
        items += attempt(name, lambda: read_ical(name, url, today))
    for name, url, pattern in topic.get("pages", []):
        items += attempt(name, lambda: read_page(name, url, pattern)[:25])
    for name, url in topic.get("feeds", []):
        items += attempt(name, lambda: [dict(a, source=name) for a in fetch_news.read_feed(url, 15)])
    for search in topic.get("searches", []):
        items += attempt(f"News search: {search[:50]}", lambda: fetch_news.read_feed(G(search), 10))

    # Drop repeats and (for news) anything already shown on an earlier day.
    fresh, titles = [], set()
    for item in items:
        key = re.sub(r"[^a-z0-9]", "", item["title"].lower())[:60] + item.get("sort", "")
        if not item["title"] or key in titles:
            continue
        if not item.get("when") and item["link"] in seen:
            continue
        titles.add(key)
        fresh.append(item)
    dated = sorted([i for i in fresh if i.get("when")], key=lambda i: i["sort"])
    fresh = dated + [i for i in fresh if not i.get("when")]
    if report:
        print(f"\n{topic['title']}")
        for line in notes:
            print("  " + line)
    return fresh[:MAX_CANDIDATES], notes


# ---------------------------------------------------------------------------
# Animal and quote of the day
# ---------------------------------------------------------------------------

def animal_of_the_day(state, today):
    """Pick the next animal not yet used and fetch its summary and photo from Wikipedia."""
    if state.get("animal_date") == str(today) and state.get("animal"):
        return state["animal"]
    used = state.setdefault("animals_used", [])
    names = [a for a in dict.fromkeys(ANIMALS) if a not in used]
    if not names:                 # Every animal has had its day: start the list again
        used.clear()
        names = list(dict.fromkeys(ANIMALS))
    # A fixed shuffle, so the order is mixed but never repeats until the list runs out.
    names.sort(key=lambda n: sum(ord(c) * (i + 7) for i, c in enumerate(n)) % 997)
    for name in names[:4]:
        animal = {"name": re.sub(r" \(.*\)$", "", name), "extract": "", "image": "", "tag": "",
                  "link": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(name.replace(" ", "_"))}
        try:
            animal.update(wiki_summary(name))
        except Exception:
            used.append(name)
            continue
        used.append(name)
        state.update(animal=animal, animal_date=str(today))
        return animal
    state.update(animal=animal, animal_date=str(today))   # Wikipedia unreachable: name only
    return animal


def wiki_summary(title):
    request = urllib.request.Request(WIKI + urllib.parse.quote(title.replace(" ", "_"), safe=""),
                                     headers={"User-Agent": "MiningDesk/1.0 (personal morning page)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    return {"name": data.get("title", title), "extract": data.get("extract", ""), "tag": data.get("description", ""),
            "image": (data.get("thumbnail") or {}).get("source", ""),
            "link": (data.get("content_urls") or {}).get("desktop", {}).get("page",
                     "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")))}


def place_of_the_day(state, today):
    if state.get("place_date") == str(today) and state.get("place"):
        return state["place"]
    used = state.setdefault("places_used", [])
    names = [p for p in dict.fromkeys(PLACES) if p not in used]
    if not names:
        used.clear()
        names = list(dict.fromkeys(PLACES))
    names.sort(key=lambda n: sum(ord(c) * (i + 3) for i, c in enumerate(n)) % 991)
    place = {"name": names[0], "extract": "", "image": "", "tag": "",
             "link": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(names[0].replace(" ", "_"))}
    for name in names[:4]:
        used.append(name)
        try:
            place = wiki_summary(name)
            break
        except Exception:
            continue
    state.update(place=place, place_date=str(today))
    return place


def long_read_candidates():
    """Recent pieces from the long-read magazines. Returns (items, status lines)."""
    items, notes = [], []
    for name, url in LONG_READS:
        try:
            found = [dict(a, source=name) for a in fetch_news.read_feed(url, 6) if not fetch_news.too_old(a.get("published", ""))]
            items += found
            notes.append(f"Long read · {name}: {len(found)} item(s)")
        except Exception as error:
            notes.append(f"Long read · {name}: FAILED ({str(error)[:80]})")
    return items, notes


def volunteer_pick(state, today):
    """This week's organisation (changes each Monday), plus any recent news about it."""
    week = str(today - timedelta(days=today.weekday()))
    if state.get("volunteer", {}).get("week") == week:
        return state["volunteer"], not state["volunteer"].get("rat")
    used = state.setdefault("volunteer_used", [])
    choices = [o for o in VOLUNTEER_ORGS if o[0] not in used]
    if not choices:
        used.clear()
        choices = list(VOLUNTEER_ORGS)
    name, where, what = choices[0]
    used.append(name)
    pick = {"week": week, "name": name, "where": where, "what": what,
            "link": "https://www.google.com/search?q=" + urllib.parse.quote_plus(f"{name} volunteer"), "news": []}
    try:
        pick["news"] = [{"title": a["title"], "link": a["link"], "source": a["source"]}
                        for a in fetch_news.read_feed(G(f'"{name}" when:60d'), 4)]
    except Exception:
        pass
    state["volunteer"] = pick
    return pick, True


def quote_of_the_day(today):
    author, text, source = QUOTES[(today.toordinal() * 7) % len(QUOTES)]
    return {"author": author, "text": text, "source": source}


def yearly_events(today):
    months = {today.month, today.month % 12 + 1}
    return [{"name": name, "where": where, "when": when,
             "link": "https://www.google.com/search?q=" + urllib.parse.quote_plus(f"{name} {where} {today.year} dates")}
            for name, where, in_months, when in YEARLY if months & set(in_months)]


# ---------------------------------------------------------------------------
# Feedback from the page (GitHub issues titled "[dan] ...", from the owner only)
# ---------------------------------------------------------------------------

def github(path, method="GET", body=None):
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "MiningDesk"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "null")


def read_feedback(password):
    """Returns (items, note). Each item is {'t': 'rate', ...} or {'t': 'ask', 'text': ...}.
    The page encrypts what it sends; anything that won't unlock is ignored."""
    if not os.environ.get("GITHUB_TOKEN") or not os.environ.get("GITHUB_REPOSITORY"):
        return [], "Feedback: not checked (this only works when GitHub runs the desk)"
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").lower()
    items, count = [], 0
    try:
        for issue in github("/issues?state=open&per_page=50"):
            if "pull_request" in issue or not issue.get("title", "").lower().startswith("[dan]"):
                continue
            if issue["user"]["login"].lower() != owner:
                continue                       # Anyone can open an issue on a public repository; only yours count
            body = issue.get("body") or ""
            try:
                box = json.loads(body[body.index("{"): body.rindex("}") + 1])
                found = json.loads(vault.unlock(box, password)).get("items", [])
            except ValueError:
                found = []        # Not from the page, or a different password
            # Replace the issue's text, so nothing readable stays on GitHub.
            github(f"/issues/{issue['number']}", "PATCH", {"body": "(applied)", "state": "closed"})
            items += [i for i in found if isinstance(i, dict)]
            count += 1
        return items, f"Feedback: {count} message(s) from the page, {len(items)} item(s)"
    except Exception as error:
        return items, f"Feedback: FAILED ({str(error)[:100]})"


def apply_commands(profile, reply):
    """Apply The Rat's commands (see prompts.DAN_REQUESTS) to the profile. Returns his reply to Dan."""
    by_id = {t["id"]: t for t in profile["topics"]}
    answer = ""
    for line in (reply or "").splitlines():
        parts = [p.strip() for p in line.strip().lstrip("-* ").split("|")]
        command = parts[0].upper()
        if command == "ADD" and len(parts) >= 3:
            new_id = re.sub(r"[^a-z0-9]+", "-", parts[1].lower()).strip("-")[:24] or f"topic{len(by_id) + 1}"
            if new_id not in by_id:
                topic = {"id": new_id, "title": parts[1][:80], "show": 4, "note": parts[3] if len(parts) > 3 else "",
                         "feeds": [], "searches": [s.strip() + " when:7d" for s in parts[2].split(";") if s.strip()][:4]}
                profile["topics"].append(topic)
                by_id[new_id] = topic
        elif command == "NOTE" and len(parts) >= 3 and parts[1] in by_id:
            by_id[parts[1]]["note"] = parts[2][:500]
        elif command == "SEARCH" and len(parts) >= 3 and parts[1] in by_id:
            searches = by_id[parts[1]].setdefault("searches", [])
            if len(searches) < 8:
                searches.append(parts[2][:150] + ("" if "when:" in parts[2] else " when:7d"))
        elif command == "DROP" and len(parts) >= 2 and parts[1] in by_id and parts[1] != "events":
            profile["topics"] = [t for t in profile["topics"] if t["id"] != parts[1]]
            by_id.pop(parts[1])
        elif command == "SIZE" and len(parts) >= 3 and parts[1] in by_id and parts[2].isdigit():
            by_id[parts[1]]["show"] = max(1, min(10, int(parts[2])))
        elif command == "STANDING" and len(parts) >= 2:
            profile["notes"] = (profile["notes"] + [parts[1][:300]])[-12:]
        elif command == "REPLY" and len(parts) >= 2:
            answer = " | ".join(parts[1:])
    return answer


def show_count(topic, ratings):
    """How many items a section shows: its size, nudged up or down by Dan's recent ratings."""
    recent = [r["score"] for r in ratings if r.get("topic") == topic["id"]][-15:]
    nudge = round(sum(recent) / len(recent) - 3) if len(recent) >= 3 else 0
    return max(1, min(12, topic.get("show", 4) + nudge))


# ---------------------------------------------------------------------------
# Building the page data
# ---------------------------------------------------------------------------

def build(ask, fill, prompts, data_dir, now, password, dry_run=False):
    """Gather everything, have The Rat choose, and return the data for the page.
    `ask(agent, task)` sends a task to an agent; `fill` fills a prompt's blanks."""
    folder = data_dir / "dan"
    profile = load(folder, "profile", password, None) or json.loads(json.dumps(DEFAULT_PROFILE))
    ratings = load(folder, "ratings", password, [])
    state = load(folder, "state", password, {"seen": []})
    today = now.date()
    status = []

    # 1. Feedback from the page: ratings are saved, messages go to The Rat.
    feedback, note = ([], "Feedback (dry run): none") if dry_run else read_feedback(password)
    status.append(note)
    for item in feedback:
        if item.get("t") == "rate" and str(item.get("score", "")).isdigit():
            ratings.append({"topic": item.get("topic", ""), "title": str(item.get("title", ""))[:140],
                            "score": max(1, min(5, int(item["score"]))), "date": str(today)})
    ratings = ratings[-RATINGS_KEPT:]
    messages = [str(i.get("text", ""))[:1500] for i in feedback if i.get("t") == "ask" and i.get("text")]
    if messages:
        reply = ask("curator", fill(prompts.DAN_REQUESTS,
            topics="\n".join(f"{t['id']}: {t['title']} (shows {t.get('show', 4)}). Note: {t.get('note', '')}"
                             for t in profile["topics"]),
            notes="\n".join("- " + n for n in profile["notes"]) or "- None.",
            messages="\n".join("- " + m for m in messages)))
        state["reply"] = {"date": str(today), "text": apply_commands(profile, reply), "asked": messages}

    # 2. Candidates for every section.
    seen = set(state.get("seen", []))
    sections, numbered, number = [], [], 1
    for topic in profile["topics"]:
        if dry_run:
            items = [{"title": f"Sample item {n} for {topic['title']}", "link": "#", "source": "Sample source",
                      **({"when": friendly_date(today + timedelta(days=3 * n)), "place": "Sample Hall"} if topic.get("kind") == "events" else {})}
                     for n in (1, 2, 3)]
        else:
            items, notes = gather(topic, today, seen)
            status += [f"{topic['id']} · {n}" for n in notes]
        lines = []
        for item in items:
            item["number"] = number
            extra = " · ".join(x for x in (item.get("when"), item.get("place"), item.get("source")) if x)
            lines.append(f"{number}. {item['title']} ({extra})" + (f" - {item['about']}" if item.get("about") else ""))
            number += 1
        sections.append({"topic": topic, "items": items, "show": show_count(topic, ratings)})
        numbered.append(f"SECTION \"{topic['title']}\" (choose up to {show_count(topic, ratings)}). "
                        f"What Dan wants here: {topic.get('note', '')}\n" + ("\n".join(lines) or "(nothing new today)"))

    # 3. The animal, the place, the volunteer spotlight and the long read.
    if dry_run:
        animal = {"name": "Okapi", "extract": "Sample extract about the okapi.", "image": "", "tag": "Sample", "link": "#"}
        place = {"name": "Socotra", "extract": "Sample extract about Socotra.", "image": "", "tag": "Sample", "link": "#"}
        volunteer, new_week = {"name": "Spokane Riverkeeper", "where": "Spokane", "what": "river cleanups",
                               "link": "#", "news": [], "week": "x"}, True
        reads = [{"title": f"Sample long read {n}", "link": "#", "source": "Aeon"} for n in (1, 2)]
    else:
        animal, place = animal_of_the_day(state, today), place_of_the_day(state, today)
        volunteer, new_week = volunteer_pick(state, today)
        reads = []
    week = str(today - timedelta(days=today.weekday()))
    if state.get("long_read", {}).get("week") != week and not reads:
        reads, notes = long_read_candidates()
        status += notes
    weekly = ""
    if reads:
        weekly += "\n\nLONG READ OF THE WEEK: choose the one piece most worth 20 minutes of Dan's week. Prefer something " \
                  "outside his usual interests that widens his view of the world. Candidates:\n" + "\n".join(
                      f"R{n}. {r['title']} ({r['source']})" for n, r in enumerate(reads, 1)) + \
                  "\nWrite it as: READ | R-number | two sentences on why it's worth his time"
    if new_week:
        news = "; ".join(a["title"] for a in volunteer.get("news", [])) or "none found"
        weekly += (f"\n\nVOLUNTEER SPOTLIGHT for this week: {volunteer['name']} ({volunteer['where']}): {volunteer['what']}. "
                   f"Recent news mentioning them: {news}.\nWrite it as: VOLUNTEER | two sentences on what they do and "
                   "how someone could help, using only the facts given here")

    # 4. The Rat chooses.
    rated = "\n".join(f"- {r['score']}/5: {r['title']} ({r['topic']})" for r in ratings[-40:]) or "- No ratings yet."
    reply = ask("curator", fill(prompts.DAN_CURATE, now=f"{now:%A, %B} {now.day}, {now.year}",
        notes="\n".join("- " + n for n in profile["notes"]) or "- None.", ratings=rated,
        candidates="\n\n".join(numbered), animal=animal["name"],
        animal_extract=animal["extract"] or "no description available; say only that you'd like to meet one",
        place=place["name"], place_extract=place["extract"] or "no description available", weekly=weekly))
    chosen = {}
    for line in reply.splitlines():
        parts = [p.strip() for p in line.strip().lstrip("-* ").split("|", 1)]
        if len(parts) == 2 and parts[0].rstrip(".").isdigit():
            chosen[int(parts[0].rstrip("."))] = parts[1]
        elif len(parts) == 2 and parts[0].upper() == "ANIMAL":
            animal["rat"] = parts[1]
        elif len(parts) == 2 and parts[0].upper() == "PLACE":
            place["rat"] = parts[1]
        elif len(parts) == 2 and parts[0].upper() == "VOLUNTEER":
            volunteer["rat"] = parts[1]
        elif len(parts) == 2 and parts[0].upper() == "READ":
            pick = [x.strip() for x in parts[1].split("|", 1)]
            number = pick[0].upper().lstrip("R").rstrip(".")
            if number.isdigit() and 1 <= int(number) <= len(reads):
                state["long_read"] = dict(reads[int(number) - 1], week=week, why=pick[1] if len(pick) > 1 else "")

    page_sections = []
    for s in sections:
        picked = [dict(i, line=chosen[i["number"]]) for i in s["items"] if i["number"] in chosen][:s["show"]]
        if s["topic"].get("kind") != "events":
            picked.sort(key=lambda i: list(chosen).index(i["number"]))
            state["seen"] = (state.get("seen", []) + [i["link"] for i in picked])[-3000:]
        page_sections.append({"id": s["topic"]["id"], "title": s["topic"]["title"], "items": picked,
                              "kind": s["topic"].get("kind", "news")})
    # Community events stay on top; the rest follow in Dan's order of interest (average rating).
    def interest(section):
        scores = [r["score"] for r in ratings if r.get("topic") == section["id"]][-15:]
        return -(sum(scores) / len(scores)) if len(scores) >= 3 else -3
    page_sections = ([s for s in page_sections if s["kind"] == "events"]
                     + sorted([s for s in page_sections if s["kind"] != "events"], key=interest))

    state["volunteer"] = volunteer if not dry_run else state.get("volunteer", volunteer)
    save(folder, "profile", profile, password)
    save(folder, "ratings", ratings, password)
    save(folder, "state", state, password)
    reply_note = state.get("reply") if state.get("reply", {}).get("date", "") >= str(today - timedelta(days=2)) else None
    return {"animal": animal, "place": place, "volunteer": volunteer, "long_read": state.get("long_read"),
            "quote": quote_of_the_day(today), "sections": page_sections,
            "yearly": yearly_events(today), "reply": reply_note, "status": status,
            "repo": os.environ.get("GITHUB_REPOSITORY", ""), "ratings_count": len(ratings)}


# ---------------------------------------------------------------------------
# Run on its own: check every source (uses a scratch folder; your data is untouched)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    today = date.today()
    for topic in DEFAULT_PROFILE["topics"]:
        items, _ = gather(topic, today, set(), report=True)
        for item in items[:4]:
            print(f"    - {item.get('when', '')} {item['title'][:80]}  ({item['source']})")
    animal = animal_of_the_day({}, today)
    print(f"\nAnimal of the day: {animal['name']} - {animal['extract'][:100] or 'NO DESCRIPTION (Wikipedia not reached)'}")
    place = place_of_the_day({}, today)
    print(f"Place of the day: {place['name']} - {place['extract'][:80] or 'NO DESCRIPTION'}")
    reads, notes = long_read_candidates()
    print("\n".join("  " + n for n in notes))
    pick, _ = volunteer_pick({}, today)
    print(f"Volunteer spotlight: {pick['name']} ({len(pick['news'])} recent news item(s))")
    quote = quote_of_the_day(today)
    print(f"Quote of the day: \"{quote['text'][:70]}...\" - {quote['author']}")
    print("Yearly events coming up:", ", ".join(e["name"] for e in yearly_events(today)) or "none this month or next")
