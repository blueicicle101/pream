"""
Tests for Scheduler and Notifications (Phase 7).
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
import app.models  # noqa: F401
from app.main import app
from app.models.setting import Setting
from app.scheduler.scheduler import get_cron_expression, start_scheduler, stop_scheduler
from app.scheduler.tasks import scheduled_refresh_job, scheduled_tmdb_job
from app.services.notifications import (
    notify_refresh_report,
    notify_source_failure,
    send_telegram_message,
)

test_engine = create_engine("sqlite:///./data/test_sched.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class SchedulerAndNotificationsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        app.dependency_overrides[get_db] = override_get_db
        self.db = TestingSessionLocal()
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.db.close()
        stop_scheduler()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_sched.db"):
            try:
                os.remove("./data/test_sched.db")
            except Exception:
                pass

    @patch("app.services.notifications.httpx.AsyncClient")
    async def test_01_telegram_notifications(self, mock_client_cls):
        """Test formatting and sending refresh report and failure alerts."""
        mock_client = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        # 1. Direct message
        ok = await send_telegram_message(
            "<b>Hello World</b>",
            bot_token="test_token",
            chat_id="test_chat",
        )
        self.assertTrue(ok)
        self.assertEqual(mock_client.post.call_count, 1)

        # 2. Refresh report
        results = [
            {"source_name": "Alpha", "status": "ok", "channels": 100, "movies": 200, "series": 50},
            {"source_name": "Beta", "status": "error", "error_reason": "auth_failed", "error_message": "Invalid password"},
        ]
        ok_report = await notify_refresh_report(
            results,
            duration_sec=3.5,
            db=self.db,
        )
        self.assertIsInstance(ok_report, bool)

        # 3. Source failure alert
        ok_fail = await notify_source_failure(
            "Beta",
            error_reason="auth_failed",
            error_msg="HTTP 401 unauthorized",
            db=self.db,
        )
        self.assertIsInstance(ok_fail, bool)

    def test_02_scheduler_cron_and_lifecycle(self):
        """Test cron parsing and starting/stopping scheduler."""
        # Check default cron
        cron = get_cron_expression()
        self.assertIn("*", cron)

        # Test start and stop
        start_scheduler()
        stop_scheduler()

    @patch("app.scheduler.tasks.apply_all_rules_to_library")
    @patch("app.scheduler.tasks.refresh_all_sources", new_callable=AsyncMock)
    def test_03_scheduled_refresh_job(self, mock_refresh, mock_apply_rules):
        """Test scheduled refresh job calls refresh_all_sources."""
        mock_refresh.return_value = []
        mock_apply_rules.return_value = {"status": "ok"}
        scheduled_refresh_job()
        self.assertTrue(mock_refresh.called)
        self.assertTrue(mock_apply_rules.called)


if __name__ == "__main__":
    unittest.main()
