#!/usr/bin/env python3
"""
Anshu's Tech Deck
=================
Fetches the latest articles from a list of RSS feeds, sorts them into
Smartphones / India / AI & Tech / General, removes duplicate stories, finds a
thumbnail image for each, and writes a single self-contained web page to
`public/index.html`.

It uses ONLY the feeds' own content — no paid APIs and no API keys. Paywalled
sources (e.g. WSJ) still show their headline plus the short summary the feed
provides, clearly marked "subscriber-only".

You normally don't run this by hand: a GitHub Action runs it every few hours
and publishes the result. To preview locally:

    python3 -m venv .venv
    . .venv/bin/activate          # Windows: .venv\\Scripts\\activate
    pip install -r requirements.txt
    python build.py               # writes public/index.html
    open public/index.html        # macOS  (Windows: start / Linux: xdg-open)

To change what appears on the site, edit the SOURCES list and the CATEGORY
keyword lists below — nothing else needs to change.
"""

from __future__ import annotations

import calendar
import concurrent.futures
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 1. SOURCES  —  edit this list to add or remove feeds.
#    name        : shown on the card badge
#    url         : the RSS/Atom feed address
#    paywall     : True  -> card is marked "subscriber-only"
#    india_source: True  -> every story from this source also shows under India
#    enabled     : False -> kept for reference but skipped (feed blocked / no feed)
# ---------------------------------------------------------------------------
SOURCES = [
    # ---- working direct feeds --------------------------------------------
    {"name": "LiveMint",         "url": "https://www.livemint.com/rss/technology",                              "india_source": True},
    {"name": "Economic Times",   "url": "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",      "india_source": True},
    {"name": "FoneArena",        "url": "https://www.fonearena.com/blog/feed/",                                 "india_source": True},
    {"name": "Smartprix",        "url": "https://www.smartprix.com/bytes/feed/",                                "india_source": True},
    {"name": "GSMArena",         "url": "https://www.gsmarena.com/rss-news-reviews.php3"},
    {"name": "Android Central",  "url": "https://www.androidcentral.com/feed"},
    {"name": "Android Headlines","url": "https://www.androidheadlines.com/feed"},
    {"name": "Android Authority","url": "https://www.androidauthority.com/feed/"},
    {"name": "MacRumors",        "url": "https://feeds.macrumors.com/MacRumors-All"},
    {"name": "AppleInsider",     "url": "https://appleinsider.com/rss/news/"},
    {"name": "Samsung Newsroom", "url": "https://news.samsung.com/global/feed"},
    {"name": "The Verge",        "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "Techmeme",         "url": "https://www.techmeme.com/feed.xml"},
    {"name": "Hacker News",      "url": "https://news.ycombinator.com/rss"},
    # ---- paywalled, but its feed still gives us a real summary to show -----
    # Financial Times (subscription): live feed with a useful one-line standfirst
    # we surface as the "tailored summary" so you get the gist without a login.
    {"name": "Financial Times",  "url": "https://www.ft.com/technology?format=rss",  "paywall": True},

    # ---- disabled: feed is blocked / dead / does not exist (kept so you can retry later)
    # WSJ's public RSS feeds are frozen on old (Jan 2025) content — no longer live.
    {"name": "WSJ Tech",         "url": "https://feeds.a.dj.com/rss/RSSWSJD.xml",     "paywall": True, "enabled": False},
    # Apple's own newsroom feed 404s and its aggregator alternative returns mostly
    # Apple TV/Arcade PR; Apple hardware news is well covered by MacRumors + AppleInsider.
    {"name": "Apple Newsroom",   "url": "https://www.apple.com/newsroom/rss/newsroom.rss",                       "enabled": False},
    # Business Standard blocks feed access (HTTP 403).
    {"name": "Business Standard","url": "https://www.business-standard.com/rss/technology-108.rss", "india_source": True, "enabled": False},
    # Reuters retired its public RSS feeds (HTTP 404).
    {"name": "Reuters Tech",     "url": "https://www.reutersagency.com/feed/?best-topics=tech",                  "enabled": False},
    # Bloomberg has no public feed; only aggregator headlines exist (no clean link/summary).
    {"name": "Bloomberg",        "url": "",                                          "paywall": True,            "enabled": False},
]

# ---------------------------------------------------------------------------
# 2. CATEGORY RULES  —  case-insensitive keyword match on title + summary.
# ---------------------------------------------------------------------------
# 2. CATEGORY RULES  —  whole-word keyword match on title + summary.
#    A keyword only counts as a whole word: "ai" matches "AI" but not "said",
#    "trai" matches "TRAI" but not "training", "ios" matches "iOS" but not
#    "studios". A plural "s" is allowed automatically ("chip" -> "chips").
#    Keywords are case-insensitive, EXCEPT ones you type with a capital letter:
#    those must match exactly — e.g. "Vi" (the Indian carrier) won't catch the
#    "VI" in "GTA VI".
# ---------------------------------------------------------------------------
KW_PHONE = [
    "apple", "iphone", "ipad", "samsung", "galaxy", "oneplus", "oppo", "vivo",
    "xiaomi", "redmi", "realme", "poco", "pixel", "nothing phone", "nothing os",
    "nothing ear", "cmf phone", "carl pei", "motorola", "moto", "huawei", "honor",
    "foldable", "snapdragon", "mediatek", "tensor", "android", "ios",
    "smartphone", "phone", "camera phone", "battery",
]
KW_INDIA = [
    "india", "indian", "₹", "rupee", "lakh", "crore", "bis certification",
    "bis listing", "bureau of indian standards", "uidai", "aadhaar", "jio",
    "jiohotstar", "airtel", "vodafone idea", "Vi", "trai",
]
KW_AI = [
    "ai", "genai", "artificial intelligence", "llm", "gpt", "chatgpt", "gemini",
    "claude", "anthropic", "openai", "chip", "chipmaker", "semiconductor", "gpu",
    "fab", "data center", "datacenter", "quantum", "policy", "regulation",
    "antitrust",
]
# ---------------------------------------------------------------------------
# 3. SETTINGS
# ---------------------------------------------------------------------------
PER_FEED_LIMIT   = 25     # newest N entries taken from each feed
MAX_ARTICLES     = 120    # total cards on the page after de-duplication
SUMMARY_CHARS    = 200    # trim card summaries to about this length
OG_IMAGE_BUDGET  = 110    # max article pages to open to hunt a missing thumbnail
FEED_TIMEOUT     = 20
OG_TIMEOUT       = 8
OUTPUT           = Path("public/index.html")
TEMPLATE         = Path("template.html")
CACHE_PATH       = Path(".cache/ai_summaries.json")   # reuse AI summaries so frequent refreshes don't re-pay
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ---------------------------------------------------------------------------
# AI SUMMARIES for paywalled sources (optional).
# If an ANTHROPIC_API_KEY is available, each subscriber-only story gets a short
# AI-written "what this is about" summary so you get the gist without the
# paywall. With no key, the card gracefully falls back to the feed's own teaser.
# Switch AI_MODEL to "claude-haiku-4-5" for the lowest cost per refresh.
# ---------------------------------------------------------------------------
AI_MODEL = "claude-opus-4-8"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(msg, flush=True)
# Per-feed results for this run, shown as a table on the GitHub Actions run page.
FEED_REPORT: list[dict] = []
IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def feed_ok(name: str, count: int) -> None:
    """Record a feed that delivered stories."""
    FEED_REPORT.append({"name": name, "count": count, "note": ""})
    log(f"  · {name:18} — {count} entries")


def feed_problem(name: str, why: str) -> None:
    """Record a feed that gave us nothing. On GitHub this also becomes a yellow
    warning in the run's Annotations box, so a dead feed can't hide behind a
    green tick."""
    FEED_REPORT.append({"name": name, "count": 0, "note": why})
    log(f"  · {name:18} — {why} (skipped)")
    if IN_GITHUB_ACTIONS:
        print(f"::warning title=Feed problem: {name}::{name} returned no stories this run ({why}).",
              flush=True)


def write_run_summary(articles, seconds: float) -> None:
    """On GitHub, show a small feed-health table right on the run's Summary
    page, so you can see which sources worked without opening the logs."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    working = sum(1 for f in FEED_REPORT if f["count"])
    lines = [
        f"### Tech Deck: {len(articles)} stories published",
        f"{working} of {len(FEED_REPORT)} feeds delivered stories · built in {seconds:.0f}s",
        "",
        "| Feed | Result |",
        "|---|---|",
    ]
    for f in sorted(FEED_REPORT, key=lambda f: (f["count"] > 0, f["name"])):
        result = f"{f['count']} stories" if f["count"] else f"⚠️ {f['note']}"
        lines.append(f"| {f['name']} | {result} |")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass

def clean_text(raw: str, limit: int | None = None) -> str:
    """Strip HTML, unescape entities, collapse whitespace, optionally trim."""
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:—-")
        text = cut + "…"
    return text


def entry_datetime(entry) -> datetime:
    """Best-available publish time as an aware UTC datetime."""
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc)


def find_feed_image(entry) -> str | None:
    """Look for a thumbnail inside the feed entry itself (no extra request)."""
    # media:content / media:thumbnail
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            url = m.get("url")
            if url:
                return url
    # enclosure links
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and "image" in (link.get("type") or ""):
            if link.get("href"):
                return link["href"]
    # first <img> inside content/summary HTML
    blobs = []
    if entry.get("content"):
        blobs.append(entry["content"][0].get("value", ""))
    blobs.append(entry.get("summary", ""))
    for blob in blobs:
        if blob and "<img" in blob:
            img = BeautifulSoup(blob, "html.parser").find("img")
            src = img and (img.get("src") or img.get("data-src"))
            if src and src.startswith("http"):
                return src
    return None


def fetch_og_image(url: str) -> str | None:
    """Open an article page and read its share-image meta tag (og:image etc.)."""
    try:
        r = SESSION.get(url, timeout=OG_TIMEOUT, allow_redirects=True,
                        headers={"Referer": "https://www.google.com/"})
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            return None
        soup = BeautifulSoup(r.text[:250_000], "html.parser")
        for attr, key in (("property", "og:image"), ("property", "og:image:secure_url"),
                          ("property", "og:image:url"), ("name", "twitter:image"),
                          ("name", "twitter:image:src")):
            tag = soup.find("meta", attrs={attr: key})
            if tag and tag.get("content", "").startswith("http"):
                return tag["content"]
        # last resort: an explicit <link rel="image_src">
        link = soup.find("link", attrs={"rel": "image_src"})
        if link and link.get("href", "").startswith("http"):
            return link["href"]
    except requests.RequestException:
        return None
    return None


def keyword_matcher(words):
    """Turn a keyword list into one whole-word test (see CATEGORY RULES).

    The old plain-substring check misfiled lots of stories: "ai" is inside
    "said" and "again", "trai" inside "training" (so AI stories got an India
    flag), "oppo" inside "opportunity", "ios" inside "studios"."""
    def pattern(word):
        body = r"\s+".join(re.escape(part) for part in word.split())
        left = r"(?<!\w)" if re.match(r"\w", word[0]) else ""        # "₹" needs no edge
        right = r"(?:e?s)?(?!\w)" if re.match(r"\w", word[-1]) else ""
        return left + body + right

    loose = [w for w in words if w == w.lower()]
    exact = [w for w in words if w != w.lower()]               # typed with capitals
    checks = []
    if loose:
        checks.append(re.compile("|".join(map(pattern, loose)), re.IGNORECASE))
    if exact:
        checks.append(re.compile("|".join(map(pattern, exact))))
    return lambda text: any(rx.search(text) for rx in checks)


IS_PHONE = keyword_matcher(KW_PHONE)
IS_INDIA = keyword_matcher(KW_INDIA)
IS_AI    = keyword_matcher(KW_AI)


def categorize(title: str, summary: str, india_source: bool):
    """Return (primary_category, india_flag) per the category rules."""
    blob = f"{title} {summary}"
    is_phone = IS_PHONE(blob)
    is_india = india_source or IS_INDIA(blob)
    is_ai    = IS_AI(blob)

    if is_phone:
        primary = "phone"
    elif is_india:
        primary = "india"
    elif is_ai:
        primary = "ai"
    else:
        primary = "general"
    return primary, is_india


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def refine_summary(summary: str, title: str) -> str:
    """Drop low-value summaries (common with discovery feeds like Techmeme / HN):
    strip an 'Author / Publication : ' lead-in, then blank it out if what's left
    is just the headline again or too short to add anything."""
    s = summary.strip()
    # Techmeme-style attribution always has a space before the colon ("CNBC : ",
    # "Meir Orbach / CTech : "); normal prose writes "Word:" with no leading space.
    m = re.match(r"^([^:]{3,60})\s:\s(.+)$", s)
    if m and not re.search(r"[.!?]", m.group(1)):
        s = m.group(2).strip()
    if len(s) < 25:
        return ""
    ns, nt = normalize_title(s), normalize_title(title)
    if nt and (nt in ns or ns.startswith(nt[:40])):      # summary repeats the title
        return ""
    return s


def find_duplicate(norm: str, url: str, kept: list, kept_tokens: list) -> int:
    """Index of an already-kept story this one duplicates, or -1 if it's new."""
    tokens = set(norm.split())
    for i, k in enumerate(kept):
        if k["url"] == url or normalize_title(k["title"]) == norm:
            return i
        if len(tokens) >= 5:
            prev = kept_tokens[i]
            overlap = len(tokens & prev)
            union = len(tokens | prev)
            if union and overlap / union >= 0.72:    # near-identical headline
                return i
    return -1


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def collect_articles():
    articles = []
    active = [s for s in SOURCES if s.get("enabled", True) and s.get("url")]
    log(f"Fetching {len(active)} feeds…")

    for src in active:
        try:
            resp = SESSION.get(src["url"], timeout=FEED_TIMEOUT)
            feed = feedparser.parse(resp.content)
            entries = feed.entries[:PER_FEED_LIMIT]
            if not entries:
                feed_problem(src["name"], f"HTTP {resp.status_code}" if resp.status_code >= 400 else "feed was empty or unreadable")
                continue
            for e in entries:
                title = clean_text(e.get("title", ""))
                link = e.get("link", "")
                if not title or not link:
                    continue
                summary = clean_text(e.get("summary", "") or
                                     (e.get("content", [{}])[0].get("value", "") if e.get("content") else ""),
                                     SUMMARY_CHARS)
                cat, india = categorize(title, summary, src.get("india_source", False))
                summary = refine_summary(summary, title)
                articles.append({
                    "title": title,
                    "url": link,
                    "source": src["name"],
                    "summary": summary,
                    "img": find_feed_image(e),
                    "cat": cat,
                    "india": india,
                    "paywall": src.get("paywall", False),
                    "_dt": entry_datetime(e),
                })
            feed_ok(src["name"], len(entries))
        except requests.RequestException as ex:
            feed_problem(src["name"], f"could not connect: {type(ex).__name__}")
    return articles


def dedupe_and_sort(articles):
    articles.sort(key=lambda a: a["_dt"], reverse=True)   # newest first
    kept, kept_tokens = [], []
    for a in articles:
        norm = normalize_title(a["title"])
        dup = find_duplicate(norm, a["url"], kept, kept_tokens)
        if dup >= 0:
            # Same story from another source: if the version we kept has no
            # image but this duplicate does, borrow it.
            if not kept[dup]["img"] and a["img"]:
                kept[dup]["img"] = a["img"]
            continue
        kept.append(a)
        kept_tokens.append(set(norm.split()))
        if len(kept) >= MAX_ARTICLES:
            break
    return kept


def backfill_images(articles):
    """For cards still missing an image, open the article page for its og:image."""
    missing = [a for a in articles if not a["img"]][:OG_IMAGE_BUDGET]
    if not missing:
        return
    log(f"Hunting thumbnails for {len(missing)} image-less stories…")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda a: (a, fetch_og_image(a["url"])), missing)
        for a, img in results:
            if img:
                a["img"] = img


