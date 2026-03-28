import sqlite3
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from config import DB_PATH


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_country TEXT,
                original_language TEXT,
                original_title TEXT NOT NULL,
                translated_title TEXT,
                ai_headline TEXT,
                url TEXT NOT NULL UNIQUE,
                published_at TEXT,
                crawled_at TEXT NOT NULL,
                summary TEXT,
                cluster_id INTEGER,
                importance_score REAL DEFAULT 0,
                active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT,
                top_headline TEXT,
                article_count INTEGER DEFAULT 1,
                importance_score REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crawl_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                sources_attempted INTEGER DEFAULT 0,
                sources_succeeded INTEGER DEFAULT 0,
                articles_found INTEGER DEFAULT 0,
                articles_new INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_articles_cluster ON articles(cluster_id);
            CREATE INDEX IF NOT EXISTS idx_articles_importance ON articles(importance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_crawled ON articles(crawled_at DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
            CREATE INDEX IF NOT EXISTS idx_clusters_importance ON clusters(importance_score DESC);
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_article(
    source_name: str,
    source_country: str,
    original_language: str,
    original_title: str,
    url: str,
    published_at: str | None = None,
    summary: str | None = None,
) -> int | None:
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO articles
                   (source_name, source_country, original_language,
                    original_title, url, published_at, crawled_at, summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_name,
                    source_country,
                    original_language,
                    original_title,
                    url,
                    published_at,
                    datetime.now(timezone.utc).isoformat(),
                    summary,
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def update_article_ai(article_id: int, translated_title: str, ai_headline: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE articles SET translated_title=?, ai_headline=?
               WHERE id=?""",
            (translated_title, ai_headline, article_id),
        )


def update_article_cluster(article_id: int, cluster_id: int, importance: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET cluster_id=?, importance_score=? WHERE id=?",
            (cluster_id, importance, article_id),
        )


def create_cluster(label: str, top_headline: str, article_count: int, importance: float) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO clusters (label, top_headline, article_count,
               importance_score, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (label, top_headline, article_count, importance, now, now),
        )
        return cur.lastrowid


def update_cluster(cluster_id: int, top_headline: str, article_count: int, importance: float):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE clusters SET top_headline=?, article_count=?,
               importance_score=?, updated_at=? WHERE id=?""",
            (top_headline, article_count, importance, now, cluster_id),
        )


def get_top_stories(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.id as cluster_id, c.top_headline, c.article_count,
                      c.importance_score, c.updated_at
               FROM clusters c
               WHERE c.importance_score > 0
               ORDER BY c.importance_score DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        results = []
        for row in rows:
            articles = conn.execute(
                """SELECT id, source_name, source_country, original_language,
                          original_title, translated_title, ai_headline,
                          url, published_at, importance_score
                   FROM articles
                   WHERE cluster_id = ?
                   ORDER BY importance_score DESC""",
                (row["cluster_id"],),
            ).fetchall()

            results.append({
                "cluster_id": row["cluster_id"],
                "headline": row["top_headline"],
                "article_count": row["article_count"],
                "importance": row["importance_score"],
                "updated_at": row["updated_at"],
                "articles": [dict(a) for a in articles],
            })

        return results


def get_recent_unprocessed(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, source_name, source_country, original_language,
                      original_title, url, published_at, summary
               FROM articles
               WHERE ai_headline IS NULL AND active = 1
               ORDER BY crawled_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_unclustered_articles(limit: int = 500) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, source_name, ai_headline, translated_title,
                      original_title, url, published_at, original_language
               FROM articles
               WHERE cluster_id IS NULL AND active = 1
                     AND ai_headline IS NOT NULL
               ORDER BY crawled_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_clustered_headlines() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, cluster_id, ai_headline
               FROM articles
               WHERE cluster_id IS NOT NULL AND active = 1
               ORDER BY crawled_at DESC
               LIMIT 2000""",
        ).fetchall()
        return [dict(r) for r in rows]


def log_crawl_start() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO crawl_log (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        return cur.lastrowid


def log_crawl_finish(log_id: int, attempted: int, succeeded: int, found: int, new: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE crawl_log SET finished_at=?, sources_attempted=?,
               sources_succeeded=?, articles_found=?, articles_new=?
               WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), attempted, succeeded, found, new, log_id),
        )


def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles WHERE active=1").fetchone()[0]
        clusters = conn.execute("SELECT COUNT(*) FROM clusters WHERE importance_score>0").fetchone()[0]
        last_crawl = conn.execute(
            "SELECT * FROM crawl_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_articles": total,
            "total_clusters": clusters,
            "last_crawl": dict(last_crawl) if last_crawl else None,
        }
