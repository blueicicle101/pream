"""
TMDB Metadata Enrichment Service for Pream.
Cleans titles, searches TMDB API, fetches posters, plots, ratings, genres,
and optionally reorganizes movies/series into '<LANG> | <GENRE>' display groups.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.movie import Movie
from app.models.series import Series
from app.models.setting import Setting
from app.services.task_status import set_task_status

logger = logging.getLogger(__name__)

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
LANG_PREFIX_REGEX = re.compile(r"^([A-Z]{2,3})\b", re.IGNORECASE)

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


def detect_language_code(group_name: Optional[str]) -> str:
    """Detect language prefix code from group name (e.g. 'EN | Movies' -> 'EN')."""
    if not group_name:
        return "EN"
    match = LANG_PREFIX_REGEX.match(group_name.strip())
    if match:
        code = match.group(1).upper()
        if code in ("EN", "UK", "US", "NL", "DE", "FR", "ES", "IT", "PT", "AR", "RU", "TR", "IN"):
            return code
    return "EN"


def get_tmdb_api_key(db: Session) -> str:
    """Retrieve TMDB API key from DB settings or .env configuration."""
    db_setting = db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
    if db_setting and db_setting.value:
        return db_setting.value.strip()
    return settings.TMDB_API_KEY.strip()


async def search_movie_tmdb(title: str, year: Optional[int], api_key: str) -> Optional[Dict[str, Any]]:
    """Search TMDB for a movie by title and optional year."""
    if not api_key:
        return None

    params = {"api_key": api_key, "query": title, "include_adult": False}
    if year:
        params["year"] = year

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TMDB_BASE_URL}/search/movie", params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    return results[0]
    except Exception as e:
        logger.warning(f"TMDB movie search failed for '{title}': {e}")
    return None


async def search_tv_tmdb(title: str, year: Optional[int], api_key: str) -> Optional[Dict[str, Any]]:
    """Search TMDB for a TV series by title and optional first air date year."""
    if not api_key:
        return None

    params = {"api_key": api_key, "query": title, "include_adult": False}
    if year:
        params["first_air_date_year"] = year

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TMDB_BASE_URL}/search/tv", params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    return results[0]
    except Exception as e:
        logger.warning(f"TMDB TV search failed for '{title}': {e}")
    return None


async def enrich_movie(movie: Movie, api_key: str) -> bool:
    """Enrich a single movie record with TMDB metadata."""
    clean_title, year = clean_media_title(movie.raw_name)
    result = await search_movie_tmdb(clean_title, year or movie.year, api_key)

    now = datetime.now(timezone.utc)
    movie.tmdb_fetched_at = now

    if not result:
        return False

    movie.tmdb_id = result.get("id")
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
        primary_genre = genre_names[0]
        # Re-group by language + genre: "<LANG> | <GENRE>"
        lang = detect_language_code(movie.display_group)
        movie.display_group = f"{lang} | {primary_genre}"

    return True


async def enrich_series(series: Series, api_key: str) -> bool:
    """Enrich a single TV series record with TMDB metadata."""
    clean_title, year = clean_media_title(series.raw_name)
    result = await search_tv_tmdb(clean_title, year or series.year, api_key)

    now = datetime.now(timezone.utc)
    series.tmdb_fetched_at = now

    if not result:
        return False

    series.tmdb_id = result.get("id")
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
        primary_genre = genre_names[0]
        lang = detect_language_code(series.display_group)
        series.display_group = f"{lang} | {primary_genre}"

    return True


async def enrich_pending_media(db: Session, batch_size: int = 50) -> Dict[str, Any]:
    """
    Enrich a batch of movies and series missing TMDB metadata.
    Respects rate limits by staggering calls and updates real-time task progress.
    """
    api_key = get_tmdb_api_key(db)
    if not api_key:
        return {"status": "skipped", "reason": "No TMDB API key configured"}

    # Query pending movies
    pending_movies = (
        db.query(Movie)
        .filter(Movie.tmdb_fetched_at == None, Movie.is_hidden == False)
        .limit(batch_size)
        .all()
    )

    # Query pending series
    remaining_budget = max(10, batch_size - len(pending_movies))
    pending_series = (
        db.query(Series)
        .filter(Series.tmdb_fetched_at == None, Series.is_hidden == False)
        .limit(remaining_budget)
        .all()
    )

    total_items = len(pending_movies) + len(pending_series)
    if total_items == 0:
        set_task_status("completed", "TMDB Enrichment", "All media already enriched with TMDB metadata", 100)
        return {
            "status": "ok",
            "movies_processed": 0,
            "movies_enriched": 0,
            "series_processed": 0,
            "series_enriched": 0,
        }

    set_task_status("running", "TMDB Enrichment", f"Starting enrichment for {total_items} items...", 5)

    enriched_movies = 0
    current_idx = 0

    for movie in pending_movies:
        current_idx += 1
        pct = max(5, int((current_idx / total_items) * 95))
        clean_title, _ = clean_media_title(movie.raw_name)
        set_task_status(
            "running",
            "TMDB Enrichment",
            f"Enriching Movie [{current_idx}/{total_items}]: '{clean_title}'...",
            pct,
        )
        if await enrich_movie(movie, api_key):
            enriched_movies += 1
        await asyncio.sleep(0.1)  # TMDB rate limit friendliness

    enriched_series = 0
    for s in pending_series:
        current_idx += 1
        pct = max(5, int((current_idx / total_items) * 95))
        clean_title, _ = clean_media_title(s.raw_name)
        set_task_status(
            "running",
            "TMDB Enrichment",
            f"Enriching Series [{current_idx}/{total_items}]: '{clean_title}'...",
            pct,
        )
        if await enrich_series(s, api_key):
            enriched_series += 1
        await asyncio.sleep(0.1)

    db.commit()

    set_task_status(
        "completed",
        "Complete",
        f"TMDB enrichment complete: {enriched_movies} movies, {enriched_series} series enriched",
        100,
    )

    return {
        "status": "ok",
        "movies_processed": len(pending_movies),
        "movies_enriched": enriched_movies,
        "series_processed": len(pending_series),
        "series_enriched": enriched_series,
    }
