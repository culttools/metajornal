#!/usr/bin/env python3
"""
Static site generator for Metajornal.

Usage:
    python generate.py              # full crawl + process + export
    python generate.py --export     # export only (use existing DB)
    python generate.py --daily      # daily edition: crawl once, deeper processing
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from config import STATIC_DIR
from database import init_db, get_top_stories, get_stats
from sources import SOURCES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate")


def export_json(limit: int = 20):
    stories = get_top_stories(limit)
    stats = get_stats()

    sources_list = [
        {"name": name, "website": site, "language": lang, "country": country}
        for name, _, site, lang, country in SOURCES
    ]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stories": stories,
        "stats": stats,
        "sources": sources_list,
        "count": len(stories),
    }

    data_path = STATIC_DIR / "data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=None)

    sources_path = STATIC_DIR / "sources.json"
    with open(sources_path, "w", encoding="utf-8") as f:
        json.dump({"sources": sources_list, "count": len(sources_list)}, f, ensure_ascii=False)

    logger.info(f"Exported {len(stories)} stories + {len(sources_list)} sources")
    return data_path


async def main():
    init_db()
    export_only = "--export" in sys.argv

    if not export_only:
        from crawler import crawl_all
        from processor import run_processing_pipeline

        logger.info("=== CRAWL ===")
        result = await crawl_all()
        logger.info(f"Crawl result: {result}")

        logger.info("=== PROCESS ===")
        run_processing_pipeline()

    logger.info("=== EXPORT ===")
    export_json()
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
