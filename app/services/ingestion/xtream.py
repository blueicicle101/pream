"""
Xtream Codes API fetcher for Pream.
Connects to upstream Xtream Codes servers, authenticates, and retrieves
Live TV channels, VOD movies, and TV Series with categories and metadata.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.models.source import Source
from app.services.ingestion.user_agents import get_user_agent

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Standard container for content fetched from an upstream source."""
    status: str = "ok"  # "ok" or "error"
    error_reason: Optional[str] = None  # auth_failed, banned, not_found, connection_error, parse_error
    error_message: Optional[str] = None
    channels: List[Dict[str, Any]] = field(default_factory=list)
    movies: List[Dict[str, Any]] = field(default_factory=list)
    series: List[Dict[str, Any]] = field(default_factory=list)
    episodes: List[Dict[str, Any]] = field(default_factory=list)
    expiry_date: Optional[str] = None
    max_connections: Optional[str] = None


def clean_base_url(raw_url: str) -> str:
    """Normalize base URL by removing path suffixes and trailing slashes."""
    url = raw_url.strip().rstrip("/")
    # If the user supplied player_api.php or get.php in the URL, strip it
    for suffix in ("/player_api.php", "/get.php", "/xmltv.php"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    # Ensure scheme
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    return url


def parse_content_types(content_types_field: Any) -> List[str]:
    """Parse content types list from JSON string or list."""
    if isinstance(content_types_field, str):
        try:
            parsed = json.loads(content_types_field)
            if isinstance(parsed, list):
                return [str(item).lower() for item in parsed]
        except Exception:
            return [s.strip().lower() for s in content_types_field.split(",") if s.strip()]
    elif isinstance(content_types_field, list):
        return [str(item).lower() for item in content_types_field]
    return ["live", "vod", "series"]


async def fetch_xtream(source: Source) -> IngestResult:
    """
    Authenticate against Xtream Codes API and fetch requested content types.
    """
    result = IngestResult()
    base_url = clean_base_url(source.url)
    username = source.username or ""
    password = source.password or ""
    ua = get_user_agent(source.user_agent)

    content_types = parse_content_types(source.content_types)

    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
    }

    # Timeouts: 15s connect, 90s read for large playlists
    timeout = httpx.Timeout(connect=15.0, read=90.0, write=15.0, pool=15.0)

    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            # 1. Authenticate & verify server
            auth_url = f"{base_url}/player_api.php?username={username}&password={password}"
            resp = await client.get(auth_url)

            if resp.status_code in (401, 403):
                result.status = "error"
                result.error_reason = "auth_failed" if resp.status_code == 401 else "banned"
                result.error_message = f"HTTP {resp.status_code} - Authentication rejected or access denied"
                return result

            if resp.status_code == 404:
                result.status = "error"
                result.error_reason = "not_found"
                result.error_message = f"HTTP 404 - player_api.php not found at {base_url}"
                return result

            if resp.status_code != 200:
                result.status = "error"
                result.error_reason = "connection_error"
                result.error_message = f"HTTP {resp.status_code} - Unexpected status code"
                return result

            try:
                auth_data = resp.json()
            except Exception as e:
                result.status = "error"
                result.error_reason = "parse_error"
                result.error_message = f"Failed to parse JSON auth response: {str(e)}"
                return result

            user_info = auth_data.get("user_info", {}) if isinstance(auth_data, dict) else {}
            auth_status = user_info.get("auth")
            account_status = user_info.get("status")

            if auth_status == 0 or (account_status and account_status.lower() not in ("active", "trial")):
                result.status = "error"
                result.error_reason = "auth_failed"
                msg = user_info.get("message") or f"Account status: {account_status or 'Inactive'}"
                result.error_message = f"Authentication rejected by provider: {msg}"
                return result

            # Extract account expiry and connection limit
            exp_raw = user_info.get("exp_date")
            result.expiry_date = str(exp_raw) if exp_raw is not None else None
            max_conns_raw = user_info.get("max_connections")
            result.max_connections = str(max_conns_raw) if max_conns_raw is not None else None

            # 2. Live TV
            if "live" in content_types or "all" in content_types:
                try:
                    cat_resp = await client.get(
                        f"{base_url}/player_api.php?username={username}&password={password}&action=get_live_categories"
                    )
                    category_map: Dict[str, str] = {}
                    if cat_resp.status_code == 200:
                        categories = cat_resp.json()
                        if isinstance(categories, list):
                            for cat in categories:
                                cid = str(cat.get("category_id"))
                                category_map[cid] = cat.get("category_name", "Uncategorized")

                    streams_resp = await client.get(
                        f"{base_url}/player_api.php?username={username}&password={password}&action=get_live_streams"
                    )
                    if streams_resp.status_code == 200:
                        streams = streams_resp.json()
                        if isinstance(streams, list):
                            for item in streams:
                                stream_id = str(item.get("stream_id", ""))
                                cat_id = str(item.get("category_id", ""))
                                group_name = category_map.get(cat_id, "Uncategorized")
                                name = item.get("name") or f"Channel {stream_id}"
                                logo = item.get("stream_icon")
                                epg_id = item.get("epg_channel_id")
                                stream_url = f"{base_url}/live/{username}/{password}/{stream_id}.ts"

                                result.channels.append({
                                    "source_id": source.id,
                                    "source_stream_id": stream_id,
                                    "raw_name": name,
                                    "raw_group": group_name,
                                    "logo_url": logo,
                                    "epg_channel_id": epg_id,
                                    "stream_url": stream_url,
                                })
                except Exception as e:
                    logger.warning(f"Error fetching live streams for source {source.name}: {e}")

            # 3. VOD (Movies)
            if "vod" in content_types or "movies" in content_types or "all" in content_types:
                try:
                    cat_resp = await client.get(
                        f"{base_url}/player_api.php?username={username}&password={password}&action=get_vod_categories"
                    )
                    category_map = {}
                    if cat_resp.status_code == 200:
                        categories = cat_resp.json()
                        if isinstance(categories, list):
                            for cat in categories:
                                cid = str(cat.get("category_id"))
                                category_map[cid] = cat.get("category_name", "Uncategorized")

                    streams_resp = await client.get(
                        f"{base_url}/player_api.php?username={username}&password={password}&action=get_vod_streams"
                    )
                    if streams_resp.status_code == 200:
                        streams = streams_resp.json()
                        if isinstance(streams, list):
                            for item in streams:
                                stream_id = str(item.get("stream_id", ""))
                                cat_id = str(item.get("category_id", ""))
                                group_name = category_map.get(cat_id, "Uncategorized")
                                name = item.get("name") or f"Movie {stream_id}"
                                poster = item.get("stream_icon")
                                container_ext = item.get("container_extension") or "mp4"
                                rating_val = item.get("rating_5based") or item.get("rating")
                                rating = None
                                if rating_val is not None:
                                    try:
                                        rating = float(rating_val)
                                    except (ValueError, TypeError):
                                        rating = None

                                stream_url = f"{base_url}/movie/{username}/{password}/{stream_id}.{container_ext}"

                                result.movies.append({
                                    "source_id": source.id,
                                    "source_stream_id": stream_id,
                                    "raw_name": name,
                                    "raw_group": group_name,
                                    "poster_url": poster,
                                    "rating": rating,
                                    "container_ext": container_ext,
                                    "stream_url": stream_url,
                                })
                except Exception as e:
                    logger.warning(f"Error fetching VOD for source {source.name}: {e}")

            # 4. Series
            if "series" in content_types or "all" in content_types:
                try:
                    cat_resp = await client.get(
                        f"{base_url}/player_api.php?username={username}&password={password}&action=get_series_categories"
                    )
                    category_map = {}
                    if cat_resp.status_code == 200:
                        categories = cat_resp.json()
                        if isinstance(categories, list):
                            for cat in categories:
                                cid = str(cat.get("category_id"))
                                category_map[cid] = cat.get("category_name", "Uncategorized")

                    series_resp = await client.get(
                        f"{base_url}/player_api.php?username={username}&password={password}&action=get_series"
                    )
                    if series_resp.status_code == 200:
                        series_list = series_resp.json()
                        if isinstance(series_list, list):
                            for item in series_list:
                                series_id = str(item.get("series_id", ""))
                                cat_id = str(item.get("category_id", ""))
                                group_name = category_map.get(cat_id, "Uncategorized")
                                name = item.get("name") or f"Series {series_id}"
                                poster = item.get("cover")
                                plot = item.get("plot")
                                genre = item.get("genre")
                                release_date = str(item.get("releaseDate") or item.get("year") or "")
                                year = None
                                if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
                                    year = int(release_date[:4])

                                rating_val = item.get("rating_5based") or item.get("rating")
                                rating = None
                                if rating_val is not None:
                                    try:
                                        rating = float(rating_val)
                                    except (ValueError, TypeError):
                                        rating = None

                                result.series.append({
                                    "source_id": source.id,
                                    "source_series_id": series_id,
                                    "raw_name": name,
                                    "raw_group": group_name,
                                    "poster_url": poster,
                                    "plot": plot,
                                    "genre": genre,
                                    "year": year,
                                    "rating": rating,
                                })
                except Exception as e:
                    logger.warning(f"Error fetching series for source {source.name}: {e}")

        result.status = "ok"
        return result

    except httpx.ConnectTimeout as e:
        result.status = "error"
        result.error_reason = "connection_error"
        result.error_message = f"Connection timed out: {str(e)}"
        return result
    except httpx.ConnectError as e:
        result.status = "error"
        result.error_reason = "connection_error"
        result.error_message = f"Connection failed (check host/port/DNS): {str(e)}"
        return result
    except httpx.HTTPError as e:
        result.status = "error"
        result.error_reason = "connection_error"
        result.error_message = f"HTTP error during fetch: {str(e)}"
        return result
    except Exception as e:
        result.status = "error"
        result.error_reason = "unexpected_error"
        result.error_message = f"Unexpected error during fetch: {str(e)}"
        return result
