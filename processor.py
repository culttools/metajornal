"""
Metajornal — Translation, headline generation, and story clustering.

CLUSTERING ALGORITHM
====================
The goal: detect when multiple *different* news outlets cover the same event,
then rank stories by how many independent sources report on it.

1. TRANSLATE — Non-English headlines → English via Google Translate.
2. HEADLINE  — Clean up to neutral wire-style (AI optional, rule-based fallback).
3. VECTORIZE — TF-IDF on English headlines (unigrams + bigrams, stop words removed).
4. CLUSTER   — For each new article, compute cosine similarity against all
               existing cluster centroids. If similarity >= threshold AND the
               article comes from a DIFFERENT source than existing cluster
               members, merge into that cluster. Otherwise, start a new cluster.
5. RANK      — Importance = number of UNIQUE sources in the cluster.
               Single-source clusters rank lowest. A story covered by
               The Guardian + Al Jazeera + Página/12 = importance 3.
6. HEADLINE SELECTION — Pick the shortest, most neutral headline from the
               cluster (preferring English-native sources) as the display headline.
"""

import logging
import re
from collections import defaultdict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import OPENAI_API_KEY, OPENAI_MODEL, SIMILARITY_THRESHOLD, TOP_STORIES_COUNT
from database import (
    get_recent_unprocessed,
    get_unclustered_articles,
    get_all_clustered_headlines,
    update_article_ai,
    update_article_cluster,
    create_cluster,
    update_cluster,
)

logger = logging.getLogger("processor")

# ── Translation ──────────────────────────────────────────────────────────────

def _looks_non_latin(text: str) -> bool:
    latin_count = sum(1 for c in text if c.isalpha() and ord(c) < 0x0250)
    alpha_count = sum(1 for c in text if c.isalpha())
    if alpha_count == 0:
        return False
    return (latin_count / alpha_count) < 0.5


def translate_text(text: str, source_lang: str) -> str:
    if not text:
        return text
    needs_translation = source_lang != "en" or _looks_non_latin(text)
    if not needs_translation:
        return text
    try:
        from deep_translator import GoogleTranslator
        lang = "auto" if (source_lang == "en" or len(source_lang) > 3) else source_lang
        result = GoogleTranslator(source=lang, target="en").translate(text)
        return result or text
    except Exception as e:
        try:
            from deep_translator import GoogleTranslator
            result = GoogleTranslator(source="auto", target="en").translate(text)
            return result or text
        except Exception:
            logger.debug(f"Translation failed for '{text[:50]}': {e}")
            return text


# ── Headline Generation ──────────────────────────────────────────────────────

HEADLINE_SYSTEM_PROMPT = """You are a senior wire-service editor. Rewrite the given headline to meet AP wire standards:
- Neutral, objective tone — no editorializing or advocacy language
- Active voice, present tense for immediacy
- Concise: ideally under 12 words
- Factual: preserve all key facts (who, what, where)
- No clickbait, no questions, no exclamation marks
- Remove source bias or loaded language
Return ONLY the rewritten headline, nothing else."""


def _ai_headline_openai(title: str) -> str | None:
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": HEADLINE_SYSTEM_PROMPT},
                {"role": "user", "content": title},
            ],
            max_tokens=60,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip().strip('"')
    except Exception as e:
        logger.error(f"OpenAI headline error: {e}")
        return None


def _cleanup_headline(title: str) -> str:
    title = re.sub(r"\s*[|–—:]\s*Opinion\b.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[|–—:]\s*Commentary\b.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[|–—]\s*[A-Z][\w\s]+$", "", title)
    title = title.replace("!", ".").replace("?!", "?")
    title = re.sub(r"\s{2,}", " ", title).strip()
    if title and title[-1] not in ".?":
        title = title.rstrip(".")
    return title


def generate_headline(original_title: str, language: str) -> tuple[str, str]:
    translated = translate_text(original_title, language)
    ai = _ai_headline_openai(translated)
    if not ai:
        ai = _cleanup_headline(translated)
    return translated, ai


# ── Process Unprocessed Articles ─────────────────────────────────────────────

def process_new_articles():
    total = 0
    while True:
        articles = get_recent_unprocessed(limit=200)
        if not articles:
            break

        logger.info(f"Processing batch of {len(articles)} articles ({total} done so far)...")
        for art in articles:
            try:
                translated, headline = generate_headline(
                    art["original_title"], art["original_language"]
                )
                update_article_ai(art["id"], translated, headline)
                total += 1
            except Exception as e:
                logger.error(f"Process error for article {art['id']}: {e}")
                update_article_ai(art["id"], art["original_title"], art["original_title"])
                total += 1

    logger.info(f"Processed {total} total articles with headlines")
    return total


# ── Clustering ───────────────────────────────────────────────────────────────

