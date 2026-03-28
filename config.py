import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "nokia_news.db"

DATA_DIR.mkdir(exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

CRAWL_INTERVAL_MINUTES = int(os.getenv("CRAWL_INTERVAL", "30"))
MAX_CONCURRENT_FEEDS = int(os.getenv("MAX_CONCURRENT_FEEDS", "50"))
FEED_TIMEOUT_SECONDS = int(os.getenv("FEED_TIMEOUT", "15"))

TOP_STORIES_COUNT = 20
SIMILARITY_THRESHOLD = 0.45

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8088"))
