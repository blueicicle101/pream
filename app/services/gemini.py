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


def is_gemini_enabled(db: Optional[Session] = None) -> bool:
    """Check if Gemini AI parsing is enabled."""
    if db:
        db_setting = db.query(Setting).filter(Setting.key == "gemini_enabled").first()
        if db_setting and db_setting.value:
            return db_setting.value.lower() in ("true", "1", "yes")
    return getattr(settings, "GEMINI_ENABLED", True)


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
    db: Optional[Session] = None,
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
    if not is_gemini_enabled(db):
        return None

    key = api_key or get_gemini_api_key(db)
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


async def suggest_group_rules_with_gemini(
    user_instruction: str,
    categories: Dict[str, list[str]],
    group_by_source: bool = False,
    db: Optional[Session] = None,
) -> list[Dict[str, Any]]:
    """
    Use Gemini AI to analyze raw stream categories and suggest group cleaning/hiding/renaming rules
    based on the user's instructions (e.g. 'Keep only UK, US, and Sports, hide everything else').

    When group_by_source is True, the prompt explicitly instructs Gemini NEVER to generate
    rules that match or strip source prefixes (e.g., '[SourceName] ...').
    """
    global _last_gemini_call_time

    if not is_gemini_enabled(db):
        logger.warning("Gemini AI is disabled in settings or DB.")
        raise ValueError("Gemini AI is currently disabled in Settings. Please enable it in Settings > Gemini AI.")

    key = get_gemini_api_key(db)
    if not key:
        logger.warning("Gemini API key is not configured.")
        raise ValueError("Gemini API key is missing. Please provide your Google AI Studio API key in Settings > Gemini AI.")

    if is_staggered_gemini_calls_enabled(db):
        delay_seconds = get_gemini_rate_delay(db)
        if _last_gemini_call_time > 0:
            now = asyncio.get_event_loop().time()
            elapsed = now - _last_gemini_call_time
            if elapsed < delay_seconds:
                await asyncio.sleep(delay_seconds - elapsed)
            _last_gemini_call_time = asyncio.get_event_loop().time()

    live_groups = categories.get("live", [])[:200]
    vod_groups = categories.get("vod", [])[:200]
    series_groups = categories.get("series", [])[:200]

    source_prefix_instruction = ""
    if group_by_source:
        source_prefix_instruction = """
IMPORTANT CONSTRAINT: 'Group by Source' is ENABLED. Categories in the system may display with a bracketed source prefix like '[Provider] GroupName'.
You MUST NOT generate rules that target, strip, or replace these source prefix brackets '[...]'! All rules must match the core category names only (e.g., match 'UK NEWS' or 'DE:.*', NEVER '[IPTV] UK NEWS').
"""

    prompt = f"""You are an IPTV category organization assistant.
Generate structured transformation and filtering rules for the rules engine based on the user's request.

User Request / Instruction:
"{user_instruction}"

Available Live TV Categories (sample):
{json.dumps(live_groups)}

Available VOD Movie Categories (sample):
{json.dumps(vod_groups)}

Available TV Series Categories (sample):
{json.dumps(series_groups)}

{source_prefix_instruction}

Supported rule types:
- "hide_group": Hide all channels/streams in matching groups. Requires "match_pattern" (regex).
- "rename_group": Rename matching groups using regex pattern and replacement. Requires "match_pattern" and "replacement".
- "set_group": Move streams matching pattern to a fixed new group. Requires "match_pattern" and "replacement" (the new group name).
- "uppercase_group", "lowercase_group", "capitalize_group", "titlecase_group": Case normalization.

Content types:
- "all" (applies across Live, Movies, Series)
- "live" (Live TV only)
- "vod" (VOD Movies only)
- "series" (Series only)

Match field:
- Must always be "group" because you are cleaning groups/categories.

Rules:
1. Generate precise, clean Python regex patterns for "match_pattern" (e.g. "^(?!.*(UK|US|SPORTS)).*$" or "DE:.*").
2. Always provide a clear human-readable "description" explaining what each rule does.
3. Keep the number of suggested rules minimal and effective (typically 1 to 10 rules).

Respond ONLY with a valid JSON array of rule objects matching this schema:
[
  {{
    "rule_type": "hide_group" | "rename_group" | "set_group" | "uppercase_group" | "titlecase_group",
    "content_type": "all" | "live" | "vod" | "series",
    "match_field": "group",
    "match_pattern": "string (regex)",
    "replacement": "string or null",
    "description": "string"
  }}
]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    url = f"{GEMINI_BASE_URL}/{DEFAULT_GEMINI_MODEL}:generateContent?key={key}"

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        raw_json = parts[0]["text"].strip()
                        parsed = json.loads(raw_json)
                        if isinstance(parsed, list):
                            clean_rules = []
                            for r in parsed:
                                if isinstance(r, dict) and r.get("rule_type") and r.get("match_pattern"):
                                    clean_rules.append({
                                        "rule_type": str(r["rule_type"]).strip(),
                                        "content_type": str(r.get("content_type", "all")).strip(),
                                        "match_field": "group",
                                        "match_pattern": str(r["match_pattern"]).strip(),
                                        "replacement": str(r["replacement"]).strip() if r.get("replacement") else "",
                                        "description": str(r.get("description", "")).strip(),
                                    })
                            return clean_rules
            else:
                err_msg = f"Gemini API error ({resp.status_code}): {resp.text[:250]}"
                logger.warning(err_msg)
                raise RuntimeError(err_msg)
    except Exception as e:
        logger.warning(f"Gemini rule suggestion failed: {e}")
        raise e


