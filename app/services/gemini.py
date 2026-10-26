"""
Gemini AI service for intelligent extraction of clean media title,
release year, and language code from noisy raw stream titles.
Uses Google's Gemini Flash-Lite model with native JSON output.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.setting import Setting

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


def get_gemini_api_key(db: Optional[Session] = None) -> str:
    """Retrieve Gemini API key from database setting or .env."""
    if db:
        db_setting = db.query(Setting).filter(Setting.key == "gemini_api_key").first()
        if db_setting and db_setting.value:
            return db_setting.value.strip()
    return getattr(settings, "GEMINI_API_KEY", "").strip()


def is_staggered_gemini_calls_enabled(db: Optional[Session] = None) -> bool:
    """Check if staggered calls to Gemini are enabled to prevent hitting 15 RPM limits."""
    if db:
        db_setting = db.query(Setting).filter(Setting.key == "staggered_gemini_calls").first()
        if db_setting and db_setting.value:
            return db_setting.value.lower() in ("true", "1", "yes")
    return getattr(settings, "STAGGERED_GEMINI_CALLS", True)


def get_gemini_rate_delay(db: Optional[Session] = None) -> float:
    """Delay in seconds between staggered Gemini calls (defaults to 4.0s for 15 RPM)."""
    if db:
        db_setting = db.query(Setting).filter(Setting.key == "gemini_rate_delay").first()
        if db_setting and db_setting.value:
            try:
                return max(0.5, float(db_setting.value))
            except ValueError:
                pass
    return getattr(settings, "GEMINI_RATE_DELAY", 4.0)


# Module-level lock for coordinating staggered calls
_gemini_lock = asyncio.Lock()
_last_gemini_call_time: float = 0.0


async def extract_title_with_gemini(
    raw_title: str,
    raw_group: Optional[str] = None,
    media_type: str = "movie",
    api_key: Optional[str] = None,
    stagger: bool = True,
    delay_seconds: float = 4.0,
) -> Optional[Dict[str, Any]]:
    """
    Call Gemini Flash-Lite to extract clean title, release year, language ISO code,
    and TMDB language code from a dirty raw IPTV release title.

    Returns dict like:
    {
        "clean_title": "Avatar: The Way of Water",
        "year": 2022,
        "language_code": "FR",
        "tmdb_language": "fr-FR"
    }
    """
    key = api_key or getattr(settings, "GEMINI_API_KEY", "").strip()
    if not key:
        return None

    # Stagger calls if requested to respect rate limits
    if stagger:
        global _last_gemini_call_time
        async with _gemini_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - _last_gemini_call_time
            if elapsed < delay_seconds:
                await asyncio.sleep(delay_seconds - elapsed)
            _last_gemini_call_time = asyncio.get_event_loop().time()

    prompt = f"""You are a specialized media parser. Extract metadata from this raw {media_type} release string.
Raw title: "{raw_title}"
Raw group/category: "{raw_group or ''}"

Rules:
1. Strip all release artifacts, resolutions (4K, 1080p, FHD), codecs (x264, HEVC), audio formats (AC3, AAC, 5.1), rip tags (WEB-DL, BluRay), and release groups.
2. Determine the primary audio or presentation language of this release:
   - Identify 2-letter uppercase ISO language code (e.g., EN, FR, ES, DE, IT, NL, HI, AR, PT, RU, TR).
   - If country code like UK or US is present, language_code should be EN.
   - For tmdb_language, format as standard TMDB locale like "en-US", "fr-FR", "es-ES", "de-DE", "nl-NL", "it-IT", etc.
3. Determine the clean official movie/series name without year or language prefixes.
4. If a 4-digit release year is present or strongly inferred, extract it as an integer; otherwise null.

Respond ONLY with a valid JSON object matching this schema:
{{
  "clean_title": "string",
  "year": integer or null,
  "language_code": "string",
  "tmdb_language": "string"
}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    url = f"{GEMINI_BASE_URL}/{DEFAULT_GEMINI_MODEL}:generateContent?key={key}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        raw_json = parts[0]["text"].strip()
                        parsed = json.loads(raw_json)
                        if isinstance(parsed, dict) and parsed.get("clean_title"):
                            return {
                                "clean_title": str(parsed.get("clean_title")).strip(),
                                "year": int(parsed["year"]) if parsed.get("year") else None,
                                "language_code": str(parsed.get("language_code", "EN")).upper().strip(),
                                "tmdb_language": str(parsed.get("tmdb_language", "en-US")).strip(),
                            }
            else:
                logger.warning(f"Gemini API returned status {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Gemini title extraction failed for '{raw_title}': {e}")

    return None

