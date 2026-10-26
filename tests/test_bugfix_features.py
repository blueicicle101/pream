"""
Tests for newly added features and bugfixes:
- SOURCE_SCAN_THREADS setting
- Non-blocking background source refresh endpoints
- Real-time task progress tracker and auto-idle transition
- Central Library left category sidebar rendering
"""
import os
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base, get_db
import app.models  # noqa: F401
from app.models.channel import Channel
from app.models.source import Source
from app.main import app
from app.services.task_status import get_task_status, set_idle, set_task_status

test_engine = create_engine("sqlite:///./data/test_bugfix.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class BugfixFeaturesTestCase(unittest.TestCase):
    def setUp(self):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        from app.services.auth import require_admin
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_admin] = lambda: "admin"
        self.client = TestClient(app)
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_bugfix.db"):
            try:
                os.remove("./data/test_bugfix.db")
            except Exception:
                pass

    def test_01_source_scan_threads_setting(self):
        """Test SOURCE_SCAN_THREADS has default 1 and can be configured."""
        settings = Settings()
        self.assertEqual(settings.SOURCE_SCAN_THREADS, 1)

        custom_settings = Settings(SOURCE_SCAN_THREADS=2)
        self.assertEqual(custom_settings.SOURCE_SCAN_THREADS, 2)

    def test_02_task_status_tracker_and_idle_transition(self):
        """Test set_task_status and get_task_status transitions."""
        set_task_status("running", "Scanning Source", "Connecting to provider...", 15)
        st = get_task_status()
        self.assertEqual(st["status"], "running")
        self.assertEqual(st["task"], "Scanning Source")
        self.assertEqual(st["progress"], 15)

        # Update progress to 50%
        set_task_status("running", "Scanning Source", "Progress: 50%", 50)
        st = get_task_status()
        self.assertEqual(st["progress"], 50)
        self.assertIn("Progress: 50%", st["message"])

        # Complete
        set_task_status("completed", "Complete", "Refreshed: 100 TV, 20 VOD", 100)
        st = get_task_status()
        self.assertEqual(st["status"], "completed")
        self.assertEqual(st["task"], "Complete")
        self.assertEqual(st["progress"], 100)

        # Reset to idle
        set_idle("System ready")
        st = get_task_status()
        self.assertEqual(st["status"], "idle")
        self.assertEqual(st["task"], "Idle")
        self.assertEqual(st["progress"], 0)

    def test_03_api_task_progress_endpoint(self):
        """Test GET /api/system/task-progress returns current background task status."""
        set_task_status("running", "Scanning Source", "Connecting to AlphaTV...", 25)
        res = self.client.get("/api/system/task-progress")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["task"], "Scanning Source")
        self.assertEqual(data["progress"], 25)
        self.assertIn("AlphaTV", data["message"])

    @patch("app.routers.sources.run_refresh_source_background")
    def test_04_refresh_source_non_blocking(self, mock_bg_run):
        """Test POST /api/sources/{id}/refresh returns status 'started' immediately without blocking."""
        source = Source(
            name="StreamPlus",
            type="xtream",
            url="http://stream.plus:8080",
            username="u",
            password="p",
            enabled=True,
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        res = self.client.post(f"/api/sources/{source.id}/refresh")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "started")
        self.assertEqual(data["source_id"], source.id)
        self.assertIn("StreamPlus", data["message"])

        mock_bg_run.assert_called_once_with(source.id)

    @patch("app.routers.sources.run_refresh_all_background")
    def test_05_refresh_all_non_blocking(self, mock_bg_run):
        """Test POST /api/sources/refresh returns status 'started' immediately without blocking."""
        res = self.client.post("/api/sources/refresh")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "started")
        self.assertIn("Parallel refresh", data["message"])

        mock_bg_run.assert_called_once()

    def test_06_central_library_left_sidebar(self):
        """Test GET /library renders left category sidebar instead of dropdown."""
        source = Source(
            name="TestTV",
            type="xtream",
            url="http://test.tv:8080",
            username="u",
            password="p",
            enabled=True,
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        ch1 = Channel(
            source_id=source.id,
            raw_name="Sky Sports",
            display_name="Sky Sports",
            raw_group="Sports",
            display_group="Sports",
            is_hidden=False,
        )
        ch2 = Channel(
            source_id=source.id,
            raw_name="BBC News",
            display_name="BBC News",
            raw_group="News",
            display_group="News",
            is_hidden=False,
        )
        self.db.add_all([ch1, ch2])
        self.db.commit()

        res = self.client.get("/library?tab=live")
        self.assertEqual(res.status_code, 200)

        # Check for left sidebar elements
        self.assertIn("category-sidebar-card", res.text)
        self.assertIn("categorySidebarList", res.text)
        self.assertIn("Filter groups...", res.text)
        self.assertIn("All Categories", res.text)
        self.assertIn("Sports", res.text)
        self.assertIn("News", res.text)

        # Check for footer banner
        self.assertIn("preamFooterBanner", res.text)
        self.assertIn("footerStatusDot", res.text)
        self.assertIn("footerTaskTitle", res.text)

    def test_07_light_theme(self):
        """Test that light mode theme is active across the application."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-bs-theme="light"', res.text)
        self.assertIn("bootswatch@5.3.3/dist/zephyr", res.text)

    def test_08_dashboard_stream_totals(self):
        """Test Dashboard displays visible count out of total streams combined."""
        src = Source(name="Alpha", type="xtream", url="http://alpha.tv:8080", username="u", password="p", enabled=True)
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)

        # 2 visible channels, 1 hidden channel
        ch1 = Channel(source_id=src.id, raw_name="Ch 1", display_name="Ch 1", is_hidden=False)
        ch2 = Channel(source_id=src.id, raw_name="Ch 2", display_name="Ch 2", is_hidden=False)
        ch3 = Channel(source_id=src.id, raw_name="Ch 3", display_name="Ch 3", is_hidden=True)
        self.db.add_all([ch1, ch2, ch3])
        self.db.commit()

        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        # Visible channels = 2, total = 3
        self.assertIn("out of <span class=\"fw-semibold text-dark\">3</span> total streams", res.text)
        self.assertIn("total all streams", res.text)

    def test_09_edit_source(self):
        """Test PUT /api/sources/{id} updates source attributes."""
        src = Source(name="Old Name", type="xtream", url="http://old.tv:8080", username="user1", password="pass1", enabled=True)
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)

        update_payload = {
            "name": "New Name",
            "type": "xtream",
            "url": "http://new.tv:8080",
            "username": "user2",
            "password": "pass2",
            "enabled": False,
            "user_agent": "CustomAgent/1.0",
        }
        res = self.client.put(f"/api/sources/{src.id}", json=update_payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["name"], "New Name")
        self.assertEqual(data["url"], "http://new.tv:8080")
        self.assertEqual(data["enabled"], False)

        # Confirm from DB
        self.db.expire_all()
        reloaded = self.db.query(Source).filter(Source.id == src.id).first()
        self.assertEqual(reloaded.name, "New Name")
        self.assertEqual(reloaded.username, "user2")

    def test_10_edit_rule(self):
        """Test PUT /api/rules/{id} modifies rule configuration."""
        from app.models.rule import Rule
        rule = Rule(
            order=0,
            rule_type="rename_channel",
            content_type="live",
            match_field="name",
            match_pattern=r"^UK:\s*",
            replacement="",
            description="Prefix Clean",
            enabled=True,
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)

        update_payload = {
            "rule_type": "rename_channel",
            "content_type": "live",
            "match_field": "name",
            "match_pattern": r"^US:\s*",
            "replacement": "[US] ",
            "description": "Updated Clean Rule",
            "enabled": True,
        }
        res = self.client.put(f"/api/rules/{rule.id}", json=update_payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["description"], "Updated Clean Rule")
        self.assertEqual(data["match_pattern"], r"^US:\s*")
        self.assertEqual(data["replacement"], "[US] ")

        self.db.expire_all()
        reloaded = self.db.query(Rule).filter(Rule.id == rule.id).first()
        self.assertEqual(reloaded.description, "Updated Clean Rule")
        self.assertEqual(reloaded.replacement, "[US] ")

    def test_11_rules_affected_count_and_task_status(self):
        """Test applying rules updates affected_count and logs footer task status."""
        from app.models.rule import Rule
        src = Source(name="Beta", type="xtream", url="http://beta.tv:8080", username="u", password="p", enabled=True)
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)

        ch = Channel(source_id=src.id, raw_name="UK: BBC One", display_name="UK: BBC One", is_hidden=False)
        self.db.add(ch)
        self.db.commit()

        rule = Rule(
            order=0,
            rule_type="rename_channel",
            content_type="live",
            match_field="name",
            match_pattern=r"^UK:\s*",
            replacement="",
            description="Remove UK Prefix",
            enabled=True,
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)

        # Trigger POST /api/rules/apply
        res = self.client.post("/api/rules/apply")
        self.assertEqual(res.status_code, 200)

        # Verify rule affected count is updated
        self.db.expire_all()
        reloaded_rule = self.db.query(Rule).filter(Rule.id == rule.id).first()
        self.assertEqual(reloaded_rule.affected_count, 1)

        # Verify task status was set and finished
        st = get_task_status()
        self.assertEqual(st["task"], "Complete")
        self.assertIn("Rules applied:", st["message"])
        self.assertEqual(st["status"], "completed")

    def test_12_rules_diff_preview(self):
        """Test GET /api/rules/diff returns original vs filtered items and groups with changed flags."""
        src = Source(name="Gamma", type="xtream", url="http://gamma.tv:8080", username="u", password="p", enabled=True)
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)

        ch1 = Channel(
            source_id=src.id,
            raw_name="HD: Sports 1",
            display_name="Sports 1",
            raw_group="RAW_SPORTS",
            display_group="SPORTS",
            is_hidden=False,
        )
        ch2 = Channel(
            source_id=src.id,
            raw_name="Movie Channel",
            display_name="Movie Channel",
            raw_group="MOVIES",
            display_group="MOVIES",
            is_hidden=False,
        )
        self.db.add_all([ch1, ch2])
        self.db.commit()

        # Test channels scope
        res = self.client.get("/api/rules/diff?scope=channels")
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 2)
        # Find ch1
        item1 = next((it for it in items if it["id"] == ch1.id), None)
        self.assertIsNotNone(item1)
        self.assertTrue(item1["changed"])
        self.assertEqual(item1["raw_name"], "HD: Sports 1")
        self.assertEqual(item1["display_name"], "Sports 1")

        # Test groups scope
        res_grp = self.client.get("/api/rules/diff?scope=groups")
        self.assertEqual(res_grp.status_code, 200)
        grp_items = res_grp.json()
        self.assertIsInstance(grp_items, list)
        self.assertGreaterEqual(len(grp_items), 1)
        grp1 = next((g for g in grp_items if g["raw_name"] == "RAW_SPORTS"), None)
        self.assertIsNotNone(grp1)
        self.assertEqual(grp1["display_name"], "SPORTS")
        self.assertTrue(grp1["changed"])

    def test_13_series_episodes_modal_api(self):
        """Test GET /api/library/series/{series_id}/episodes returns episodes grouped by season."""
        from app.models.series import Series, Episode

        src = Source(name="Delta", type="xtream", url="http://delta.tv:8080", username="u", password="p", enabled=True)
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)

        series = Series(
            source_id=src.id,
            raw_name="Breaking Bad",
            display_name="Breaking Bad",
            raw_group="Drama",
            display_group="Drama",
            is_hidden=False,
        )
        self.db.add(series)
        self.db.commit()
        self.db.refresh(series)

        ep1 = Episode(
            series_id=series.id,
            source_id=src.id,
            title="Pilot",
            season_num=1,
            episode_num=1,
        )
        ep2 = Episode(
            series_id=series.id,
            source_id=src.id,
            title="Cat's in the Bag...",
            season_num=1,
            episode_num=2,
        )
        self.db.add_all([ep1, ep2])
        self.db.commit()

        res = self.client.get(f"/api/library/series/{series.id}/episodes")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["id"], series.id)
        self.assertEqual(data["name"], "Breaking Bad")
        self.assertEqual(data["total_episodes"], 2)
        self.assertIn("1", data["seasons"])
        self.assertEqual(len(data["seasons"]["1"]), 2)
        self.assertEqual(data["seasons"]["1"][0]["title"], "Pilot")

    def test_14_library_floating_action_btn(self):
        """Test library template contains floating action buttons for stylish interaction."""
        src = Source(name="Epsilon", type="xtream", url="http://eps.tv:8080", username="u", password="p", enabled=True)
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)

        from app.models.movie import Movie
        m = Movie(
            source_id=src.id,
            raw_name="Inception",
            display_name="Inception",
            raw_group="Sci-Fi",
            display_group="Sci-Fi",
            is_hidden=False,
        )
        self.db.add(m)
        self.db.commit()

        res = self.client.get("/library?tab=vod")
        self.assertEqual(res.status_code, 200)
        self.assertIn("floating-action-btn", res.text)

    def test_15_source_test_endpoint_xtream_success(self):
        """Test POST /api/sources/test successfully authenticates and retrieves account info."""
        mock_response = unittest.mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user_info": {
                "auth": 1,
                "status": "Active",
                "exp_date": "1893456000",  # ~2030
                "max_connections": "3",
                "active_cons": "1",
            }
        }

        with unittest.mock.patch("httpx.AsyncClient.get", return_value=mock_response):
            res = self.client.post(
                "/api/sources/test",
                json={
                    "type": "xtream",
                    "url": "http://upstream.provider:8080",
                    "username": "user123",
                    "password": "pass123",
                },
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["max_connections"], "3")
            self.assertEqual(data["active_connections"], "1")
            self.assertIsNotNone(data["days_remaining"])
            self.assertGreater(data["days_remaining"], 0)

    def test_16_source_test_endpoint_xtream_auth_failure(self):
        """Test POST /api/sources/test handles auth failure gracefully."""
        mock_response = unittest.mock.MagicMock()
        mock_response.status_code = 401

        with unittest.mock.patch("httpx.AsyncClient.get", return_value=mock_response):
            res = self.client.post(
                "/api/sources/test",
                json={
                    "type": "xtream",
                    "url": "http://upstream.provider:8080",
                    "username": "baduser",
                    "password": "badpassword",
                },
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "error")
            self.assertIn("Authentication failed", data["message"])

    def test_17_source_expiry_and_connection_properties(self):
        """Test Source.expiry_info and Source.connections_display properties."""
        now_ts = int(time.time())
        # Source 1: Future expiry (10 days from now)
        future_ts = str(now_ts + 86400 * 10)
        s1 = Source(
            name="Source 1",
            type="xtream",
            url="http://s1.test",
            expiry_date=future_ts,
            max_connections="5",
        )
        self.assertFalse(s1.expiry_info["is_expired"])
        self.assertIn("d left", s1.expiry_info["label"])
        self.assertEqual(s1.connections_display, "5 conn")

        # Source 2: Expired in the past
        past_ts = str(now_ts - 86400 * 5)
        s2 = Source(
            name="Source 2",
            type="xtream",
            url="http://s2.test",
            expiry_date=past_ts,
            max_connections=None,
        )
        self.assertTrue(s2.expiry_info["is_expired"])
        self.assertEqual(s2.expiry_info["label"], "Expired")
        self.assertEqual(s2.connections_display, "Unlimited")

    def test_18_concurrent_source_refresh_in_memory_rules(self):
        """Test concurrent source refresh applies rules directly in-memory and stores account info."""
        import asyncio
        from app.models.rule import Rule
        from app.services.ingestion.manager import refresh_source
        from app.services.ingestion.xtream import IngestResult

        # Add active rule to hide any Adult channel
        rule = Rule(
            order=1,
            rule_type="hide_channel",
            content_type="all",
            match_field="name",
            match_pattern=r"Adult|XXX",
            enabled=True,
        )
        self.db.add(rule)

        # Create two sources
        s1 = Source(name="Alpha", type="xtream", url="http://alpha.tv", username="a", password="a", enabled=True)
        s2 = Source(name="Beta", type="xtream", url="http://beta.tv", username="b", password="b", enabled=True)
        self.db.add_all([s1, s2])
        self.db.commit()

        mock_result_alpha = IngestResult(
            status="ok",
            expiry_date="1900000000",
            max_connections="2",
            channels=[
                {"source_stream_id": "1", "raw_name": "BBC News", "raw_group": "News", "stream_url": "http://alpha/1.ts"},
                {"source_stream_id": "2", "raw_name": "Adult XXX Channel", "raw_group": "Adult", "stream_url": "http://alpha/2.ts"},
            ],
        )

        mock_result_beta = IngestResult(
            status="ok",
            expiry_date="1950000000",
            max_connections="5",
            channels=[
                {"source_stream_id": "3", "raw_name": "Sky Sports", "raw_group": "Sports", "stream_url": "http://beta/3.ts"},
                {"source_stream_id": "4", "raw_name": "XXX Late Night", "raw_group": "Adult", "stream_url": "http://beta/4.ts"},
            ],
        )

        async def _run_concurrent():
            with unittest.mock.patch("app.services.ingestion.manager.fetch_xtream", side_effect=[mock_result_alpha, mock_result_beta]):
                res1 = await refresh_source(s1.id, self.db)
                res2 = await refresh_source(s2.id, self.db)
                return res1, res2

        asyncio.run(_run_concurrent())

        # Check Alpha items were filtered immediately
        ch_bbc = self.db.query(Channel).filter(Channel.source_stream_id == "1").first()
        ch_adult1 = self.db.query(Channel).filter(Channel.source_stream_id == "2").first()
        self.assertIsNotNone(ch_bbc)
        self.assertFalse(ch_bbc.is_hidden)
        self.assertIsNotNone(ch_adult1)
        self.assertTrue(ch_adult1.is_hidden)  # Hidden by in-memory rule!

        # Check Beta items were filtered immediately
        ch_sky = self.db.query(Channel).filter(Channel.source_stream_id == "3").first()
        ch_adult2 = self.db.query(Channel).filter(Channel.source_stream_id == "4").first()
        self.assertIsNotNone(ch_sky)
        self.assertFalse(ch_sky.is_hidden)
        self.assertIsNotNone(ch_adult2)
        self.assertTrue(ch_adult2.is_hidden)  # Hidden by in-memory rule!

        # Check account info was saved
        self.db.refresh(s1)
        self.db.refresh(s2)
        self.assertEqual(s1.expiry_date, "1900000000")
        self.assertEqual(s1.max_connections, "2")
        self.assertEqual(s2.expiry_date, "1950000000")
        self.assertEqual(s2.max_connections, "5")

    def test_19_rules_page_db_locked_overlay(self):
        """Test rules page renders visual banner when database is locked rather than raising 500 error."""
        from sqlalchemy.exc import OperationalError
        with unittest.mock.patch("sqlalchemy.orm.Session.query", side_effect=OperationalError("database is locked", None, None)):
            res = self.client.get("/rules")
            self.assertEqual(res.status_code, 200)
            self.assertIn("Database Ingestion in Progress", " ".join(res.text.split()))
            self.assertIn("reloadCount", res.text)

    def test_20_rules_diff_movie_and_series_groups(self):
        """Test rules diff preview and rule execution for Movie Groups and Series Groups."""
        from app.models.movie import Movie
        from app.models.series import Series
        from app.models.rule import Rule
        from app.services.rules_engine import apply_all_rules_to_library

        # Seed sample movies and series
        mv1 = Movie(
            source_id=1,
            source_stream_id="m1",
            raw_name="Avatar 2",
            raw_group="Sci-Fi Movies",
            display_name="Avatar 2",
            display_group="Sci-Fi Movies",
            stream_url="http://src/m1.mp4",
            container_ext="mp4",
            is_hidden=False,
        )
        mv2 = Movie(
            source_id=1,
            source_stream_id="m2",
            raw_name="Dune Part Two",
            raw_group="Sci-Fi Movies",
            display_name="Dune Part Two",
            display_group="Sci-Fi Movies",
            stream_url="http://src/m2.mp4",
            container_ext="mp4",
            is_hidden=False,
        )
        sr1 = Series(
            source_id=1,
            source_series_id="s1",
            raw_name="Severance",
            raw_group="Thriller Series",
            display_name="Severance",
            display_group="Thriller Series",
            is_hidden=False,
        )
        self.db.add_all([mv1, mv2, sr1])
        self.db.commit()

        # 1. Test diff preview before rules
        res_mv_groups = self.client.get("/api/rules/diff?scope=movie_groups")
        self.assertEqual(res_mv_groups.status_code, 200)
        mv_data = res_mv_groups.json()
        self.assertEqual(len(mv_data), 1)
        self.assertEqual(mv_data[0]["raw_name"], "Sci-Fi Movies")
        self.assertEqual(mv_data[0]["raw_group"], "Movie Group (2 movies)")

        res_sr_groups = self.client.get("/api/rules/diff?scope=series_groups")
        self.assertEqual(res_sr_groups.status_code, 200)
        sr_data = res_sr_groups.json()
        self.assertEqual(len(sr_data), 1)
        self.assertEqual(sr_data[0]["raw_name"], "Thriller Series")
        self.assertEqual(sr_data[0]["raw_group"], "Series Group (1 series)")

        # 2. Add rules targeting movie groups and series groups
        r_movie = Rule(
            order=1,
            rule_type="rename_group",
            content_type="vod",
            match_field="group",
            match_pattern=r"Sci-Fi Movies",
            replacement="Futuristic Sci-Fi",
            enabled=True,
        )
        r_series = Rule(
            order=2,
            rule_type="hide_group",
            content_type="series",
            match_field="group",
            match_pattern=r"Thriller Series",
            enabled=True,
        )
        self.db.add_all([r_movie, r_series])
        self.db.commit()

        # 3. Apply rules to library
        stats = apply_all_rules_to_library(self.db)
        self.assertGreaterEqual(stats["movies_processed"], 2)
        self.assertGreaterEqual(stats["series_processed"], 1)
        self.assertGreaterEqual(stats["series_hidden"], 1)

        # 4. Verify DB items updated
        self.db.refresh(mv1)
        self.db.refresh(mv2)
        self.db.refresh(sr1)
        self.assertEqual(mv1.display_group, "Futuristic Sci-Fi")
        self.assertEqual(mv2.display_group, "Futuristic Sci-Fi")
        self.assertTrue(sr1.is_hidden)

        # 5. Check updated diff preview reflects renamed movie group and hidden series group
        res_mv_diff = self.client.get("/api/rules/diff?scope=movie_groups")
        mv_diff = res_mv_diff.json()
        self.assertTrue(mv_diff[0]["is_renamed"])
        self.assertEqual(mv_diff[0]["display_name"], "Futuristic Sci-Fi")

        res_sr_diff = self.client.get("/api/rules/diff?scope=series_groups")
        sr_diff = res_sr_diff.json()
        self.assertTrue(sr_diff[0]["is_hidden"])
        self.assertIn("0 active / 1 hidden", sr_diff[0]["display_group"])

        # 6. Test all_groups scope
        res_all_groups = self.client.get("/api/rules/diff?scope=all_groups")
        self.assertEqual(res_all_groups.status_code, 200)
        all_diff = res_all_groups.json()
        group_names = [d["raw_name"] for d in all_diff]
        self.assertIn("Sci-Fi Movies", group_names)
        self.assertIn("Thriller Series", group_names)

    def test_21_stream_health_check_and_reprioritization(self):
        """Test stream health check probes streams, re-prioritizes sources, and sends Telegram report."""
        import asyncio
        from unittest.mock import patch, AsyncMock
        from app.models.source import Source
        from app.models.channel import Channel
        from app.models.movie import Movie

        # Create 2 enabled sources
        src_a = Source(name="Provider LowQuality", type="xtream", url="http://low.quality", priority=1, enabled=True)
        src_b = Source(name="Provider HighQuality", type="xtream", url="http://high.quality", priority=2, enabled=True)
        self.db.add_all([src_a, src_b])
        self.db.commit()

        # Add streams
        ch_a = Channel(source_id=src_a.id, raw_name="Ch A", display_name="Ch A", stream_url="http://low/1.ts")
        mv_a = Movie(source_id=src_a.id, raw_name="Mv A", display_name="Mv A", stream_url="http://low/m1.mp4")
        ch_b = Channel(source_id=src_b.id, raw_name="Ch B", display_name="Ch B", stream_url="http://high/1.ts")
        mv_b = Movie(source_id=src_b.id, raw_name="Mv B", display_name="Mv B", stream_url="http://high/m1.mp4")
        self.db.add_all([ch_a, mv_a, ch_b, mv_b])
        self.db.commit()

        async def mock_probe(client, stream_url, user_agent, timeout=6.0):
            if "low" in stream_url:
                return False, 500.0, "Connection refused"
            return True, 120.0, None

        with patch("app.services.stream_health.probe_stream", side_effect=mock_probe), \
             patch("app.services.stream_health.send_telegram_message", new_callable=AsyncMock) as mock_tg:
            mock_tg.return_value = True

            res = self.client.post("/api/sources/health-check?count=2")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["sample_count"], 2)
            self.assertTrue(data["telegram_sent"])
            mock_tg.assert_called_once()
            report_text = mock_tg.call_args[0][0]
            self.assertIn("Stream Health & Prioritization Report", report_text)
            self.assertIn("Provider HighQuality", report_text)

            # Check that Provider HighQuality was promoted to priority 1
            self.db.refresh(src_a)
            self.db.refresh(src_b)
            self.assertEqual(src_b.priority, 1)  # Promoted to #1
            self.assertEqual(src_a.priority, 2)  # Demoted to #2

    def test_22_settings_actions_and_stream_health_config(self):
        """Test Settings page displays Actions section and saves stream_health config."""
        # 1. Verify GET /settings includes Actions and stream_health
        res = self.client.get("/settings")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Actions", res.text)
        self.assertIn("Refresh Sources", res.text)
        self.assertIn("Run Stream Health Check", res.text)
        self.assertIn("stream_health_count", res.text)

        # 2. Verify top bar in base.html does NOT contain the removed navbar refresh button
        # The navbar in base.html should not have triggerRefreshAll
        self.assertNotIn('<button class="btn btn-sm btn-outline-light" onclick="triggerRefreshAll(this)">', res.text)

        # 3. Test POST /settings updates stream health settings
        post_res = self.client.post(
            "/settings",
            data={
                "stream_health": "on",
                "stream_health_count": "3",
                "refresh_cron": "0 2 * * *",
            },
            follow_redirects=True,
        )
        self.assertEqual(post_res.status_code, 200)
        from app.models.setting import Setting
        sh = self.db.query(Setting).filter(Setting.key == "stream_health").first()
        shc = self.db.query(Setting).filter(Setting.key == "stream_health_count").first()
        self.assertIsNotNone(sh)
        self.assertEqual(sh.value, "true")
        self.assertIsNotNone(shc)
        self.assertEqual(shc.value, "3")

    def test_23_source_deletion_counts_endpoint(self):
        """Test GET /api/sources/{id}/counts returns accurate item breakdown for deletion confirmation."""
        from app.models.channel import Channel
        from app.models.movie import Movie
        from app.models.series import Series

        src = Source(name="TestSrc", type="xtream", url="http://test.url", enabled=True)
        self.db.add(src)
        self.db.commit()

        self.db.add(Channel(source_id=src.id, raw_name="Ch1", display_name="Ch1"))
        self.db.add(Channel(source_id=src.id, raw_name="Ch2", display_name="Ch2"))
        self.db.add(Movie(source_id=src.id, raw_name="Mv1", display_name="Mv1"))
        self.db.add(Series(source_id=src.id, raw_name="Sr1", display_name="Sr1"))
        self.db.commit()

        res = self.client.get(f"/api/sources/{src.id}/counts")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["source_id"], src.id)
        self.assertEqual(data["channels"], 2)
        self.assertEqual(data["movies"], 1)
        self.assertEqual(data["series"], 1)
        self.assertEqual(data["total"], 4)

    def test_24_multi_worker_task_progress_tracking(self):
        """Test multi-worker task status tracking across parallel threads."""
        from app.services.task_status import (
            clear_all_workers,
            get_task_status,
            remove_worker,
            update_worker_progress,
        )

        clear_all_workers()
        update_worker_progress("worker_1", "Source A", "Connecting...", 20)
        update_worker_progress("worker_2", "Source B", "Saving streams...", 80)

        st = get_task_status()
        self.assertEqual(st["status"], "running")
        self.assertEqual(len(st["workers"]), 2)
        self.assertEqual(st["progress"], 50)  # average of 20 and 80

        # Remove worker_1
        remove_worker("worker_1")
        st2 = get_task_status()
        self.assertEqual(len(st2["workers"]), 1)
        self.assertEqual(st2["workers"][0]["id"], "worker_2")

        clear_all_workers()
        st3 = get_task_status()
        self.assertEqual(len(st3["workers"]), 0)

    def test_25_central_library_group_sources_and_search_elements(self):
        """Test Central Library includes group source badges and search functions."""
        from app.models.channel import Channel

        src1 = Source(name="SrcOne", type="xtream", url="http://s1.test", enabled=True)
        src2 = Source(name="SrcTwo", type="xtream", url="http://s2.test", enabled=True)
        self.db.add_all([src1, src2])
        self.db.commit()

        self.db.add(Channel(source_id=src1.id, raw_name="Ch1", display_name="Ch1", display_group="Sports", is_hidden=False))
        self.db.add(Channel(source_id=src2.id, raw_name="Ch2", display_name="Ch2", display_group="Sports", is_hidden=False))
        self.db.commit()

        res = self.client.get("/library?tab=live")
        self.assertEqual(res.status_code, 200)
        self.assertIn("categorySidebarList", res.text)
        self.assertIn("Sports", res.text)
        self.assertIn("2 sources", res.text)  # Shows multi-source badge in sidebar

    def test_26_rules_accordion_and_dark_mode_settings(self):
        """Test Rules Engine accordion and Settings Dark Mode toggle."""
        # Rules page has accordion
        res_rules = self.client.get("/rules")
        self.assertEqual(res_rules.status_code, 200)
        self.assertIn("rulesPipelineAccordion", res_rules.text)
        self.assertIn("collapsePipeline", res_rules.text)

        # Settings page has Dark Mode toggle
        res_settings = self.client.get("/settings")
        self.assertEqual(res_settings.status_code, 200)
        self.assertIn("darkModeToggle", res_settings.text)
        self.assertIn("Theme & Appearance", res_settings.text)


if __name__ == "__main__":
    unittest.main()


