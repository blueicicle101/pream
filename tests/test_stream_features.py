"""
Tests for new features:
1. Transparent zero-bandwidth failover via HTTP 302 redirect.
2. Stream playback history logging and 30-day auto-retention.
3. Encrypted backup export and restore API.
4. Onboarding wizard dismissal and potential failovers count calculation.
"""
from datetime import datetime, timedelta, timezone
import io
import json
import os
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
import app.models  # noqa: F401
from app.main import app
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.rule import Rule
from app.models.setting import Setting
from app.models.source import Source
from app.models.stream_log import StreamLog
from app.services.backup import export_encrypted_backup, import_encrypted_backup
from app.services.stream_logs import cleanup_old_stream_logs

test_engine = create_engine(
    "sqlite:///./data/test_stream_features.db",
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class StreamFeaturesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        from app.services.auth import require_admin
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_admin] = lambda: "admin"
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_stream_features.db"):
            try:
                os.remove("./data/test_stream_features.db")
            except Exception:
                pass

    def setUp(self):
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    def test_01_stream_history_logging_and_cleanup(self):
        """Test stream requests write to StreamLog and 30-day cleanup removes old records."""
        src = Source(name="SrcHist", type="m3u", url="http://provider.tv/p.m3u", enabled=True, last_status="ok")
        self.db.add(src)
        self.db.commit()

        movie = Movie(
            source_id=src.id,
            source_stream_id="m101",
            raw_name="Interstellar",
            raw_group="Sci-Fi",
            display_name="Interstellar",
            display_group="Sci-Fi",
            stream_url="http://stream.upstream/vod/interstellar.mp4",
        )
        self.db.add(movie)
        self.db.commit()

        # Add an old stream log (>35 days old)
        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=35)
        old_log = StreamLog(
            stream_type="movie",
            stream_id=999,
            stream_title="Old Classic Movie",
            client_ip="192.168.1.50",
            created_at=old_time,
        )
        self.db.add(old_log)
        self.db.commit()

        # Stream movie via proxy endpoint
        resp = self.client.get(
            f"/movie/{settings.OUTPUT_USERNAME}/{settings.OUTPUT_PASSWORD}/{movie.id}.mp4",
            follow_redirects=False,
            headers={"X-Forwarded-For": "203.0.113.195", "User-Agent": "TiviMate/4.7.0"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "http://stream.upstream/vod/interstellar.mp4")

        # Verify stream log was recorded
        new_log = (
            self.db.query(StreamLog)
            .filter(StreamLog.stream_id == movie.id, StreamLog.stream_type == "movie")
            .first()
        )
        self.assertIsNotNone(new_log)
        self.assertEqual(new_log.client_ip, "203.0.113.195")
        self.assertEqual(new_log.user_agent, "TiviMate/4.7.0")
        self.assertEqual(new_log.stream_title, "Interstellar")

        # Test cleanup deletes 35-day-old log but keeps new log
        deleted = cleanup_old_stream_logs(self.db, days=30)
        self.assertGreaterEqual(deleted, 1)

        recheck_old = self.db.query(StreamLog).filter(StreamLog.stream_id == 999).first()
        self.assertIsNone(recheck_old)

        recheck_new = self.db.query(StreamLog).filter(StreamLog.stream_id == movie.id).first()
        self.assertIsNotNone(recheck_new)

    def test_02_stream_failover_transparent_redirect(self):
        """Test that if a primary source is unhealthy (status=error), proxy transparently 302-redirects to healthy alternate."""
        # Primary source in error
        src_primary = Source(
            name="DownProvider",
            type="m3u",
            url="http://broken.tv/m.m3u",
            enabled=True,
            last_status="error",
            last_error="Connection timed out",
            priority=1,
        )
        # Secondary healthy source
        src_backup = Source(
            name="HealthyBackup",
            type="m3u",
            url="http://healthy.tv/m.m3u",
            enabled=True,
            last_status="ok",
            priority=2,
        )
        self.db.add_all([src_primary, src_backup])
        self.db.commit()

        # Primary channel on down source
        ch_down = Channel(
            source_id=src_primary.id,
            source_stream_id="live_down",
            raw_name="Sky Sports F1 HD",
            display_name="Sky Sports F1 HD",
            display_group="UK - Sports",
            stream_url="http://down.upstream/live/skyf1.ts",
        )
        # Secondary channel with identical display name on healthy source
        ch_backup = Channel(
            source_id=src_backup.id,
            source_stream_id="live_backup",
            raw_name="[HD] Sky Sports F1 HD",
            display_name="Sky Sports F1 HD",
            display_group="Sports",
            stream_url="http://backup.upstream/live/skyf1_backup.ts",
        )
        self.db.add_all([ch_down, ch_backup])
        self.db.commit()

        # Request stream on down channel ID
        resp = self.client.get(
            f"/live/{settings.OUTPUT_USERNAME}/{settings.OUTPUT_PASSWORD}/{ch_down.id}.ts",
            follow_redirects=False,
            headers={"X-Forwarded-For": "10.0.0.99", "User-Agent": "VLC/3.0.18"},
        )
        self.assertEqual(resp.status_code, 302)
        # MUST redirect to the backup healthy provider's URL
        self.assertEqual(resp.headers["location"], "http://backup.upstream/live/skyf1_backup.ts")

        # Verify failover flag was marked in log
        log_entry = (
            self.db.query(StreamLog)
            .filter(StreamLog.stream_id == ch_down.id, StreamLog.stream_type == "live")
            .order_by(StreamLog.id.desc())
            .first()
        )
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.is_failover, 1)
        self.assertEqual(log_entry.source_id, src_backup.id)

    def test_03_backup_export_and_restore_api(self):
        """Test encrypted backup export and import REST endpoints."""
        # 1. Export backup via API
        exp_res = self.client.post("/api/system/backup/export", json={"password": "testpass1234"})
        self.assertEqual(exp_res.status_code, 200)
        self.assertTrue(exp_res.headers.get("content-disposition", "").startswith("attachment;"))

        envelope = exp_res.json()
        self.assertTrue(envelope.get("pream_backup"))
        self.assertEqual(envelope.get("cipher"), None)
        self.assertIn("salt", envelope)
        self.assertIn("ciphertext", envelope)
        self.assertIn("auth_tag", envelope)

        # 2. Import backup with wrong password should return 400
        file_bytes = exp_res.content
        wrong_form = {
            "password": "wrongpassword!",
            "mode": "merge",
        }
        res_wrong = self.client.post(
            "/api/system/backup/import",
            data=wrong_form,
            files={"file": ("backup.json", file_bytes, "application/json")},
        )
        self.assertEqual(res_wrong.status_code, 400)
        self.assertIn("Decryption failed", res_wrong.json()["detail"])

        # 3. Import backup with correct password should succeed
        ok_form = {
            "password": "testpass1234",
            "mode": "merge",
        }
        res_ok = self.client.post(
            "/api/system/backup/import",
            data=ok_form,
            files={"file": ("backup.json", file_bytes, "application/json")},
        )
        self.assertEqual(res_ok.status_code, 200)
        data = res_ok.json()
        self.assertEqual(data["status"], "ok")
        self.assertGreater(data["sources_restored"], 0)

    def test_04_onboarding_dismiss_and_stream_logs_api(self):
        """Test dismissal of onboarding wizard and stream logs query endpoint."""
        dismiss_res = self.client.post("/api/system/onboarding/dismiss")
        self.assertEqual(dismiss_res.status_code, 200)
        self.assertTrue(dismiss_res.json()["onboarding_completed"])

        setting = self.db.query(Setting).filter(Setting.key == "onboarding_completed").first()
        self.assertIsNotNone(setting)
        self.assertEqual(setting.value, "true")

        # Query stream logs API
        logs_res = self.client.get("/api/system/stream-logs?limit=50")
        self.assertEqual(logs_res.status_code, 200)
        logs_data = logs_res.json()
        self.assertIn("logs", logs_data)
        self.assertIn("total", logs_data)
        self.assertIn("movies_count", logs_data)
        self.assertIn("live_count", logs_data)
        self.assertIn("failover_count", logs_data)
        self.assertGreaterEqual(logs_data["total"], 1)

    def test_05_stream_info_and_launcher_api(self):
        """Test stream-info and .m3u launcher generation endpoints for desktop players."""
        src = Source(name="LauncherSrc", type="m3u", url="http://provider.tv/l.m3u", enabled=True)
        self.db.add(src)
        self.db.commit()

        movie_mkv = Movie(
            source_id=src.id,
            source_stream_id="mkv1",
            raw_name="Avatar Way of Water",
            display_name="Avatar Way of Water",
            display_group="Sci-Fi",
            container_ext="mkv",
            stream_url="http://upstream.tv/movie/avatar.mkv",
        )
        self.db.add(movie_mkv)
        self.db.commit()

        # 1. Test stream-info
        info_res = self.client.get(f"/api/proxy/stream-info/movie/{movie_mkv.id}")
        self.assertEqual(info_res.status_code, 200)
        info_data = info_res.json()
        self.assertTrue(info_data["is_mkv"])
        self.assertEqual(info_data["container"], "mkv")
        self.assertEqual(info_data["url"], "http://upstream.tv/movie/avatar.mkv")

        # 2. Test .m3u desktop launcher endpoint
        launcher_res = self.client.get(f"/api/proxy/stream-launcher/movie/{movie_mkv.id}")
        self.assertEqual(launcher_res.status_code, 200)
        self.assertTrue(launcher_res.headers.get("content-type", "").startswith("audio/x-mpegurl"))
        self.assertIn("Avatar", launcher_res.headers.get("content-disposition", ""))
        content = launcher_res.text
        self.assertTrue(content.startswith("#EXTM3U\n"))
        self.assertIn("#EXTINF:-1,Avatar Way of Water", content)
        self.assertIn("http://upstream.tv/movie/avatar.mkv", content)


if __name__ == "__main__":
    unittest.main()

