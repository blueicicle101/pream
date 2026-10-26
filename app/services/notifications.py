"""
Telegram notifications service for Pream.
Sends refresh summary reports and instant failure alerts.
"""
import html
import logging
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.setting import Setting

logger = logging.getLogger(__name__)


def get_telegram_config(db: Optional[Session] = None) -> tuple[str, str]:
    """Retrieve Telegram bot token and chat ID from DB or config."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if db:
        token_setting = db.query(Setting).filter(Setting.key == "telegram_bot_token").first()
        if token_setting and token_setting.value:
            token = token_setting.value.strip()

        chat_setting = db.query(Setting).filter(Setting.key == "telegram_chat_id").first()
        if chat_setting and chat_setting.value:
            chat_id = chat_setting.value.strip()

    return token, chat_id


async def send_telegram_message(
    text_html: str,
    db: Optional[Session] = None,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Send an HTML-formatted message via Telegram Bot API."""
    if not bot_token or not chat_id:
        token, cid = get_telegram_config(db)
        bot_token = bot_token or token
        chat_id = chat_id or cid

    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram notification returned HTTP {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.warning(f"Failed to send Telegram message: {e}")
        return False


async def notify_refresh_report(
    results: List[Dict[str, Any]],
    duration_sec: float,
    db: Optional[Session] = None,
) -> bool:
    """
    Format and send a structured refresh report following the user specification.
    """
    total_live = 0
    total_vod = 0
    total_series = 0

    lines = [
        "📊 <b>Pream Refresh Report</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    has_failures = False

    for res in results:
        s_name = html.escape(str(res.get("source_name") or f"Source {res.get('source_id')}"))
        status = res.get("status")

        if status == "ok":
            ch = res.get("channels", 0)
            mv = res.get("movies", 0)
            sr = res.get("series", 0)
            total_live += ch
            total_vod += mv
            total_series += sr
            lines.append(f"✅ <b>{s_name}</b>: {ch:,} TV | {mv:,} VOD | {sr:,} Series")
        else:
            has_failures = True
            err_msg = html.escape(str(res.get("error_message") or res.get("error_reason") or "Failed"))
            lines.append(f"❌ <b>{s_name}</b>: {err_msg}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<b>Total:</b> {total_live:,} TV | {total_vod:,} Movies | {total_series:,} Series")
    lines.append(f"⏱ Completed in {duration_sec:.1f}s")

    message = "\n".join(lines)
    return await send_telegram_message(message, db=db)


async def notify_source_failure(
    source_name: str,
    error_reason: str,
    error_msg: str,
    db: Optional[Session] = None,
) -> bool:
    """Send an immediate failure alert when a source fails during refresh."""
    s_name = html.escape(source_name)
    e_reason = html.escape(error_reason or "unknown_error")
    e_msg = html.escape(error_msg or "")

    message = (
        f"🚨 <b>Source Alert: '{s_name}' FAILED</b>\n\n"
        f"<b>Reason:</b> <code>{e_reason}</code>\n"
        f"<b>Details:</b> {e_msg}\n"
    )
    return await send_telegram_message(message, db=db)
