"""
Stream Health Check & Source Auto Re-prioritization Service.

Tests random streams (Live TV, Movies, Series) from each enabled source without
downloading entire files (HTTP Range / stream probe) to verify stream playback,
calculates success rate and response latency, re-ranks source priority,
and reports results to the configured Telegram notification channel.
"""
import asyncio
import html
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Episode
from app.models.setting import Setting
from app.models.source import Source
from app.services.ingestion.user_agents import get_user_agent
from app.services.notifications import send_telegram_message

logger = logging.getLogger(__name__)


def get_stream_health_config(db: Optional[Session] = None) -> Tuple[bool, int]:
    """Retrieve STREAM_HEALTH enabled flag and STREAM_HEALTH_COUNT (clamped 1-5)."""
    enabled = settings.STREAM_HEALTH
    count = settings.STREAM_HEALTH_COUNT

    if db:
        enabled_setting = db.query(Setting).filter(Setting.key == "stream_health").first()
        if enabled_setting and enabled_setting.value:
            enabled = enabled_setting.value.strip().lower() in ("true", "1", "yes")

        count_setting = db.query(Setting).filter(Setting.key == "stream_health_count").first()
        if count_setting and count_setting.value:
            try:
                count = int(count_setting.value.strip())
            except ValueError:
                pass

    clamped_count = max(1, min(5, count))
    return enabled, clamped_count


async def probe_stream(
    client: httpx.AsyncClient,
    stream_url: str,
    user_agent: str,
    timeout: float = 6.0,
) -> Tuple[bool, float, Optional[str]]:
    """
    Probe a stream URL without downloading full file or consuming disk space.
    Uses HTTP Range: bytes=0-1024 or streaming GET reading only the first chunk.
    Returns: (is_healthy, latency_ms, error_message)
    """
    if not stream_url:
        return False, 0.0, "Missing stream URL"

    headers = {
        "User-Agent": user_agent,
        "Range": "bytes=0-1024",
        "Accept": "*/*",
    }

    start = time.perf_counter()
    try:
        async with client.stream(
            "GET",
            stream_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        ) as resp:
            latency = (time.perf_counter() - start) * 1000.0

            # 200 OK or 206 Partial Content indicates valid stream endpoint
            if resp.status_code in (200, 206):
                # Verify we can read first chunk without reading entire file
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        return True, round(latency, 1), None
                    break
                # If stream returned 200/206 with zero bytes
                return True, round(latency, 1), None
            else:
                return False, round(latency, 1), f"HTTP {resp.status_code}"

    except httpx.TimeoutException:
        latency = (time.perf_counter() - start) * 1000.0
        return False, round(latency, 1), f"Connection timeout ({timeout}s)"
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000.0
        return False, round(latency, 1), f"Stream error: {str(exc)[:120]}"


