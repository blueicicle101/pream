"""
TMDB Metadata Enrichment Service for Pream.
Cleans titles, searches TMDB API, fetches posters, plots, ratings, genres,
and optionally reorganizes movies/series into '<LANG> | <GENRE>' display groups.
"""
import asyncio
import difflib
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.movie import Movie
from app.models.series import Series
from app.models.setting import Setting
from app.services.gemini import (
    extract_title_with_gemini,
    get_gemini_api_key,
    get_gemini_rate_delay,
    is_gemini_enabled,
    is_staggered_gemini_calls_enabled,
)
from app.services.task_status import set_task_status

logger = logging.getLogger(__name__)

# Cancellation flag and active task tracker for TMDB enrichment
_tmdb_cancel_requested: bool = False
_tmdb_active_task: Optional[asyncio.Task] = None


def request_tmdb_cancellation() -> None:
    """Signal active TMDB enrichment to cancel immediately."""
    global _tmdb_cancel_requested, _tmdb_active_task
    _tmdb_cancel_requested = True
    if _tmdb_active_task and not _tmdb_active_task.done():
        try:
            _tmdb_active_task.cancel()
        except Exception:
            pass


def is_tmdb_cancelled() -> bool:
    """Check if cancellation has been requested for TMDB enrichment."""
    global _tmdb_cancel_requested
    return _tmdb_cancel_requested


def reset_tmdb_cancellation() -> None:
    """Reset cancellation state before starting a new run."""
    global _tmdb_cancel_requested
    _tmdb_cancel_requested = False


def register_active_tmdb_task(task: asyncio.Task) -> None:
    """Register current asyncio task running TMDB enrichment."""
    global _tmdb_active_task
    _tmdb_active_task = task


TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Common title noise tags to strip for cleaner search queries
NOISE_PATTERNS = [
    r"\b(?:1080p|720p|2160p|4k|uhd|fhd|hd|sd|hevc|x264|x265|bluray|web-dl|webrip|dvdrip|aac|ac3|ddp5\.1)\b",
    r"\[.*?\]",
    r"\(.*?\)",
    r"[._-]",
]

YEAR_REGEX = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Robust language prefix regex: matches standalone codes or brackets/delimiters like [EN], |EN|, EN |, EN -
LANG_PREFIX_REGEX = re.compile(r"(?:^|[\[\|\(\s_-])([A-Z]{2,3})(?:[\]\|\)\s_:-]|$)", re.IGNORECASE)

GENRE_MAP = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Sci-Fi",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
    # TV Genres
    10759: "Action & Adventure",
    10762: "Kids",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
}


