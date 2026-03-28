import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import HOST, PORT, CRAWL_INTERVAL_MINUTES, STATIC_DIR, TOP_STORIES_COUNT
from database import init_db, get_top_stories, get_stats
from crawler import crawl_all
from processor import run_processing_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")

scheduler = AsyncIOScheduler()


async def scheduled_crawl_and_process():
    logger.info("Scheduled crawl triggered")
    try:
        await crawl_all()
        run_processing_pipeline()
    except Exception as e:
        logger.error(f"Scheduled job failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized")

    scheduler.add_job(
        scheduled_crawl_and_process,
        "interval",
        minutes=CRAWL_INTERVAL_MINUTES,
        id="crawl_job",
        max_instances=1,
    )
    scheduler.start()
    logger.info(f"Scheduler started — crawl every {CRAWL_INTERVAL_MINUTES} min")

    asyncio.create_task(scheduled_crawl_and_process())

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down")


app = FastAPI(title="METAJORNAL", version="1.0", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/top")
async def api_top_stories(limit: int = TOP_STORIES_COUNT):
    stories = get_top_stories(min(limit, 100))
    return JSONResponse(content={"stories": stories, "count": len(stories)})


@app.get("/api/stats")
async def api_stats():
    return JSONResponse(content=get_stats())


@app.get("/api/sources")
async def api_sources():
    from sources import SOURCES
    out = [
        {"name": name, "feed": feed, "website": site, "language": lang, "country": country}
        for name, feed, site, lang, country in SOURCES
    ]
    return JSONResponse(content={"sources": out, "count": len(out)})


@app.post("/api/crawl")
async def api_trigger_crawl():
    asyncio.create_task(_manual_crawl())
    return JSONResponse(content={"status": "crawl started"})


async def _manual_crawl():
    try:
        result = await crawl_all()
        run_processing_pipeline()
        logger.info(f"Manual crawl complete: {result}")
    except Exception as e:
        logger.error(f"Manual crawl error: {e}")


# Static files mounted LAST so API routes take priority
app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False, log_level="info")