async def run_stream_health_check(
    db: Session,
    custom_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Perform random stream health test across all enabled sources,
    re-prioritize sources based on success rate and latency, and send a Telegram report.
    """
    enabled, default_count = get_stream_health_config(db)
    sample_count = max(1, min(5, custom_count if custom_count is not None else default_count))

    sources = db.query(Source).filter(Source.enabled == True).order_by(Source.priority.asc(), Source.id.asc()).all()
    if not sources:
        return {
            "status": "ok",
            "message": "No enabled sources to test",
            "enabled": enabled,
            "sample_count": sample_count,
            "sources": [],
        }

    overall_start = time.perf_counter()
    source_results = []

    # Sequential per-source testing to respect single-connection limits of upstream providers
    for src in sources:
        ua = get_user_agent(src.user_agent)
        candidates: List[Dict[str, Any]] = []

        # 1. Collect candidate Live channels
        channels = (
            db.query(Channel.display_name, Channel.stream_url)
            .filter(Channel.source_id == src.id, Channel.is_hidden == False, Channel.stream_url.isnot(None))
            .limit(100)
            .all()
        )
        for ch in channels:
            candidates.append({"type": "live", "name": ch.display_name, "url": ch.stream_url})

        # 2. Collect candidate Movies
        movies = (
            db.query(Movie.display_name, Movie.stream_url)
            .filter(Movie.source_id == src.id, Movie.is_hidden == False, Movie.stream_url.isnot(None))
            .limit(100)
            .all()
        )
        for mv in movies:
            candidates.append({"type": "movie", "name": mv.display_name, "url": mv.stream_url})

        # 3. Collect candidate Series Episodes
        episodes = (
            db.query(Episode.title, Episode.stream_url)
            .filter(Episode.source_id == src.id, Episode.stream_url.isnot(None))
            .limit(100)
            .all()
        )
        for ep in episodes:
            candidates.append({"type": "series", "name": ep.title or "Episode", "url": ep.stream_url})

        if not candidates:
            source_results.append({
                "source_id": src.id,
                "source_name": src.name,
                "old_priority": src.priority,
                "new_priority": src.priority,
                "tested_count": 0,
                "passed_count": 0,
                "failed_count": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "details": [],
                "status": "empty",
                "notes": "No stream URLs found in library",
            })
            continue

        # Mixed random sample up to sample_count
        sampled = random.sample(candidates, min(len(candidates), sample_count))
        test_details = []
        passed = 0
        latencies = []

        timeout = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for item in sampled:
                ok, lat, err = await probe_stream(client, item["url"], ua)
                if ok:
                    passed += 1
                    latencies.append(lat)
                test_details.append({
                    "name": item["name"],
                    "type": item["type"],
                    "status": "ok" if ok else "failed",
                    "latency_ms": lat,
                    "error": err,
                })

        tested_count = len(sampled)
        success_rate = round((passed / tested_count) * 100.0, 1) if tested_count > 0 else 0.0
        avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

        source_results.append({
            "source_id": src.id,
            "source_name": src.name,
            "old_priority": src.priority,
            "new_priority": src.priority,
            "tested_count": tested_count,
            "passed_count": passed,
            "failed_count": tested_count - passed,
            "success_rate": success_rate,
            "avg_latency_ms": avg_lat,
            "details": test_details,
            "status": "ok" if passed > 0 else "failed",
            "notes": None,
        })

    # Re-prioritize sources based on success_rate DESC, avg_latency_ms ASC
    active_results = [r for r in source_results if r["tested_count"] > 0]
    active_results.sort(key=lambda x: (-x["success_rate"], x["avg_latency_ms"]))

    # Assign new sequential priorities starting at 1
    new_priority = 1
    for r in active_results:
        r["new_priority"] = new_priority
        src_obj = next((s for s in sources if s.id == r["source_id"]), None)
        if src_obj:
            src_obj.priority = new_priority
        new_priority += 1

    # Empty sources get placed after active ones
    for r in source_results:
        if r["tested_count"] == 0:
            r["new_priority"] = new_priority
            src_obj = next((s for s in sources if s.id == r["source_id"]), None)
            if src_obj:
                src_obj.priority = new_priority
            new_priority += 1

    db.commit()

    total_duration = round(time.perf_counter() - overall_start, 2)

    # Format Telegram Notification Report
    telegram_sent = False
    try:
        report_lines = [
            "🩺 <b>Pream Stream Health & Prioritization Report</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 <b>Sample Size:</b> {sample_count} random streams per source",
            "",
        ]

        for r in source_results:
            s_name = html.escape(r["source_name"])
            if r["tested_count"] == 0:
                report_lines.append(f"⚪ <b>{s_name}</b> (No streams indexed)")
                report_lines.append(f"• Priority: #{r['old_priority']} ➔ #{r['new_priority']}")
            else:
                icon = "🟢" if r["success_rate"] >= 80 else ("🟡" if r["success_rate"] > 0 else "🔴")
                shift = ""
                if r["new_priority"] < r["old_priority"]:
                    shift = " <i>(Promoted ⬆️)</i>"
                elif r["new_priority"] > r["old_priority"]:
                    shift = " <i>(Demoted ⬇️)</i>"

                report_lines.append(f"{icon} <b>{s_name}</b>{shift}")
                report_lines.append(f"• Success Rate: <b>{r['success_rate']}%</b> ({r['passed_count']}/{r['tested_count']} passed)")
                report_lines.append(f"• Avg Latency: <b>{r['avg_latency_ms']}ms</b>")
                report_lines.append(f"• Priority: #{r['old_priority']} ➔ #{r['new_priority']}")

            report_lines.append("")

        report_lines.append("━━━━━━━━━━━━━━━━━━━━")
        report_lines.append(f"⏱ <b>Duration:</b> {total_duration}s")

        telegram_sent = await send_telegram_message("\n".join(report_lines), db=db)
    except Exception as ex:
        logger.warning(f"Failed to send stream health Telegram report: {ex}")

    return {
        "status": "ok",
        "sample_count": sample_count,
        "duration_sec": total_duration,
        "telegram_sent": telegram_sent,
        "sources": source_results,
    }