def clean_media_title(raw_name: str) -> Tuple[str, Optional[int]]:
    """
    Extract a clean title and optional 4-digit release year from media name.
    Example: 'Avatar.2009.1080p.BluRay.x264' -> ('Avatar', 2009)
    """
    text = raw_name.strip()

    # Search for year
    year = None
    year_match = YEAR_REGEX.search(text)
    if year_match:
        try:
            year = int(year_match.group(1))
            # If the year is preceded by '(' or '[', include that opening bracket in the cut
            start_idx = year_match.start()
            if start_idx > 0 and text[start_idx - 1] in "([":
                start_idx -= 1
            text = text[:start_idx]
        except ValueError:
            year = None

    # Strip noise words
    cleaned = re.sub(r"\[.*?\]", "", text)
    cleaned = re.sub(r"\(.*?\)", "", cleaned)
    cleaned = re.sub(
        r"\b(?:1080p|720p|2160p|4k|uhd|fhd|hd|sd|hevc|x264|x265|bluray|web-dl|webrip|dvdrip|aac|ac3)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[._-]", " ", cleaned)
    cleaned = re.sub(r"[\(\[\{\)\]\}]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or raw_name, year


def is_latin_script(text: Optional[str]) -> bool:
    """
    Check if a text string is in readable Latin script (including accented characters
    like é, à, ü, ñ, etc., as well as digits, spaces, and common punctuation).
    Returns False for non-Latin scripts such as Devanagari (चाँदनी), Cyrillic (Брат),
    Han/Kanji/CJK (千と千尋の神隠し / 寄生虫), Arabic, Hangul, Greek, Hebrew, Thai, etc.
    """
    if not text:
        return True
    has_letters = False
    for ch in text:
        if ch.isalpha():
            has_letters = True
            try:
                name = unicodedata.name(ch, "")
                if "LATIN" not in name:
                    return False
            except Exception:
                return False
    return has_letters or bool(text.strip())


def choose_best_display_title(
    localized_title: Optional[str],
    original_title: Optional[str],
    fallback_title: Optional[str] = None,
) -> str:
    """
    Select the best human-readable display title:
    1. If localized_title (English) is present and in Latin script, use it (e.g. 'Chandni', 'Spirited Away').
    2. If localized_title is non-Latin but original_title is in Latin script (e.g. French 'Le Fabuleux Destin d\'Amélie Poulain'), keep it.
    3. If both are non-Latin (Devanagari, Cyrillic, Kanji), prefer the localized Latin title if available; else fallback_title.
    Safety Net: Never returns an empty or whitespace-only string; guarantees a fallback title.
    """
    clean_loc = (localized_title or "").strip()
    clean_orig = (original_title or "").strip()

    if clean_loc and is_latin_script(clean_loc):
        return clean_loc

    if clean_orig and is_latin_script(clean_orig):
        return clean_orig

    if clean_loc:
        return clean_loc

    if clean_orig:
        return clean_orig

    return (fallback_title or "").strip() or "Untitled"


def sanitize_display_name_and_group(
    candidate_name: Optional[str],
    fallback_name: Optional[str],
    candidate_group: Optional[str],
    fallback_group: Optional[str],
) -> Tuple[str, str]:
    """
    Safety net to guarantee that display_name and display_group are NEVER left empty or blank.
    If enrichment generates an empty/null candidate, falls back safely to previous or raw values.
    """
    name = (candidate_name or "").strip()
    if not name:
        name = (fallback_name or "").strip() or "Untitled"

    group = (candidate_group or "").strip()
    if not group:
        group = (fallback_group or "").strip() or "Uncategorized"

    return name, group


def detect_language_code(group_name: Optional[str]) -> str:
    """
    Detect language prefix code from group name.
    Supports formats like:
      - 'EN | Movies' -> 'EN'
      - '[UK] Action 4K' -> 'UK'
      - '|FR| Comedies' -> 'FR'
      - 'NL - Series' -> 'NL'
    Defaults to 'EN' if not recognized.
    """
    if not group_name:
        return "EN"
    clean = group_name.strip()
    match = LANG_PREFIX_REGEX.search(clean)
    if match:
        code = match.group(1).upper()
        if code in ("EN", "UK", "US", "NL", "DE", "FR", "ES", "IT", "PT", "AR", "RU", "TR", "IN", "PL", "GR", "SE", "NO", "DK", "FI"):
            return code
    return "EN"


def is_tmdb_auto_group_enabled(db: Optional[Session] = None) -> bool:
    """Check if auto-grouping by Language & Genre is enabled."""
    if db:
        db_setting = db.query(Setting).filter(Setting.key == "tmdb_auto_group").first()
        if db_setting and db_setting.value:
            return db_setting.value.lower() in ("true", "1", "yes")
    return getattr(settings, "TMDB_AUTO_GROUP", True)


def is_tmdb_enabled(db: Session) -> bool:
    """Check if TMDB enrichment is globally enabled in DB settings or .env configuration."""
    db_setting = db.query(Setting).filter(Setting.key == "tmdb_enabled").first()
    if db_setting and db_setting.value:
        return db_setting.value.lower() in ("true", "1", "yes")
    return getattr(settings, "TMDB_ENABLED", True)


def get_tmdb_rate_limit(db: Session) -> float:
    """Get the request delay (seconds) between TMDB API calls (default 0.05s)."""
    db_setting = db.query(Setting).filter(Setting.key == "tmdb_rate_limit").first()
    if db_setting and db_setting.value:
        try:
            return max(0.0, float(db_setting.value))
        except ValueError:
            pass
    return getattr(settings, "TMDB_RATE_LIMIT", 0.05)


def get_tmdb_api_key(db: Session) -> str:
    """Retrieve TMDB API key from DB settings or .env configuration."""
    db_setting = db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
    if db_setting and db_setting.value:
        return db_setting.value.strip()
    return settings.TMDB_API_KEY.strip()


def select_best_tmdb_candidate(
    query_title: str,
    target_year: Optional[int],
    results: List[Dict[str, Any]],
    is_tv: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Select the most accurate candidate from TMDB results by prioritizing:
    1. Release year matching within ±1 year (prevents "$Positions" 2026 matching "21 Positions" 2023).
       If target_year is provided and year-matching candidates exist, candidates with mismatched years (>1 diff) are excluded.
    2. Exact title match (query equals title or original_title).
    3. String similarity ratio between query and candidate title/original_title.
    """
    if not results:
        return None

    norm_query = query_title.strip().lower()
    # Also strip non-alphanumeric for a normalized comparison (e.g. "$positions" -> "positions")
    norm_query_alphanum = re.sub(r"[^\w\s]", "", norm_query).strip()

    valid_candidates = []
    for item in results:
        date_str = item.get("first_air_date" if is_tv else "release_date") or ""
        cand_year = None
        if len(date_str) >= 4 and date_str[:4].isdigit():
            cand_year = int(date_str[:4])

        item_title = (item.get("name" if is_tv else "title") or "").strip().lower()
        orig_title = (item.get("original_name" if is_tv else "original_title") or "").strip().lower()

        # Compute title similarity
        score_title = difflib.SequenceMatcher(None, norm_query, item_title).ratio()
        score_orig = difflib.SequenceMatcher(None, norm_query, orig_title).ratio()
        best_sim = max(score_title, score_orig)

        # Bonus for exact or alphanum-exact match
        if item_title == norm_query or orig_title == norm_query:
            best_sim += 0.5
        elif item_title == norm_query_alphanum or orig_title == norm_query_alphanum:
            best_sim += 0.4
        elif item_title.startswith(norm_query) or orig_title.startswith(norm_query):
            best_sim += 0.2

        year_diff = None
        if target_year and cand_year:
            year_diff = abs(target_year - cand_year)

        valid_candidates.append({
            "item": item,
            "year": cand_year,
            "year_diff": year_diff,
            "score": best_sim,
            "popularity": float(item.get("popularity", 0.0) or 0.0),
        })

    # If target_year was provided, prefer candidates within ±1 year tolerance
    if target_year:
        year_matched = [c for c in valid_candidates if c["year_diff"] is not None and c["year_diff"] <= 1]
        if year_matched:
            # Sort by score desc, then popularity desc
            year_matched.sort(key=lambda x: (x["score"], x["popularity"]), reverse=True)
            return year_matched[0]["item"]
        # If no candidates matched the year, check if the top candidate has high exact title match
        exact_title_matches = [c for c in valid_candidates if c["score"] >= 1.4]
        if exact_title_matches:
            exact_title_matches.sort(key=lambda x: (x["score"], x["popularity"]), reverse=True)
            return exact_title_matches[0]["item"]
        return None

    # No target_year provided: sort by score desc, then popularity desc
    valid_candidates.sort(key=lambda x: (x["score"], x["popularity"]), reverse=True)
    return valid_candidates[0]["item"]


async def search_movie_tmdb(
    title: str,
    year: Optional[int],
    api_key: str,
    language: Optional[str] = None,
    return_all: bool = False,
) -> Any:
    """Search TMDB for a movie by title and optional year and language."""
    if not api_key:
        return [] if return_all else None

    params = {"api_key": api_key, "query": title, "include_adult": False}
    if year:
        params["year"] = year
    if language:
        params["language"] = language

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TMDB_BASE_URL}/search/movie", params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if return_all:
                    return results
                if results:
                    return select_best_tmdb_candidate(title, year, results, is_tv=False)
    except Exception as e:
        logger.warning(f"TMDB movie search failed for '{title}': {e}")
    return [] if return_all else None


async def search_tv_tmdb(
    title: str,
    year: Optional[int],
    api_key: str,
    language: Optional[str] = None,
    return_all: bool = False,
) -> Any:
    """Search TMDB for a TV series by title and optional first air date year and language."""
    if not api_key:
        return [] if return_all else None

    params = {"api_key": api_key, "query": title, "include_adult": False}
    if year:
        params["first_air_date_year"] = year
    if language:
        params["language"] = language

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TMDB_BASE_URL}/search/tv", params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if return_all:
                    return results
                if results:
                    return select_best_tmdb_candidate(title, year, results, is_tv=True)
    except Exception as e:
        logger.warning(f"TMDB TV search failed for '{title}': {e}")
    return [] if return_all else None


async def enrich_movie(movie: Movie, api_key: str, db: Optional[Session] = None) -> bool:
    """
    Enrich a single movie record with TMDB metadata.
    Uses regex cleaner first; falls back to Gemini Flash-Lite if available and needed.
    """
    clean_title, year = clean_media_title(movie.raw_name)
    result = await search_movie_tmdb(clean_title, year or movie.year, api_key)

    ai_lang_code = None
    # If regex search yields no match and Gemini API key is available, attempt AI extraction
    gemini_key = get_gemini_api_key(db) if is_gemini_enabled(db) else None
    if not result and gemini_key:
        stagger = is_staggered_gemini_calls_enabled(db)
        delay = get_gemini_rate_delay(db)
        ai_data = await extract_title_with_gemini(
            raw_title=movie.raw_name,
            raw_group=movie.raw_group or movie.display_group,
            media_type="movie",
            api_key=gemini_key,
            stagger=stagger,
            delay_seconds=delay,
        )
        if ai_data and ai_data.get("clean_title"):
            clean_title = ai_data["clean_title"]
            year = ai_data.get("year") or year
            ai_lang_code = ai_data.get("language_code")
            tmdb_lang = ai_data.get("tmdb_language")
            # Retry TMDB search with AI-extracted title and language
            result = await search_movie_tmdb(clean_title, year or movie.year, api_key, language=tmdb_lang)
            if not result and tmdb_lang:
                # Retry without strict language filter in case TMDB indexing is in original language
                result = await search_movie_tmdb(clean_title, year or movie.year, api_key)

    now = datetime.now(timezone.utc)
    movie.tmdb_fetched_at = now

    if not result:
        return False

    movie.tmdb_id = result.get("id")
    # Update display name to the clean readable title (English/Latin preferred for non-Latin scripts)
    official_title = choose_best_display_title(
        localized_title=result.get("title"),
        original_title=result.get("original_title"),
        fallback_title=clean_title,
    )
    if official_title:
        movie.display_name = official_title

    poster_path = result.get("poster_path")
    if poster_path:
        movie.poster_url = f"{TMDB_IMAGE_BASE}{poster_path}"

    backdrop_path = result.get("backdrop_path")
    if backdrop_path:
        movie.backdrop_url = f"{TMDB_IMAGE_BASE}{backdrop_path}"

    movie.plot = result.get("overview")
    movie.rating = result.get("vote_average")

    release_date = result.get("release_date")
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        movie.year = int(release_date[:4])

    genre_ids = result.get("genre_ids", [])
    genre_names = [GENRE_MAP.get(gid) for gid in genre_ids if GENRE_MAP.get(gid)]
    if genre_names:
        movie.genre = ", ".join(genre_names)
        if is_tmdb_auto_group_enabled(db):
            primary_genre = genre_names[0]
            # Use AI detected language code if available, else group prefix detection
            lang = ai_lang_code or detect_language_code(movie.raw_group or movie.display_group)
            movie.display_group = f"{lang} | {primary_genre}"

    # Safety Net: Guarantee display_name and display_group never become blank
    movie.display_name, movie.display_group = sanitize_display_name_and_group(
        candidate_name=movie.display_name,
        fallback_name=movie.raw_name,
        candidate_group=movie.display_group,
        fallback_group=movie.raw_group,
    )

    return True


async def enrich_series(series: Series, api_key: str, db: Optional[Session] = None) -> bool:
    """
    Enrich a single TV series record with TMDB metadata.
    Uses regex cleaner first; falls back to Gemini Flash-Lite if available and needed.
    """
    clean_title, year = clean_media_title(series.raw_name)
    result = await search_tv_tmdb(clean_title, year or series.year, api_key)

    ai_lang_code = None
    gemini_key = get_gemini_api_key(db) if is_gemini_enabled(db) else None
    if not result and gemini_key:
        stagger = is_staggered_gemini_calls_enabled(db)
        delay = get_gemini_rate_delay(db)
        ai_data = await extract_title_with_gemini(
            raw_title=series.raw_name,
            raw_group=series.raw_group or series.display_group,
            media_type="series",
            api_key=gemini_key,
            stagger=stagger,
            delay_seconds=delay,
        )
        if ai_data and ai_data.get("clean_title"):
            clean_title = ai_data["clean_title"]
            year = ai_data.get("year") or year
            ai_lang_code = ai_data.get("language_code")
            tmdb_lang = ai_data.get("tmdb_language")
            result = await search_tv_tmdb(clean_title, year or series.year, api_key, language=tmdb_lang)
            if not result and tmdb_lang:
                result = await search_tv_tmdb(clean_title, year or series.year, api_key)

    now = datetime.now(timezone.utc)
    series.tmdb_fetched_at = now

    if not result:
        return False

    series.tmdb_id = result.get("id")
    # Update display name to the clean readable series name (English/Latin preferred for non-Latin scripts)
    official_name = choose_best_display_title(
        localized_title=result.get("name"),
        original_title=result.get("original_name"),
        fallback_title=clean_title,
    )
    if official_name:
        series.display_name = official_name

    poster_path = result.get("poster_path")
    if poster_path:
        series.poster_url = f"{TMDB_IMAGE_BASE}{poster_path}"

    series.plot = result.get("overview")
    series.rating = result.get("vote_average")

    first_air = result.get("first_air_date")
    if first_air and len(first_air) >= 4 and first_air[:4].isdigit():
        series.year = int(first_air[:4])

    genre_ids = result.get("genre_ids", [])
    genre_names = [GENRE_MAP.get(gid) for gid in genre_ids if GENRE_MAP.get(gid)]
    if genre_names:
        series.genre = ", ".join(genre_names)
        if is_tmdb_auto_group_enabled(db):
            primary_genre = genre_names[0]
            lang = ai_lang_code or detect_language_code(series.raw_group or series.display_group)
            series.display_group = f"{lang} | {primary_genre}"

    # Safety Net: Guarantee display_name and display_group never become blank
    series.display_name, series.display_group = sanitize_display_name_and_group(
        candidate_name=series.display_name,
        fallback_name=series.raw_name,
        candidate_group=series.display_group,
        fallback_group=series.raw_group,
    )

    return True


async def enrich_pending_media(
    db: Session,
    batch_size: int = 50,
    continuous: bool = False,
    max_items: Optional[int] = None,
    group: Optional[str] = None,
    content_type: Optional[str] = None,  # "vod" or "series" or None (both)
) -> Dict[str, Any]:
    """
    Enrich movies and series missing TMDB metadata.
    Supports continuous loop until entire catalog is processed or max_items reached.
    Supports scoping to a specific category group and/or content_type.
    Respects rate limits by staggering calls and updates real-time task progress.
    """
    if not is_tmdb_enabled(db):
        return {"status": "skipped", "reason": "TMDB enrichment is disabled in settings"}

    api_key = get_tmdb_api_key(db)
    if not api_key:
        return {"status": "skipped", "reason": "No TMDB API key configured"}

    rate_delay = get_tmdb_rate_limit(db)

    # Base movie query filter
    movie_filter = [Movie.tmdb_fetched_at == None, Movie.is_hidden == False]
    if group:
        movie_filter.append(Movie.display_group == group)
    
    # Base series query filter
    series_filter = [Series.tmdb_fetched_at == None, Series.is_hidden == False]
    if group:
        series_filter.append(Series.display_group == group)

    should_include_movies = content_type in (None, "all", "vod", "movie", "movies")
    should_include_series = content_type in (None, "all", "series", "tv")

    total_pending_movies = db.query(Movie).filter(*movie_filter).count() if should_include_movies else 0
    total_pending_series = db.query(Series).filter(*series_filter).count() if should_include_series else 0
    catalog_total = total_pending_movies + total_pending_series

    if catalog_total == 0:
        scope_msg = f" for group '{group}'" if group else ""
        set_task_status("completed", "TMDB Enrichment", f"All media already enriched{scope_msg}", 100)
        return {
            "status": "ok",
            "movies_processed": 0,
            "movies_enriched": 0,
            "series_processed": 0,
            "series_enriched": 0,
            "catalog_remaining": 0,
        }

    target_total = min(catalog_total, max_items) if max_items else (catalog_total if (continuous or group) else min(catalog_total, batch_size))
    scope_desc = f" in group '{group}'" if group else ""
    reset_tmdb_cancellation()
    set_task_status("running", "TMDB Enrichment", f"Starting enrichment for {target_total} items{scope_desc}...", 5)

    total_movies_processed = 0
    total_movies_enriched = 0
    total_series_processed = 0
    total_series_enriched = 0
    processed_count = 0
    was_cancelled = False

    chunk_size = min(batch_size, 100)

    while True:
        # Check cancellation request
        if is_tmdb_cancelled():
            was_cancelled = True
            logger.info("TMDB enrichment cancelled by user request.")
            break

        # Check how many more we need
        if max_items and processed_count >= max_items:
            break

        current_batch_limit = min(chunk_size, max_items - processed_count) if max_items else chunk_size

        # Query pending movies
        pending_movies = []
        if should_include_movies:
            pending_movies = (
                db.query(Movie)
                .filter(*movie_filter)
                .order_by(Movie.id.asc())
                .limit(current_batch_limit)
                .all()
            )

        remaining_budget = max(0, current_batch_limit - len(pending_movies)) if should_include_movies else current_batch_limit
        pending_series = []
        if should_include_series and remaining_budget > 0:
            pending_series = (
                db.query(Series)
                .filter(*series_filter)
                .order_by(Series.id.asc())
                .limit(remaining_budget)
                .all()
            )

        if not pending_movies and not pending_series:
            # Everything in catalog/group is enriched!
            break

        # Process movies batch
        for movie in pending_movies:
            if is_tmdb_cancelled():
                was_cancelled = True
                break

            processed_count += 1
            total_movies_processed += 1
            pct = max(5, min(95, int((processed_count / max(1, target_total)) * 95)))
            clean_title, _ = clean_media_title(movie.raw_name)
            set_task_status(
                "running",
                "TMDB Enrichment",
                f"Enriching [{processed_count}/{target_total}]: '{clean_title}'...",
                pct,
            )
            if await enrich_movie(movie, api_key, db=db):
                total_movies_enriched += 1
                dup_count = await sync_duplicate_movies(movie, db)
                total_movies_enriched += dup_count

            if rate_delay > 0:
                await asyncio.sleep(rate_delay)

        if was_cancelled:
            db.commit()
            break

        # Process series batch
        for s in pending_series:
            if is_tmdb_cancelled():
                was_cancelled = True
                break

            processed_count += 1
            total_series_processed += 1
            pct = max(5, min(95, int((processed_count / max(1, target_total)) * 95)))
            clean_title, _ = clean_media_title(s.raw_name)
            set_task_status(
                "running",
                "TMDB Enrichment",
                f"Enriching [{processed_count}/{target_total}]: '{clean_title}'...",
                pct,
            )
            if await enrich_series(s, api_key, db=db):
                total_series_enriched += 1
                dup_count = await sync_duplicate_series(s, db)
                total_series_enriched += dup_count

            if rate_delay > 0:
                await asyncio.sleep(rate_delay)

        # Commit batch to database incrementally so progress persists
        db.commit()

        if was_cancelled:
            break

        # If not running continuously and not bounded by max_items and not group-scoped, stop after first batch
        if not continuous and not max_items and not group:
            break

    # Recalculate remaining
    remaining_movies = (
        db.query(Movie)
        .filter(*movie_filter)
        .count()
    ) if should_include_movies else 0
    remaining_series = (
        db.query(Series)
        .filter(*series_filter)
        .count()
    ) if should_include_series else 0

    status_title = "Cancelled" if was_cancelled else "Complete"
    status_code = "cancelled" if was_cancelled else "completed"
    msg = f"Enriched {total_movies_enriched} movies, {total_series_enriched} series. {remaining_movies + remaining_series} remaining."
    if was_cancelled:
        msg = f"Enrichment stopped by user: {msg}"
    logger.info(f"TMDB enrichment finished: {msg}")
    set_task_status(status_code, status_title, msg, 100 if not was_cancelled else pct)

    return {
        "status": "cancelled" if was_cancelled else "ok",
        "movies_processed": total_movies_processed,
        "movies_enriched": total_movies_enriched,
        "series_processed": total_series_processed,
        "series_enriched": total_series_enriched,
        "catalog_remaining": remaining_movies + remaining_series,
    }


async def sync_duplicate_movies(movie: Movie, db: Session) -> int:
    """
    Copy TMDB enrichment attributes from the enriched movie to all unenriched
    duplicates having the exact same raw_name and raw_group.
    Returns the count of duplicates updated.
    """
    if not movie or not movie.tmdb_id:
        return 0

    duplicates = (
        db.query(Movie)
        .filter(
            Movie.id != movie.id,
            Movie.raw_name == movie.raw_name,
            Movie.raw_group == movie.raw_group,
            Movie.tmdb_fetched_at == None,
        )
        .all()
    )

    for dup in duplicates:
        dup.tmdb_id = movie.tmdb_id
        dup.tmdb_fetched_at = movie.tmdb_fetched_at
        dup.display_name, dup.display_group = sanitize_display_name_and_group(
            candidate_name=movie.display_name,
            fallback_name=dup.raw_name,
            candidate_group=movie.display_group,
            fallback_group=dup.raw_group,
        )
        dup.poster_url = movie.poster_url
        dup.backdrop_url = movie.backdrop_url
        dup.plot = movie.plot
        dup.rating = movie.rating
        dup.year = movie.year
        dup.genre = movie.genre

    if duplicates:
        logger.info(f"Synchronized TMDB metadata to {len(duplicates)} exact duplicate movie(s) for '{movie.raw_name}'")
    return len(duplicates)


async def sync_duplicate_series(series: Series, db: Session) -> int:
    """
    Copy TMDB enrichment attributes from the enriched series to all unenriched
    duplicates having the exact same raw_name and raw_group.
    Returns the count of duplicates updated.
    """
    if not series or not series.tmdb_id:
        return 0

    duplicates = (
        db.query(Series)
        .filter(
            Series.id != series.id,
            Series.raw_name == series.raw_name,
            Series.raw_group == series.raw_group,
            Series.tmdb_fetched_at == None,
        )
        .all()
    )

    for dup in duplicates:
        dup.tmdb_id = series.tmdb_id
        dup.tmdb_fetched_at = series.tmdb_fetched_at
        dup.display_name, dup.display_group = sanitize_display_name_and_group(
            candidate_name=series.display_name,
            fallback_name=dup.raw_name,
            candidate_group=series.display_group,
            fallback_group=dup.raw_group,
        )
        dup.poster_url = series.poster_url
        dup.plot = series.plot
        dup.rating = series.rating
        dup.year = series.year
        dup.genre = series.genre

    if duplicates:
        logger.info(f"Synchronized TMDB metadata to {len(duplicates)} exact duplicate series for '{series.raw_name}'")
    return len(duplicates)


async def enrich_single_movie(movie_id: int, db: Session) -> Dict[str, Any]:
    """Enrich a single specific movie by its database ID."""
    if not is_tmdb_enabled(db):
        return {"status": "skipped", "reason": "TMDB enrichment is disabled"}
    api_key = get_tmdb_api_key(db)
    if not api_key:
        return {"status": "skipped", "reason": "No TMDB API key configured"}

    movie = db.query(Movie).filter(Movie.id == movie_id).first()
    if not movie:
        return {"status": "error", "reason": f"Movie {movie_id} not found"}

    matched = await enrich_movie(movie, api_key, db=db)
    synced_count = 0
    if matched:
        synced_count = await sync_duplicate_movies(movie, db)
    db.commit()
    return {
        "status": "ok" if matched else "not_found",
        "movie_id": movie.id,
        "title": movie.display_name,
        "matched": matched,
        "duplicates_synced": synced_count,
        "poster_url": movie.poster_url,
        "plot": movie.plot,
        "rating": movie.rating,
        "genre": movie.genre,
        "display_group": movie.display_group,
    }


async def enrich_single_series(series_id: int, db: Session) -> Dict[str, Any]:
    """Enrich a single specific TV series by its database ID."""
    if not is_tmdb_enabled(db):
        return {"status": "skipped", "reason": "TMDB enrichment is disabled"}
    api_key = get_tmdb_api_key(db)
    if not api_key:
        return {"status": "skipped", "reason": "No TMDB API key configured"}

    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return {"status": "error", "reason": f"Series {series_id} not found"}

    matched = await enrich_series(series, api_key, db=db)
    synced_count = 0
    if matched:
        synced_count = await sync_duplicate_series(series, db)
    db.commit()
    return {
        "status": "ok" if matched else "not_found",
        "series_id": series.id,
        "title": series.display_name,
        "matched": matched,
        "duplicates_synced": synced_count,
        "poster_url": series.poster_url,
        "plot": series.plot,
        "rating": series.rating,
        "genre": series.genre,
        "display_group": series.display_group,
    }


async def search_tmdb_candidates(
    query: str,
    media_type: str = "movie",
    year: Optional[int] = None,
    language: Optional[str] = None,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search TMDB for candidate matches (movie or series) with poster thumbnails,
    overview, rating, and release date.
    """
    if not api_key:
        return []

    endpoint = "tv" if media_type == "series" else "movie"
    params: Dict[str, Any] = {"api_key": api_key, "query": query, "include_adult": False}
    if year:
        if media_type == "series":
            params["first_air_date_year"] = year
        else:
            params["year"] = year
    if language:
        params["language"] = language

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TMDB_BASE_URL}/search/{endpoint}", params=params)
            if resp.status_code == 200:
                raw_results = resp.json().get("results", [])
                candidates = []
                for item in raw_results[:12]:
                    title = item.get("title") if media_type != "series" else item.get("name")
                    orig_title = item.get("original_title") if media_type != "series" else item.get("original_name")
                    release = item.get("release_date") if media_type != "series" else item.get("first_air_date")
                    poster = item.get("poster_path")
                    candidates.append({
                        "id": item.get("id"),
                        "title": title or orig_title or "Unknown",
                        "original_title": orig_title,
                        "release_date": release or "",
                        "year": release[:4] if release and len(release) >= 4 else None,
                        "poster_url": f"{TMDB_IMAGE_BASE}{poster}" if poster else None,
                        "vote_average": item.get("vote_average"),
                        "overview": item.get("overview") or "",
                    })
                return candidates
    except Exception as e:
        logger.warning(f"Error searching TMDB candidates for '{query}': {e}")
    return []


async def apply_tmdb_id_match(
    media_type: str,
    item_id: int,
    tmdb_id: int,
    db: Session,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Directly apply a chosen TMDB ID match to a movie or series.
    Fetches full metadata from TMDB and updates the item's display_name, poster,
    plot, rating, year, genre, and display_group.
    """
    api_key = get_tmdb_api_key(db)
    if not api_key:
        return {"status": "error", "reason": "No TMDB API key configured"}

    endpoint = "tv" if media_type in ("series", "tv") else "movie"
    params: Dict[str, Any] = {"api_key": api_key}
    if language:
        params["language"] = language

    detail = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}", params=params)
            if resp.status_code == 200:
                detail = resp.json()
            else:
                return {"status": "error", "reason": f"TMDB returned status {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "reason": f"Failed to contact TMDB: {e}"}

    if not detail:
        return {"status": "error", "reason": "No data returned by TMDB for this ID"}

    now = datetime.now(timezone.utc)

    if media_type in ("series", "tv"):
        series = db.query(Series).filter(Series.id == item_id).first()
        if not series:
            return {"status": "error", "reason": f"Series {item_id} not found"}

        series.tmdb_id = detail.get("id")
        series.tmdb_fetched_at = now

        name = choose_best_display_title(
            localized_title=detail.get("name"),
            original_title=detail.get("original_name"),
            fallback_title=series.display_name,
        )
        if name:
            series.display_name = name

        poster = detail.get("poster_path")
        if poster:
            series.poster_url = f"{TMDB_IMAGE_BASE}{poster}"

        series.plot = detail.get("overview")
        series.rating = detail.get("vote_average")

        first_air = detail.get("first_air_date")
        if first_air and len(first_air) >= 4 and first_air[:4].isdigit():
            series.year = int(first_air[:4])

        genres = [g.get("name") for g in detail.get("genres", []) if g.get("name")]
        if genres:
            series.genre = ", ".join(genres)
            if is_tmdb_auto_group_enabled(db):
                lang = detect_language_code(series.raw_group or series.display_group)
                series.display_group = f"{lang} | {genres[0]}"

        # Safety Net: Guarantee display_name and display_group never become blank
        series.display_name, series.display_group = sanitize_display_name_and_group(
            candidate_name=series.display_name,
            fallback_name=series.raw_name,
            candidate_group=series.display_group,
            fallback_group=series.raw_group,
        )

        # Sync exact duplicate series across sources
        dup_count = await sync_duplicate_series(series, db)
        db.commit()
        return {
            "status": "ok",
            "item_id": series.id,
            "title": series.display_name,
            "duplicates_synced": dup_count,
            "poster_url": series.poster_url,
            "year": series.year,
            "rating": series.rating,
            "genre": series.genre,
            "display_group": series.display_group,
        }

    else:
        movie = db.query(Movie).filter(Movie.id == item_id).first()
        if not movie:
            return {"status": "error", "reason": f"Movie {item_id} not found"}

        movie.tmdb_id = detail.get("id")
        movie.tmdb_fetched_at = now

        title = choose_best_display_title(
            localized_title=detail.get("title"),
            original_title=detail.get("original_title"),
            fallback_title=movie.display_name,
        )
        if title:
            movie.display_name = title

        poster = detail.get("poster_path")
        if poster:
            movie.poster_url = f"{TMDB_IMAGE_BASE}{poster}"

        backdrop = detail.get("backdrop_path")
        if backdrop:
            movie.backdrop_url = f"{TMDB_IMAGE_BASE}{backdrop}"

        movie.plot = detail.get("overview")
        movie.rating = detail.get("vote_average")

        release = detail.get("release_date")
        if release and len(release) >= 4 and release[:4].isdigit():
            movie.year = int(release[:4])

        genres = [g.get("name") for g in detail.get("genres", []) if g.get("name")]
        if genres:
            movie.genre = ", ".join(genres)
            if is_tmdb_auto_group_enabled(db):
                lang = detect_language_code(movie.raw_group or movie.display_group)
                movie.display_group = f"{lang} | {genres[0]}"

        # Safety Net: Guarantee display_name and display_group never become blank
        movie.display_name, movie.display_group = sanitize_display_name_and_group(
            candidate_name=movie.display_name,
            fallback_name=movie.raw_name,
            candidate_group=movie.display_group,
            fallback_group=movie.raw_group,
        )

        # Sync exact duplicate movies across sources
        dup_count = await sync_duplicate_movies(movie, db)
        db.commit()
        return {
            "status": "ok",
            "item_id": movie.id,
            "title": movie.display_name,
            "duplicates_synced": dup_count,
            "poster_url": movie.poster_url,
            "year": movie.year,
            "rating": movie.rating,
            "genre": movie.genre,
            "display_group": movie.display_group,
        }
