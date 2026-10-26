"""
UI and HTML template rendering integration tests.
"""
import os
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
import app.models  # noqa: F401
from app.main import app
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.setting import Setting
from app.models.source import Source
from app.services.auth import COOKIE_NAME, require_admin

test_engine = create_engine("sqlite:///./data/test_ui.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class UITemplateTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_admin] = lambda: "admin"
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_ui.db"):
            try:
                os.remove("./data/test_ui.db")
            except Exception:
                pass

    def setUp(self):
        self.db = TestingSessionLocal()
        self.db.query(Channel).delete()
        self.db.query(Movie).delete()
        self.db.query(Source).delete()
        self.db.commit()

        # Seed a test source and channel
        self.src = Source(
            name="AlphaTV",
            type="xtream",
            url="http://mock.server:8080",
            username="u",
            password="p",
            enabled=True,
        )
        self.db.add(self.src)
        self.db.commit()
        self.db.refresh(self.src)

        self.ch = Channel(
            source_id=self.src.id,
            raw_name="BBC One",
            display_name="BBC One",
            raw_group="UK",
            display_group="UK",
            is_hidden=False,
        )
        self.db.add(self.ch)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_00_admin_login_and_auth_flow(self):
        """Test unauthenticated redirect to /login, login submission, session cookie, and logout."""
        # Un-override require_admin for this isolated test
        del app.dependency_overrides[require_admin]
        raw_client = TestClient(app)

        try:
            # 1. Unauthenticated request to / redirects to /login
            res = raw_client.get("/", follow_redirects=False)
            self.assertEqual(res.status_code, 307)
            self.assertEqual(res.headers["location"], "/login")

            # 2. GET /login renders login page
            res_login = raw_client.get("/login")
            self.assertEqual(res_login.status_code, 200)
            self.assertIn("Admin Login", res_login.text)
            self.assertIn("admin", res_login.text)

            # 3. Invalid credentials redirect back to /login with error
            res_fail = raw_client.post("/login", data={"username": "admin", "password": "wrongpassword"}, follow_redirects=False)
            self.assertEqual(res_fail.status_code, 303)
            self.assertIn("error=1", res_fail.headers["location"])

            # 4. Valid credentials succeed and set session cookie
            res_success = raw_client.post("/login", data={"username": "admin", "password": "pream"}, follow_redirects=False)
            self.assertEqual(res_success.status_code, 303)
            self.assertEqual(res_success.headers["location"], "/")
            self.assertIn(COOKIE_NAME, res_success.headers.get("set-cookie", ""))

            # 5. Accessing with cookie succeeds
            cookie_val = res_success.cookies[COOKIE_NAME]
            raw_client.cookies.set(COOKIE_NAME, cookie_val)
            res_auth = raw_client.get("/")
            self.assertEqual(res_auth.status_code, 200)
            self.assertIn("Live Channels", res_auth.text)

            # 6. Logout clears cookie
            res_logout = raw_client.get("/logout", follow_redirects=False)
            self.assertEqual(res_logout.status_code, 303)
            self.assertEqual(res_logout.headers["location"], "/login")
        finally:
            # Restore override for subsequent UI tests
            app.dependency_overrides[require_admin] = lambda: "admin"

    def test_01_dashboard_page(self):
        """Test GET / renders dashboard with stats and source table."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers["content-type"])
        self.assertIn("Live Channels", res.text)
        self.assertIn("AlphaTV", res.text)
        self.assertIn("PREAM", res.text)

    def test_02_sources_page(self):
        """Test GET /sources renders sources table and Add Source modal."""
        res = self.client.get("/sources")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Upstream Sources", res.text)
        self.assertIn("AlphaTV", res.text)
        self.assertIn("addSourceModal", res.text)

    def test_03_library_page(self):
        """Test GET /library tabs (live, vod, series)."""
        res_live = self.client.get("/library?tab=live")
        self.assertEqual(res_live.status_code, 200)
        self.assertIn("BBC One", res_live.text)

        res_vod = self.client.get("/library?tab=vod")
        self.assertEqual(res_vod.status_code, 200)
        self.assertIn("Central Media Library", res_vod.text)

        res_series = self.client.get("/library?tab=series")
        self.assertEqual(res_series.status_code, 200)
        self.assertIn("Central Media Library", res_series.text)

    def test_04_rules_page(self):
        """Test GET /rules renders rules table and test sandbox."""
        res = self.client.get("/rules")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Rules Engine", res.text)
        self.assertIn("Rule Test Sandbox", res.text)

    def test_05_settings_page_and_post(self):
        """Test GET /settings and POST /settings updates database values."""
        res = self.client.get("/settings")
        self.assertEqual(res.status_code, 200)
        self.assertIn("System Settings", res.text)

        # Update settings
        post_data = {
            "group_by_source": "on",
            "tmdb_api_key": "my_new_tmdb_key",
            "telegram_bot_token": "bot123456",
            "telegram_chat_id": "-100987654321",
            "refresh_cron": "0 2 * * *",
        }
        post_res = self.client.post("/settings", data=post_data, follow_redirects=False)
        self.assertEqual(post_res.status_code, 303)

        # Check DB
        gbs = self.db.query(Setting).filter(Setting.key == "group_by_source").first()
        self.assertEqual(gbs.value, "true")

        tmdb_key = self.db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
        self.assertEqual(tmdb_key.value, "my_new_tmdb_key")


if __name__ == "__main__":
    unittest.main()
