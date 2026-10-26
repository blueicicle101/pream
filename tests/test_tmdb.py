"""
Unit and integration tests for TMDB enrichment service.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.movie import Movie
from app.models.series import Series
from app.models.source import Source
from app.services.tmdb import (
    apply_tmdb_id_match,
    choose_best_display_title,
    clean_media_title,
    detect_language_code,
    enrich_movie,
    enrich_pending_media,
    enrich_series,
    is_latin_script,
    sanitize_display_name_and_group,
    search_tmdb_candidates,
)

test_engine = create_engine("sqlite:///./data/test_tmdb.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


class TMDBEnrichmentTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.makedirs("data", exist_ok=True)
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        self.db = TestingSessionLocal()

        self.source = Source(
            name="TestProvider",
            type="xtream",
            url="http://mock:8000",
            enabled=True,
        )
        self.db.add(self.source)
        self.db.commit()

    async def asyncTearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()
        if os.path.exists("./data/test_tmdb.db"):
            try:
                os.remove("./data/test_tmdb.db")
            except Exception:
                pass

    def test_01_clean_media_title_and_language(self):
        """Test dirty release title cleaning and language detection."""
        # Clean title
        title, year = clean_media_title("Inception.2010.1080p.BluRay.x264")
        self.assertEqual(title, "Inception")
        self.assertEqual(year, 2010)

        title, year = clean_media_title("Die Hard (1988) [4K UHD]")
        self.assertEqual(title, "Die Hard")
        self.assertEqual(year, 1988)

        # Language detection
        self.assertEqual(detect_language_code("EN | Action"), "EN")
        self.assertEqual(detect_language_code("NL | Films"), "NL")
        self.assertEqual(detect_language_code("UK - Drama"), "UK")
        self.assertEqual(detect_language_code("Uncategorized"), "EN")

    @patch("app.services.tmdb.httpx.AsyncClient")
    async def test_02_enrich_movie(self, mock_client_cls):
        """Test enriching a movie with mock TMDB API response."""
        movie = Movie(
            source_id=self.source.id,
            raw_name="Gladiator.2000.1080p",
            display_name="Gladiator.2000.1080p",
            raw_group="UK | Movies",
            display_group="UK | Movies",
        )
        self.db.add(movie)
        self.db.commit()

        mock_client = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "results": [
                {
                    "id": 98,
                    "title": "Gladiator",
                    "poster_path": "/gladiator_poster.jpg",
                    "backdrop_path": "/gladiator_bg.jpg",
                    "overview": "A former Roman General sets out to exact vengeance...",
                    "vote_average": 8.5,
                    "release_date": "2000-05-01",
                    "genre_ids": [28, 12, 18],  # Action, Adventure, Drama
                }
            ]
        }
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        success = await enrich_movie(movie, api_key="fake_key")
        self.assertTrue(success)

        self.assertEqual(movie.tmdb_id, 98)
        self.assertEqual(movie.display_name, "Gladiator")
        self.assertEqual(movie.poster_url, "https://image.tmdb.org/t/p/w500/gladiator_poster.jpg")
        self.assertEqual(movie.year, 2000)
        self.assertEqual(movie.rating, 8.5)
        self.assertIn("Action", movie.genre)
        # Regrouped into "<LANG> | <GENRE>"
        self.assertEqual(movie.display_group, "UK | Action")
        # Raw fields intact
        self.assertEqual(movie.raw_name, "Gladiator.2000.1080p")
        self.assertEqual(movie.raw_group, "UK | Movies")
        self.assertIsNotNone(movie.tmdb_fetched_at)

    @patch("app.services.tmdb.httpx.AsyncClient")
    async def test_03_batch_enrich_pending(self, mock_client_cls):
        """Test batch enrichment skipping when no key vs executing when key is present."""
        # 1. Without key, skipped
        with patch("app.services.tmdb.get_tmdb_api_key", return_value=""):
            res = await enrich_pending_media(self.db)
            self.assertEqual(res["status"], "skipped")

        # 2. Add movie and mock client with key
        movie = Movie(
            source_id=self.source.id,
            raw_name="Avatar",
            display_name="Avatar",
            raw_group="Movies",
            display_group="Movies",
        )
        self.db.add(movie)
        self.db.commit()

        mock_client = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "results": [
                {
                    "id": 19995,
                    "title": "Avatar",
                    "poster_path": "/avatar.jpg",
                    "vote_average": 7.6,
                    "release_date": "2009-12-15",
                    "genre_ids": [28, 12, 878],
                }
            ]
        }
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        with patch("app.services.tmdb.get_tmdb_api_key", return_value="test_key"):
            res = await enrich_pending_media(self.db)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["movies_enriched"], 1)

    @patch("app.services.tmdb.httpx.AsyncClient")
    async def test_04_search_candidates_and_apply_match(self, mock_client_cls):
        """Test searching candidates and manually applying a TMDB ID match."""
        mock_client = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "results": [
                {
                    "id": 19995,
                    "title": "Avatar",
                    "original_title": "Avatar",
                    "release_date": "2009-12-15",
                    "poster_path": "/avatar.jpg",
                    "vote_average": 7.6,
                    "overview": "In the 22nd century...",
                }
            ]
        }
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        # 1. Search candidates
        candidates = await search_tmdb_candidates("Avatar", media_type="movie", api_key="test_key")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], 19995)
        self.assertEqual(candidates[0]["title"], "Avatar")
        self.assertEqual(candidates[0]["year"], "2009")

        # 2. Apply match
        movie = Movie(
            source_id=self.source.id,
            raw_name="Avatar.2009.Extended.Cut.1080p",
            display_name="Avatar.2009.Extended.Cut.1080p",
            raw_group="FR | Movies",
            display_group="FR | Movies",
        )
        self.db.add(movie)
        self.db.commit()

        # Mock detail call
        mock_detail_resp = AsyncMock()
        mock_detail_resp.status_code = 200
        mock_detail_resp.json = lambda: {
            "id": 19995,
            "title": "Avatar",
            "poster_path": "/avatar_official.jpg",
            "overview": "In the 22nd century...",
            "vote_average": 7.6,
            "release_date": "2009-12-15",
            "genres": [{"id": 28, "name": "Action"}, {"id": 12, "name": "Adventure"}],
        }
        mock_client.get.return_value = mock_detail_resp

        with patch("app.services.tmdb.get_tmdb_api_key", return_value="test_key"):
            match_res = await apply_tmdb_id_match("movie", movie.id, 19995, self.db)
            self.assertEqual(match_res["status"], "ok")
            self.assertEqual(match_res["title"], "Avatar")
            self.assertEqual(movie.display_name, "Avatar")
            self.assertEqual(movie.year, 2009)
            self.assertEqual(movie.display_group, "FR | Action")
            self.assertEqual(movie.raw_name, "Avatar.2009.Extended.Cut.1080p")
            self.assertEqual(movie.raw_group, "FR | Movies")

    @patch("app.services.tmdb.search_movie_tmdb")
    @patch("app.services.tmdb.extract_title_with_gemini")
    async def test_05_gemini_fallback_enrichment(self, mock_gemini, mock_search):
        """Test intelligent title extraction with Gemini fallback and staggered calls."""
        movie = Movie(
            source_id=self.source.id,
            raw_name="[NL/FR] De.Schaduw.van.het.Verleden.2023.HDR",
            display_name="[NL/FR] De.Schaduw.van.het.Verleden.2023.HDR",
            raw_group="Foreign VOD",
            display_group="Foreign VOD",
        )
        self.db.add(movie)
        self.db.commit()

        # Step 1: Initial regex search fails
        # Step 2: Gemini returns extracted data
        mock_gemini.return_value = {
            "clean_title": "De Schaduw van het Verleden",
            "year": 2023,
            "language_code": "NL",
            "tmdb_language": "nl-NL",
        }

        # Step 3: Second search with clean title succeeds
        mock_search.side_effect = [
            None,  # First try with regex fails
            {      # Second try with Gemini extracted title succeeds
                "id": 8888,
                "title": "De Schaduw van het Verleden",
                "poster_path": "/shadow.jpg",
                "overview": "Een spannende thriller...",
                "vote_average": 7.9,
                "release_date": "2023-04-10",
                "genre_ids": [53],  # Thriller
            },
        ]

        with patch("app.services.tmdb.get_gemini_api_key", return_value="fake_gemini_key"):
            success = await enrich_movie(movie, api_key="fake_tmdb_key", db=self.db)
            self.assertTrue(success)
            self.assertEqual(movie.tmdb_id, 8888)
            self.assertEqual(movie.display_name, "De Schaduw van het Verleden")
            self.assertEqual(movie.year, 2023)
            # Group updated using Gemini language code NL + TMDB Genre Thriller
            self.assertEqual(movie.display_group, "NL | Thriller")
            mock_gemini.assert_called_once()

    @patch("app.services.tmdb.search_movie_tmdb")
    async def test_06_sync_exact_duplicate_media(self, mock_search):
        """Test that enriching an item propagates TMDB metadata to all exact title+group duplicates."""
        # Source 1 movie
        m1 = Movie(
            source_id=self.source.id,
            raw_name="Top.Gun.Maverick.2022.1080p",
            display_name="Top.Gun.Maverick.2022.1080p",
            raw_group="US | Action",
            display_group="US | Action",
        )
        # Source 2 duplicate movie (exact same raw_name and raw_group)
        m2 = Movie(
            source_id=self.source.id,
            raw_name="Top.Gun.Maverick.2022.1080p",
            display_name="Top.Gun.Maverick.2022.1080p",
            raw_group="US | Action",
            display_group="US | Action",
        )
        self.db.add_all([m1, m2])
        self.db.commit()

        mock_search.return_value = {
            "id": 361743,
            "title": "Top Gun: Maverick",
            "poster_path": "/topgun.jpg",
            "backdrop_path": "/topgun_bg.jpg",
            "overview": "After thirty years, Maverick continues to push the envelope...",
            "vote_average": 8.3,
            "release_date": "2022-05-24",
            "genre_ids": [28],  # Action
        }

        from app.services.tmdb import enrich_single_movie
        res = await enrich_single_movie(m1.id, self.db)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["duplicates_synced"], 1)

        # Refresh m2 from DB
        self.db.refresh(m2)
        self.assertEqual(m2.tmdb_id, 361743)
        self.assertEqual(m2.display_name, "Top Gun: Maverick")
        self.assertEqual(m2.year, 2022)
        self.assertEqual(m2.rating, 8.3)
        self.assertEqual(m2.display_group, "US | Action")
        self.assertIsNotNone(m2.tmdb_fetched_at)

    def test_07_latin_script_and_english_title_priority(self):
        """Test Latin script detection and picking readable English/Latin titles over non-Latin scripts."""
        # 1. Latin script detection
        self.assertTrue(is_latin_script("Chandni"))
        self.assertTrue(is_latin_script("Amélie"))
        self.assertTrue(is_latin_script("El Secreto de sus Ojos"))
        self.assertTrue(is_latin_script("Das Boot"))
        self.assertFalse(is_latin_script("चाँदनी"))          # Devanagari / Hindi
        self.assertFalse(is_latin_script("千と千尋の神隠し"))   # Japanese / Kanji
        self.assertFalse(is_latin_script("Брат"))           # Russian / Cyrillic
        self.assertFalse(is_latin_script("기생충"))          # Korean / Hangul

        # 2. English localization priority for non-Latin titles (e.g. Hindi Chandni)
        title = choose_best_display_title(
            localized_title="Chandni",
            original_title="चाँदनी",
            fallback_title="Chandni 1989",
        )
        self.assertEqual(title, "Chandni")

        # 3. Japanese Spirited Away
        title = choose_best_display_title(
            localized_title="Spirited Away",
            original_title="千と千尋の神隠し",
            fallback_title="Sen to Chihiro",
        )
        self.assertEqual(title, "Spirited Away")

        # 4. French title in Latin script preserved (e.g. Amélie or original French title)
        title = choose_best_display_title(
            localized_title="Amélie",
            original_title="Le Fabuleux Destin d'Amélie Poulain",
            fallback_title="Amelie",
        )
        self.assertEqual(title, "Amélie")

    def test_08_safety_net_prevents_blank_name_or_group(self):
        """Test that enrichment safety nets never leave display_name or display_group blank."""
        # Test direct sanitizer helper
        name, group = sanitize_display_name_and_group(
            candidate_name="   ",
            fallback_name="Inception (2010)",
            candidate_group="",
            fallback_group="Action Movies",
        )
        self.assertEqual(name, "Inception (2010)")
        self.assertEqual(group, "Action Movies")

        # None candidates fallback
        name, group = sanitize_display_name_and_group(
            candidate_name=None,
            fallback_name="Avatar",
            candidate_group=None,
            fallback_group=None,
        )
        self.assertEqual(name, "Avatar")
        self.assertEqual(group, "Uncategorized")


if __name__ == "__main__":
    unittest.main()