def cluster_articles():
    """
    Cluster articles by headline similarity, enforcing CROSS-SOURCE matching.
    Articles from the same source are never merged into the same cluster
    as a similarity match — they only end up together if they independently
    match the same cross-source cluster.
    """
    articles = get_unclustered_articles(limit=500)
    if not articles:
        logger.info("No unclustered articles")
        return

    existing = get_all_clustered_headlines()
    all_items = existing + articles

    if len(all_items) < 2:
        for art in articles:
            cid = create_cluster(
                label=art["ai_headline"],
                top_headline=art["ai_headline"],
                article_count=1,
                importance=1.0,
            )
            update_article_cluster(art["id"], cid, 1.0)
        return

    headlines = [
        item.get("ai_headline") or item.get("original_title", "")
        for item in all_items
    ]

    vectorizer = TfidfVectorizer(
        max_features=10000,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.9,
    )
    try:
        tfidf_matrix = vectorizer.fit_transform(headlines)
    except ValueError:
        logger.warning("TF-IDF failed — not enough terms")
        return

    n_existing = len(existing)
    new_indices = list(range(n_existing, len(all_items)))

    # Map: item_index → cluster_id
    cluster_map: dict[int, int] = {}
    for idx in range(n_existing):
        if all_items[idx].get("cluster_id"):
            cluster_map[idx] = all_items[idx]["cluster_id"]

    # Track which sources are already in each cluster
    cluster_sources: dict[int, set[str]] = defaultdict(set)
    for idx in range(n_existing):
        item = all_items[idx]
        if item.get("cluster_id"):
            cluster_sources[item["cluster_id"]].add(item.get("source_name", ""))

    for new_idx in new_indices:
        art = all_items[new_idx]
        art_source = art.get("source_name", "")
        best_sim = 0.0
        best_cluster_id = None

        # Compare against existing clustered articles
        if n_existing > 0:
            sims = cosine_similarity(
                tfidf_matrix[new_idx:new_idx+1], tfidf_matrix[:n_existing]
            )[0]

            for idx in np.argsort(sims)[::-1]:
                if sims[idx] < SIMILARITY_THRESHOLD:
                    break
                if idx not in cluster_map:
                    continue
                cid = cluster_map[idx]
                other_source = all_items[idx].get("source_name", "")
                # Only merge if it's a DIFFERENT source
                if other_source != art_source:
                    best_sim = sims[idx]
                    best_cluster_id = cid
                    break

        # Compare against other new articles already processed this round
        for other_new_idx in new_indices:
            if other_new_idx >= new_idx:
                break
            if other_new_idx not in cluster_map:
                continue
            other_source = all_items[other_new_idx].get("source_name", "")
            if other_source == art_source:
                continue
            sim = cosine_similarity(
                tfidf_matrix[new_idx:new_idx+1],
                tfidf_matrix[other_new_idx:other_new_idx+1],
            )[0][0]
            if sim > best_sim and sim >= SIMILARITY_THRESHOLD:
                best_sim = sim
                best_cluster_id = cluster_map[other_new_idx]

        if best_cluster_id is not None:
            cluster_map[new_idx] = best_cluster_id
            cluster_sources[best_cluster_id].add(art_source)
            update_article_cluster(art["id"], best_cluster_id, best_sim)
        else:
            cid = create_cluster(
                label=art.get("ai_headline", art["original_title"]),
                top_headline=art.get("ai_headline", art["original_title"]),
                article_count=1,
                importance=1.0,
            )
            cluster_map[new_idx] = cid
            cluster_sources[cid].add(art_source)
            update_article_cluster(art["id"], cid, 1.0)

    _recalculate_cluster_scores()
    logger.info(f"Clustered {len(new_indices)} new articles")


def _pick_best_headline(articles: list[dict]) -> str:
    """
    Pick the best headline for a cluster:
    prefer English-native sources, then shortest clear headline.
    """
    if not articles:
        return "Untitled"

    scored = []
    for a in articles:
        h = a.get("ai_headline") or a.get("original_title") or ""
        if not h or len(h) < 10:
            continue
        score = 0
        if a.get("original_language") == "en":
            score += 10
        if len(h) < 80:
            score += 5
        if not any(c in h for c in "?!…"):
            score += 3
        # Penalize headlines that look like section labels
        if h.isupper() or len(h.split()) < 4:
            score -= 10
        scored.append((score, h))

    if not scored:
        return articles[0].get("ai_headline") or "Untitled"

    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def _recalculate_cluster_scores():
    """
    Recalculate importance for ALL clusters.
    Importance = unique_source_count ^ 1.5
    Single-source clusters get importance = 0.5 (rank below multi-source).
    """
    from database import get_conn
    with get_conn() as conn:
        clusters = conn.execute(
            "SELECT id FROM clusters WHERE importance_score >= 0"
        ).fetchall()

        for cluster_row in clusters:
            cid = cluster_row["id"]
            rows = conn.execute(
                """SELECT id, source_name, ai_headline, original_language,
                          original_title, published_at
                   FROM articles WHERE cluster_id = ? AND active = 1
                   ORDER BY published_at DESC""",
                (cid,),
            ).fetchall()

            if not rows:
                continue

            articles = [dict(r) for r in rows]
            unique_sources = set(a["source_name"] for a in articles)
            n_unique = len(unique_sources)

            if n_unique >= 2:
                importance = n_unique ** 1.5
            else:
                importance = 0.5

            best_headline = _pick_best_headline(articles)

            for r in rows:
                art_score = importance * (0.9 if r["source_name"] == articles[0]["source_name"] else 0.7)
                conn.execute(
                    "UPDATE articles SET importance_score=? WHERE id=?",
                    (art_score, r["id"]),
                )

            conn.execute(
                """UPDATE clusters SET top_headline=?, article_count=?,
                   importance_score=?, updated_at=datetime('now')
                   WHERE id=?""",
                (best_headline, n_unique, importance, cid),
            )


# ── Full Pipeline ────────────────────────────────────────────────────────────

def run_processing_pipeline():
    logger.info("=== Starting processing pipeline ===")
    processed = process_new_articles()
    cluster_articles()
    logger.info("=== Processing pipeline complete ===")
    return processed
