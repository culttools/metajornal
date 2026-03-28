import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
import feedparser

from config import MAX_CONCURRENT_FEEDS, FEED_TIMEOUT_SECONDS
from sources import SOURCES
from database import insert_article, log_crawl_start, log_crawl_finish

logger = logging.getLogger("crawler")

FEED_DISCOVERY_PATHS = [
    "/feed/", "/feed", "/rss/", "/rss", "/rss.xml",
    "/atom.xml", "/index.xml", "/feeds/all",
    "/feed/rss/", "/feed/atom/",
]


async def fetch_feed(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=FEED_TIMEOUT_SECONDS)) as resp:
            if resp.status == 200:
                ct = resp.headers.get("content-type", "")
                text = await resp.text(errors="replace")
                if "xml" in ct or "rss" in ct or "atom" in ct or text.strip().startswith("<?xml") or "<rss" in text[:500]:
                    return text
    except Exception:
        pass
    return None


async def discover_feed(session: aiohttp.ClientSession, website: str) -> str | None:
    parsed = urlparse(website)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for path in FEED_DISCOVERY_PATHS:
        text = await fetch_feed(session, base + path)
        if text:
            return text
    return None


def parse_feed_entries(feed_text: str, source_name: str, language: str, country: str) -> list[dict]:
    parsed = feedparser.parse(feed_text)
    articles = []
    for entry in parsed.entries[:30]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue

        pub_date = None
        for date_field in ("published_parsed", "updated_parsed"):
            ts = entry.get(date_field)
            if ts:
                try:
                    pub_date = datetime(*ts[:6], tzinfo=timezone.utc).isoformat()
                except Exception:
                    pass
                break

        summary = ""
        if entry.get("summary"):
            import re
            summary = re.sub(r"<[^>]+>", "", entry.summary)[:500]

        articles.append({
            "source_name": source_name,
            "source_country": country,
            "original_language": language,
            "original_title": title,
            "url": link,
            "published_at": pub_date,
            "summary": summary or None,
        })
    return articles


async def crawl_source(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    name: str,
    feed_url: str,
    website: str,
    language: str,
    country: str,
) -> tuple[bool, int, int]:
    async with semaphore:
        feed_text = await fetch_feed(session, feed_url)
        if not feed_text:
            feed_text = await discover_feed(session, website)
        if not feed_text:
            logger.debug(f"[SKIP] {name} — no feed found")
            return False, 0, 0

        articles = parse_feed_entries(feed_text, name, language, country)
        new_count = 0
        for art in articles:
            art_id = insert_article(**art)
            if art_id is not None:
                new_count += 1

        logger.info(f"[OK] {name}: {len(articles)} articles, {new_count} new")
        return True, len(articles), new_count


async def crawl_all():
    log_id = log_crawl_start()
    logger.info(f"Starting crawl of {len(SOURCES)} sources...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FEEDS)
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_FEEDS, ssl=False)
    headers = {
        "User-Agent": "NokiaNewsBot/1.0 (news aggregator; +https://github.com/nokia-news)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    total_attempted = len(SOURCES)
    total_succeeded = 0
    total_found = 0
    total_new = 0

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [
            crawl_source(session, semaphore, name, feed_url, website, lang, country)
            for name, feed_url, website, lang, country in SOURCES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Crawl error: {result}")
                continue
            succeeded, found, new = result
            if succeeded:
                total_succeeded += 1
            total_found += found
            total_new += new

    log_crawl_finish(log_id, total_attempted, total_succeeded, total_found, total_new)
    logger.info(
        f"Crawl done: {total_succeeded}/{total_attempted} sources, "
        f"{total_found} articles found, {total_new} new"
    )
    return {
        "sources_attempted": total_attempted,
        "sources_succeeded": total_succeeded,
        "articles_found": total_found,
        "articles_new": total_new,
    }
