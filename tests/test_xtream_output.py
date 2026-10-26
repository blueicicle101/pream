"""
Tests for Phase 4: Xtream API Output, Stream Proxy (HTTP 302), and M3U Output.
"""
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
from app.models.series import Series
from app.models.source import Source

test_engine = create_engine("sqlite:///./data/test_xtream_out.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class XtreamOutputTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_xtream_out.db"):
            try:
                os.remove("./data/test_xtream_out.db")
            except Exception:
                pass

    def setUp(self):
        self.db = TestingSessionLocal()
        self.db.query(Channel).delete()
        self.db.query(Movie).delete()
        self.db.query(Series).delete()
        self.db.query(Source).delete()
        self.db.commit()

        # Seed data
        self.source = Source(
            name="AlphaProvider",
            type="xtream",
            url="http://provider.upstream:8080",
            username="u",
            password="p",
            enabled=True,
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.source)

        self.ch = Channel(
            source_id=self.source.id,
            source_stream_id="101",
            raw_name="BBC One HD",
            raw_group="UK | Entertainment",
            display_name="BBC One HD",
            display_group="UK | Entertainment",
            logo_url="http://img.png",
            epg_channel_id="bbcone",
            stream_url="http://provider.upstream:8080/live/u/p/101.ts",
            is_hidden=False,
        )
        self.hidden_ch = Channel(
            source_id=self.source.id,
            source_stream_id="102",
            raw_name="Adult 1",
            raw_group="Adult",
            display_name="Adult 1",
            display_group="Adult",
            stream_url="http://provider.upstream:8080/live/u/p/102.ts",
            is_hidden=True,
        )
        self.movie = Movie(
            source_id=self.source.id,
            source_stream_id="201",
            raw_name="Avatar",
            raw_group="Sci-Fi",
            display_name="Avatar (2009)",
            display_group="Sci-Fi",
            poster_url="http://poster.png",
            rating=8.0,
            container_ext="mp4",
            stream_url="http://provider.upstream:8080/movie/u/p/201.mp4",
            is_hidden=False,
        )
        self.db.add_all([self.ch, self.hidden_ch, self.movie])
        self.db.commit()
        self.db.refresh(self.ch)
        self.db.refresh(self.movie)

    def tearDown(self):
        self.db.close()

    def test_01_player_api_auth(self):
        """Test authentication for Xtream client."""
        # 1. Invalid credentials
        res = self.client.get("/player_api.php?username=bad&password=bad")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["user_info"]["auth"], 0)

        # 2. Valid credentials
        res = self.client.get(
            f"/player_api.php?username={settings.OUTPUT_USERNAME}&password={settings.OUTPUT_PASSWORD}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["user_info"]["auth"], 1)
        self.assertIn("server_info", data)

    def test_02_get_live_categories_and_streams(self):
        """Test live categories and live streams filtering out hidden items."""
        u = settings.OUTPUT_USERNAME
        p = settings.OUTPUT_PASSWORD

        # Categories
        res = self.client.get(f"/player_api.php?username={u}&password={p}&action=get_live_categories")
        self.assertEqual(res.status_code, 200)
        cats = res.json()
        # Should only have UK | Entertainment, not the hidden Adult group
        cat_names = [c["category_name"] for c in cats]
        self.assertIn("UK | Entertainment", cat_names)
        self.assertNotIn("Adult", cat_names)

        # Streams
        res = self.client.get(f"/player_api.php?username={u}&password={p}&action=get_live_streams")
        self.assertEqual(res.status_code, 200)
        streams = res.json()
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["name"], "BBC One HD")

    def test_03_get_vod_categories_and_streams(self):
        """Test VOD categories and streams."""
        u = settings.OUTPUT_USERNAME
        p = settings.OUTPUT_PASSWORD

        res = self.client.get(f"/player_api.php?username={u}&password={p}&action=get_vod_categories")
        self.assertEqual(res.status_code, 200)
        cats = res.json()
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["category_name"], "Sci-Fi")

        res = self.client.get(f"/player_api.php?username={u}&password={p}&action=get_vod_streams")
        self.assertEqual(res.status_code, 200)
        streams = res.json()
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["name"], "Avatar (2009)")

    def test_04_stream_proxy_redirect_302(self):
        """Test live and movie streams issue HTTP 302 redirect directly to provider."""
        u = settings.OUTPUT_USERNAME
        p = settings.OUTPUT_PASSWORD

        # Live stream redirect (follow_redirects=False to inspect 302 status and location)
        res = self.client.get(f"/live/{u}/{p}/{self.ch.id}.ts", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["location"], self.ch.stream_url)

        # Movie stream redirect
        res = self.client.get(f"/movie/{u}/{p}/{self.movie.id}.mp4", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["location"], self.movie.stream_url)

        # Hidden channel should return 404
        res = self.client.get(f"/live/{u}/{p}/{self.hidden_ch.id}.ts", follow_redirects=False)
        self.assertEqual(res.status_code, 404)

        # Bad credentials should return 403
        res = self.client.get(f"/live/bad/bad/{self.ch.id}.ts", follow_redirects=False)
        self.assertEqual(res.status_code, 403)

    def test_05_m3u_output(self):
        """Test /get.php generates standard M3U playlist with direct provider stream URLs and fallback."""
        u = settings.OUTPUT_USERNAME
        p = settings.OUTPUT_PASSWORD

        # Add a channel without stream_url to test gateway fallback
        fallback_ch = Channel(
            source_id=self.source.id,
            source_stream_id="103",
            raw_name="Fallback Channel",
            raw_group="UK | Entertainment",
            display_name="Fallback Channel",
            display_group="UK | Entertainment",
            stream_url=None,
            is_hidden=False,
        )
        self.db.add(fallback_ch)
        self.db.commit()
        self.db.refresh(fallback_ch)

        res = self.client.get(f"/get.php?username={u}&password={p}&type=m3u_plus")
        self.assertEqual(res.status_code, 200)
        content = res.text
        self.assertTrue(content.startswith("#EXTM3U"))
        self.assertIn("BBC One HD", content)
        self.assertIn("Avatar (2009)", content)
        self.assertNotIn("Adult 1", content)
        # Direct provider stream URL used when available
        self.assertIn(self.ch.stream_url, content)
        self.assertIn(self.movie.stream_url, content)
        # Gateway fallback URL used when stream_url is empty
        self.assertIn(f"/live/{u}/{p}/{fallback_ch.id}.ts", content)


if __name__ == "__main__":
    unittest.main()
