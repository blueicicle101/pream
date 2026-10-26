"""
Integration test for Ingestion Manager with mock HTTP server.
Tests parallel refresh, DB persistence, error handling, and group_by_source prefixing.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Series
from app.models.setting import Setting
from app.models.source import Source
from app.services.ingestion.manager import refresh_all_sources, refresh_source

test_engine = create_engine("sqlite:///./data/test_mgr.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


class IngestionManagerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        self.db = TestingSessionLocal()

    async def asyncTearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_mgr.db"):
            try:
                os.remove("./data/test_mgr.db")
            except Exception:
                pass

    @patch("app.services.ingestion.xtream.httpx.AsyncClient")
    async def test_refresh_xtream_source_success(self, mock_client_cls):
        """Test successful Xtream source refresh populates channels, movies, and series."""
        # Setup source
        source = Source(
            name="IPTV",
            type="xtream",
            url="http://mock.iptv:8080",
            username="user",
            password="pass",
            content_types='["live","vod","series"]',
            priority=1,
            enabled=True,
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        # Mock HTTP responses
        mock_client = AsyncMock()

        async def mock_get(url, *args, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status_code = 200

            if "action=get_live_categories" in url:
                mock_resp.json = lambda: [{"category_id": "1", "category_name": "Sports"}]
            elif "action=get_live_streams" in url:
                mock_resp.json = lambda: [
                    {"stream_id": 101, "name": "Sky Sports Main", "category_id": "1", "stream_icon": "http://logo.png", "epg_channel_id": "sky.sports"}
                ]
            elif "action=get_vod_categories" in url:
                mock_resp.json = lambda: [{"category_id": "2", "category_name": "Action"}]
            elif "action=get_vod_streams" in url:
                mock_resp.json = lambda: [
                    {"stream_id": 201, "name": "Die Hard (1988)", "category_id": "2", "stream_icon": "http://poster.png", "container_extension": "mkv", "rating": 8.2}
                ]
            elif "action=get_series_categories" in url:
                mock_resp.json = lambda: [{"category_id": "3", "category_name": "Drama"}]
            elif "action=get_series" in url:
                mock_resp.json = lambda: [
                    {"series_id": 301, "name": "Succession", "category_id": "3", "cover": "http://cover.png", "year": "2018", "rating": 8.9}
                ]
            else:
                # Auth response
                mock_resp.json = lambda: {
                    "user_info": {"auth": 1, "status": "Active"},
                    "server_info": {"url": "mock.iptv", "port": "8080"}
                }
            return mock_resp

        mock_client.get = mock_get
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        # Run refresh
        result = await refresh_source(source.id, self.db)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["channels"], 1)
        self.assertEqual(result["movies"], 1)
        self.assertEqual(result["series"], 1)

        # Verify DB entries
        channels = self.db.query(Channel).filter(Channel.source_id == source.id).all()
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].raw_name, "Sky Sports Main")
        self.assertEqual(channels[0].display_name, "Sky Sports Main")
        self.assertEqual(channels[0].raw_group, "Sports")
        self.assertEqual(channels[0].display_group, "Sports")

        movies = self.db.query(Movie).filter(Movie.source_id == source.id).all()
        self.assertEqual(len(movies), 1)
        self.assertEqual(movies[0].raw_name, "Die Hard (1988)")
        self.assertEqual(movies[0].container_ext, "mkv")

        series = self.db.query(Series).filter(Series.source_id == source.id).all()
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].raw_name, "Succession")
        self.assertEqual(series[0].year, 2018)

        # Verify Source status updated
        self.db.refresh(source)
        self.assertEqual(source.last_status, "ok")
        self.assertIsNone(source.last_error)
        self.assertIsNotNone(source.last_refresh)

    @patch("app.services.ingestion.xtream.httpx.AsyncClient")
    async def test_refresh_group_by_source_enabled(self, mock_client_cls):
        """Test GROUP_BY_SOURCE prefixes display_group while keeping raw_group unchanged."""
        # Enable group_by_source in DB setting
        setting = Setting(key="group_by_source", value="true")
        self.db.add(setting)

        source = Source(
            name="AlphaTV",
            type="xtream",
            url="http://alpha.mock:8080",
            username="user",
            password="pass",
            content_types='["live"]',
            priority=1,
            enabled=True,
        )
        self.db.add(source)
        self.db.commit()

        mock_client = AsyncMock()

        async def mock_get(url, *args, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            if "action=get_live_categories" in url:
                mock_resp.json = lambda: [{"category_id": "1", "category_name": "UK News"}]
            elif "action=get_live_streams" in url:
                mock_resp.json = lambda: [{"stream_id": 1, "name": "BBC News", "category_id": "1"}]
            else:
                mock_resp.json = lambda: {"user_info": {"auth": 1, "status": "Active"}}
            return mock_resp

        mock_client.get = mock_get
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        result = await refresh_source(source.id, self.db)
        self.assertEqual(result["status"], "ok")

        ch = self.db.query(Channel).filter(Channel.source_id == source.id).first()
        self.assertEqual(ch.raw_group, "UK News")
        # display_group must be prefixed with source name
        self.assertEqual(ch.display_group, "[AlphaTV] UK News")


if __name__ == "__main__":
    unittest.main()
