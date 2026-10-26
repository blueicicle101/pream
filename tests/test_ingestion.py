"""
Ingestion & Sources Integration Tests.
Uses unittest and FastAPI TestClient with an isolated test SQLite DB.
"""
import os
import unittest
from datetime import datetime

# Set temporary test database
os.environ["DATABASE_URL"] = "sqlite:///./data/test_pream.db"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
import app.models  # noqa: F401
from app.main import app
from app.services.ingestion.m3u import parse_m3u_content
from app.services.ingestion.user_agents import get_user_agent
from app.services.ingestion.xtream import clean_base_url

# Setup test DB engine
test_engine = create_engine("sqlite:///./data/test_pream.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class IngestionTestCase(unittest.TestCase):
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
        if os.path.exists("./data/test_pream.db"):
            try:
                os.remove("./data/test_pream.db")
            except Exception:
                pass

    def test_01_user_agent_logic(self):
        """Test sticky user-agent when provided and random when not."""
        sticky_ua = "MyCustomPlayer/1.0"
        self.assertEqual(get_user_agent(sticky_ua), sticky_ua)

        # When None or empty, should return a valid non-empty string from pool
        rand_ua = get_user_agent(None)
        self.assertTrue(len(rand_ua) > 5)

        rand_ua2 = get_user_agent("")
        self.assertTrue(len(rand_ua2) > 5)

    def test_02_clean_base_url(self):
        """Test URL normalization."""
        self.assertEqual(clean_base_url("http://example.com:8080/"), "http://example.com:8080")
        self.assertEqual(clean_base_url("http://example.com/player_api.php"), "http://example.com")
        self.assertEqual(clean_base_url("example.com:8080"), "http://example.com:8080")

    def test_03_m3u_parser(self):
        """Test M3U content parsing for channels, movies, and series."""
        sample_m3u = """#EXTM3U
#EXTINF:-1 tvg-id="bbc1.uk" tvg-name="BBC One HD" tvg-logo="http://logo.png" group-title="UK | Entertainment",BBC One HD
http://example.com/live/user/pass/101.ts
#EXTINF:-1 tvg-id="102" tvg-name="Inception (2010)" tvg-logo="http://poster.png" group-title="EN | Movies",Inception (2010)
http://example.com/movie/user/pass/201.mp4
#EXTINF:-1 tvg-id="103" tvg-name="Breaking Bad S01E01" tvg-logo="http://cover.png" group-title="EN | Series",Breaking Bad S01E01
http://example.com/series/user/pass/301.mp4
"""
        result = parse_m3u_content(sample_m3u, source_id=1, content_types=["live", "vod", "series"])
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.channels), 1)
        self.assertEqual(len(result.movies), 1)
        self.assertEqual(len(result.series), 1)

        # Check raw preservation
        ch = result.channels[0]
        self.assertEqual(ch["raw_name"], "BBC One HD")
        self.assertEqual(ch["raw_group"], "UK | Entertainment")
        self.assertEqual(ch["epg_channel_id"], "bbc1.uk")

        mv = result.movies[0]
        self.assertEqual(mv["raw_name"], "Inception (2010)")
        self.assertEqual(mv["container_ext"], "mp4")

        sr = result.series[0]
        self.assertEqual(sr["raw_name"], "Breaking Bad S01E01")

    def test_04_sources_api_crud(self):
        """Test Source creation, listing, updating, and deletion via API."""
        # 1. Create source
        payload = {
            "name": "Provider Alpha",
            "type": "xtream",
            "url": "http://alpha.example.com:8080",
            "username": "user123",
            "password": "pass123",
            "content_types": ["live", "vod"],
            "priority": 1,
            "user_agent": "CustomTiviMate/1.0",
            "enabled": True,
        }
        res = self.client.post("/api/sources", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["name"], "Provider Alpha")
        self.assertEqual(data["user_agent"], "CustomTiviMate/1.0")
        source_id = data["id"]

        # 2. List sources
        res = self.client.get("/api/sources")
        self.assertEqual(res.status_code, 200)
        sources_list = res.json()
        self.assertEqual(len(sources_list), 1)
        self.assertEqual(sources_list[0]["id"], source_id)

        # 3. Get single source
        res = self.client.get(f"/api/sources/{source_id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "Provider Alpha")

        # 4. Update source
        update_payload = {"name": "Provider Alpha Updated", "priority": 5}
        res = self.client.put(f"/api/sources/{source_id}", json=update_payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "Provider Alpha Updated")
        self.assertEqual(res.json()["priority"], 5)

        # 5. Delete source
        res = self.client.delete(f"/api/sources/{source_id}")
        self.assertEqual(res.status_code, 204)

        # Verify deleted
        res = self.client.get(f"/api/sources/{source_id}")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
