"""
M3U / M3U8 playlist parser and fetcher for Pream.
Supports standard IPTV M3U tags (#EXTINF with tvg-name, tvg-id, tvg-logo, group-title).
"""
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.models.source import Source
from app.services.ingestion.user_agents import get_user_agent
from app.services.ingestion.xtream import IngestResult, parse_content_types

logger = logging.getLogger(__name__)

# Regex for extracting attributes from #EXTINF line
ATTR_REGEX = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')
EXTINF_REGEX = re.compile(r"^#EXTINF:(?:-?\d+)?\s*(.*?),(.*)$")
SERIES_EP_REGEX = re.compile(r"S(\d{1,3})E(\d{1,3})", re.IGNORECASE)


def parse_m3u_content(content: str, source_id: int, content_types: List[str]) -> IngestResult:
    """Parse text content of an M3U file into IngestResult."""
    result = IngestResult()
    lines = content.splitlines()

    current_meta: Optional[Dict[str, str]] = None
    stream_counter = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            stream_counter += 1
            meta: Dict[str, str] = {"stream_counter": str(stream_counter)}
            match = EXTINF_REGEX.match(line)
            if match:
                attrs_part, title_part = match.groups()
                meta["title"] = title_part.strip()
                for key, val in ATTR_REGEX.findall(attrs_part):
                    meta[key.lower()] = val.strip()
            else:
                meta["title"] = line[8:].strip()
            current_meta = meta

        elif not line.startswith("#") and current_meta:
            # This line is the stream URL
            stream_url = line
            title = current_meta.get("tvg-name") or current_meta.get("title") or f"Stream {current_meta['stream_counter']}"
            group = current_meta.get("group-title") or "Uncategorized"
            logo = current_meta.get("tvg-logo")
            epg_id = current_meta.get("tvg-id")
            stream_id = current_meta.get("tvg-id") or current_meta["stream_counter"]

            lower_url = stream_url.lower()
            lower_group = group.lower()

            # Classify stream type
            is_movie = (
                "/movie/" in lower_url
                or "vod" in lower_group
                or "movie" in lower_group
                or any(lower_url.endswith(ext) for ext in (".mp4", ".mkv", ".avi"))
            )
            is_series = (
                "/series/" in lower_url
                or "series" in lower_group
                or bool(SERIES_EP_REGEX.search(title))
            )

            if is_series and ("series" in content_types or "all" in content_types):
                result.series.append({
                    "source_id": source_id,
                    "source_series_id": stream_id,
                    "raw_name": title,
                    "raw_group": group,
                    "poster_url": logo,
                    "plot": None,
                    "genre": None,
                    "year": None,
                    "rating": None,
                })
            elif is_movie and ("vod" in content_types or "movies" in content_types or "all" in content_types):
                ext = "mp4"
                for possible_ext in ("mp4", "mkv", "avi", "ts"):
                    if lower_url.endswith(f".{possible_ext}"):
                        ext = possible_ext
                        break

                result.movies.append({
                    "source_id": source_id,
                    "source_stream_id": stream_id,
                    "raw_name": title,
                    "raw_group": group,
                    "poster_url": logo,
                    "rating": None,
                    "container_ext": ext,
                    "stream_url": stream_url,
                })
            elif "live" in content_types or "all" in content_types:
                result.channels.append({
                    "source_id": source_id,
                    "source_stream_id": stream_id,
                    "raw_name": title,
                    "raw_group": group,
                    "logo_url": logo,
                    "epg_channel_id": epg_id,
                    "stream_url": stream_url,
                })

            current_meta = None

    result.status = "ok"
    return result


async def fetch_m3u(source: Source) -> IngestResult:
    """Fetch M3U from remote HTTP/HTTPS URL or local path and parse it."""
    result = IngestResult()
    content_types = parse_content_types(source.content_types)
    ua = get_user_agent(source.user_agent)

    # Local file support
    if source.url.startswith("file://") or (not source.url.startswith("http://") and not source.url.startswith("https://") and ":" in source.url):
        try:
            path = source.url.replace("file://", "")
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return parse_m3u_content(content, source.id, content_types)
        except Exception as e:
            result.status = "error"
            result.error_reason = "not_found"
            result.error_message = f"Failed to read local M3U file: {str(e)}"
            return result

    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
    }
    src_timeout = getattr(settings, "SOURCE_TIMEOUT", 60.0) or 60.0
    timeout = httpx.Timeout(connect=min(15.0, src_timeout), read=src_timeout, write=15.0, pool=15.0)

    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(source.url)
            if resp.status_code in (401, 403):
                result.status = "error"
                result.error_reason = "auth_failed" if resp.status_code == 401 else "banned"
                result.error_message = f"HTTP {resp.status_code} - Access denied when downloading playlist"
                return result
            if resp.status_code == 404:
                result.status = "error"
                result.error_reason = "not_found"
                result.error_message = f"HTTP 404 - M3U playlist URL not found"
                return result
            if resp.status_code != 200:
                result.status = "error"
                result.error_reason = "connection_error"
                result.error_message = f"HTTP {resp.status_code} - Unexpected response code"
                return result

            return parse_m3u_content(resp.text, source.id, content_types)

    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
        result.status = "error"
        result.error_reason = "timeout"
        result.error_message = f"Request timed out ({src_timeout}s): {str(e)}"
        return result
    except httpx.ConnectError as e:
        result.status = "error"
        result.error_reason = "connection_error"
        result.error_message = f"Connection failed (check host/DNS): {str(e)}"
        return result
    except Exception as e:
        result.status = "error"
        result.error_reason = "unexpected_error"
        result.error_message = f"Unexpected error reading M3U: {str(e)}"
        return result
