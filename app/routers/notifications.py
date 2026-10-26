"""
Notifications API router.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import require_admin
from app.services.notifications import send_telegram_message

router = APIRouter(prefix="/api/notifications", tags=["Notifications"], dependencies=[Depends(require_admin)])


@router.post("/test-telegram")
async def test_telegram_notification(db: Session = Depends(get_db)):
    """Send a test message to the configured Telegram chat."""
    test_msg = (
        "🤖 <b>Pream Notification Test</b>\n"
        "Your Telegram integration is configured correctly!"
    )
    success = await send_telegram_message(test_msg, db=db)
    if success:
        return {"status": "ok", "message": "Notification sent successfully."}
    else:
        return {
            "status": "error",
            "error_message": "Failed to send notification. Verify your Bot Token and Chat ID.",
        }