def ai_summarize_paywalled(articles):
    """For subscriber-only stories, replace the feed teaser with a short
    AI-written summary so you get the gist without the paywall.

    A tiny on-disk cache (keyed by article URL) means each refresh only pays to
    summarize *new* paywalled stories and reuses the rest — so refreshing often
    stays cheap. AI writing runs only when an ANTHROPIC_API_KEY is present;
    without it, already-cached summaries are still reused and anything new falls
    back to the feed teaser."""
    paywalled = [a for a in articles if a["paywall"]]
    if not paywalled:
        return

    # Load previously-written summaries.
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cache = {}

    # 1) Reuse any cached summary for free (no API call).
    from_cache = 0
    for a in paywalled:
        cached = cache.get(a["url"])
        if cached:
            a["summary"], a["ai"] = cached, True
            from_cache += 1

    todo = [a for a in paywalled if not a.get("ai")]

    # 2) Write summaries for anything new — needs the API key.
    written = 0
    if todo and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            system = (
                "You are a tech-news editor for an Indian audience. Write a tight, "
                "factual 2-3 sentence summary of a paywalled article so a reader gets "
                "the gist without a subscription. Use only the headline, the teaser, and "
                "well-established public context. Never invent specific figures, quotes, "
                "dates, or events that aren't clearly implied. No preamble, no markdown."
            )
            for a in todo:
                prompt = (f"Source: {a['source']}\nHeadline: {a['title']}\n"
                          f"Teaser: {a['summary'] or '(none provided)'}\n\nWrite the summary.")
                try:
                    resp = client.messages.create(
                        model=AI_MODEL, max_tokens=220, system=system,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = "".join(b.text for b in resp.content if b.type == "text").strip()
                    if text:
                        a["summary"], a["ai"] = clean_text(text, 300), True
                        written += 1
                except Exception as ex:   # never let a summary failure break the build
                    log(f"  AI summary failed for '{a['title'][:40]}…' ({type(ex).__name__})")
        except ImportError:
            log("The 'anthropic' package isn't installed — new paywalled cards keep the feed teaser.")
    elif todo:
        log(f"No ANTHROPIC_API_KEY — {len(todo)} new paywalled cards keep the feed teaser.")

    # 3) Save the cache as just the current paywalled summaries (keeps it small).
    new_cache = {a["url"]: a["summary"] for a in paywalled if a.get("ai")}
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(new_cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    log(f"AI summaries: {from_cache} reused from cache, {written} newly written "
        f"({len(paywalled)} paywalled stories, model {AI_MODEL}).")


def choose_lead(articles):
    """Mark the newest story that has an image as the big lead card."""
    for a in articles:
        if a["img"]:
            a["lead"] = True
            return
    if articles:
        articles[0]["lead"] = True


def render_site(articles):
    build_dt = datetime.now(timezone.utc)
    source_count = sum(1 for s in SOURCES if s.get("enabled", True) and s.get("url"))

    payload = []
    for a in articles:
        payload.append({
            "title": a["title"],
            "url": a["url"],
            "source": a["source"],
            "summary": a["summary"],
            "img": a["img"],
            "cat": a["cat"],
            "india": a["india"],
            "paywall": a["paywall"],
            "ai": a.get("ai", False),
            "ts": a["_dt"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **({"lead": True} if a.get("lead") else {}),
        })

    template = TEMPLATE.read_text(encoding="utf-8")
    stories_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    footer = f"{source_count} SOURCES · INCL. TECHMEME + HACKER NEWS FOR DISCOVERY"

    page = (template
            .replace("__STORIES__", stories_json)
            .replace("__BUILD_ISO__", build_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
            .replace("__SOURCE_COUNT__ SOURCES · INCL. TECHMEME + HACKER NEWS FOR DISCOVERY", footer))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")


def main():
    start = time.time()
    articles = collect_articles()
    if not articles:
        log("No articles fetched — is the network reachable? Aborting without "
            "overwriting the existing page.")
        sys.exit(1)

    articles = dedupe_and_sort(articles)
    backfill_images(articles)
    ai_summarize_paywalled(articles)
    choose_lead(articles)
    render_site(articles)

    with_img = sum(1 for a in articles if a["img"])
    by_cat = {}
    for a in articles:
        by_cat[a["cat"]] = by_cat.get(a["cat"], 0) + 1
    log(f"\nWrote {OUTPUT} — {len(articles)} stories "
        f"({with_img} with images) in {time.time() - start:.1f}s")
    log(f"By category: {by_cat}")
    write_run_summary(articles, time.time() - start)


if __name__ == "__main__":
    main()
