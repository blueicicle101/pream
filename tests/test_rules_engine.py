"""
Tests for Rules Engine and Rules API endpoints.
Verifies transformation rules, hide rules, set_group, content-type scoping,
ordering, and the core invariant that raw_name and raw_group are NEVER mutated.
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
from app.models.rule import Rule
from app.models.source import Source
from app.services.rules_engine import (
    apply_all_rules_to_library,
    test_rule_preview,
)

test_engine = create_engine("sqlite:///./data/test_rules.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class RulesEngineTestCase(unittest.TestCase):
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
        if os.path.exists("./data/test_rules.db"):
            try:
                os.remove("./data/test_rules.db")
            except Exception:
                pass

    def setUp(self):
        self.db = TestingSessionLocal()
        # Clean tables
        self.db.query(Channel).delete()
        self.db.query(Movie).delete()
        self.db.query(Rule).delete()
        self.db.query(Source).delete()
        self.db.commit()

        # Insert a sample source
        self.source = Source(
            name="MainSource",
            type="xtream",
            url="http://mock:8000",
            content_types='["live","vod"]',
            priority=1,
            enabled=True,
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.source)

    def tearDown(self):
        self.db.close()

    def test_01_rename_channel_and_group_rule(self):
        """Test regex renaming of channel display_name and display_group while raw fields stay pristine."""
        ch = Channel(
            source_id=self.source.id,
            raw_name="[HD] BBC One HD",
            raw_group="UK | Entertainment & Movies",
            display_name="[HD] BBC One HD",
            display_group="UK | Entertainment & Movies",
            is_hidden=False,
        )
        self.db.add(ch)

        # Rule 1: Remove [HD] prefix from name
        r1 = Rule(
            order=0,
            rule_type="rename_channel",
            content_type="live",
            match_field="name",
            match_pattern=r"^\[HD\]\s*",
            replacement="",
            enabled=True,
        )
        # Rule 2: Simplify group
        r2 = Rule(
            order=1,
            rule_type="rename_group",
            content_type="live",
            match_field="group",
            match_pattern=r"^UK\s*\|\s*",
            replacement="UK - ",
            enabled=True,
        )
        self.db.add_all([r1, r2])
        self.db.commit()

        # Apply rules
        stats = apply_all_rules_to_library(self.db)
        self.assertEqual(stats["channels_processed"], 1)

        self.db.refresh(ch)
        # Display fields are transformed
        self.assertEqual(ch.display_name, "BBC One HD")
        self.assertEqual(ch.display_group, "UK - Entertainment & Movies")
        # Raw fields remain 100% untouched
        self.assertEqual(ch.raw_name, "[HD] BBC One HD")
        self.assertEqual(ch.raw_group, "UK | Entertainment & Movies")

    def test_02_hide_group_rule(self):
        """Test hide_group rule sets is_hidden=True on matching items."""
        ch1 = Channel(
            source_id=self.source.id,
            raw_name="BBC News",
            raw_group="UK News",
            display_name="BBC News",
            display_group="UK News",
            is_hidden=False,
        )
        ch2 = Channel(
            source_id=self.source.id,
            raw_name="RTL 4",
            raw_group="NL Algemeen",
            display_name="RTL 4",
            display_group="NL Algemeen",
            is_hidden=False,
        )
        self.db.add_all([ch1, ch2])

        # Rule: Hide any group starting with NL
        r = Rule(
            order=0,
            rule_type="hide_group",
            content_type="all",
            match_field="group",
            match_pattern=r"^NL\b",
            enabled=True,
        )
        self.db.add(r)
        self.db.commit()

        apply_all_rules_to_library(self.db)

        self.db.refresh(ch1)
        self.db.refresh(ch2)
        self.assertFalse(ch1.is_hidden)
        self.assertTrue(ch2.is_hidden)

    def test_03_set_group_rule(self):
        """Test set_group assigns new_group when pattern matches."""
        ch = Channel(
            source_id=self.source.id,
            raw_name="Premier League TV HD",
            raw_group="Sports Misc",
            display_name="Premier League TV HD",
            display_group="Sports Misc",
            is_hidden=False,
        )
        self.db.add(ch)

        r = Rule(
            order=0,
            rule_type="set_group",
            content_type="live",
            match_field="name",
            match_pattern=r"Premier League",
            new_group="UK | Sports | Football",
            enabled=True,
        )
        self.db.add(r)
        self.db.commit()

        apply_all_rules_to_library(self.db)
        self.db.refresh(ch)
        self.assertEqual(ch.display_group, "UK | Sports | Football")
        self.assertEqual(ch.raw_group, "Sports Misc")

    def test_04_rules_api(self):
        """Test Rules CRUD, test preview, reordering, and apply endpoints."""
        # Create rule
        payload = {
            "order": 1,
            "rule_type": "rename_channel",
            "content_type": "live",
            "match_field": "name",
            "match_pattern": r"\s+FHD$",
            "replacement": "",
            "enabled": True,
            "description": "Strip FHD suffix",
        }
        res = self.client.post("/api/rules", json=payload)
        self.assertEqual(res.status_code, 201)
        rule_data = res.json()
        rule_id = rule_data["id"]

        # Test preview endpoint
        test_payload = {
            "rule_type": "rename_channel",
            "content_type": "live",
            "match_field": "name",
            "match_pattern": r"\s+FHD$",
            "replacement": "",
            "sample_name": "Sky Sports Main Event FHD",
            "sample_group": "Sports",
        }
        test_res = self.client.post("/api/rules/test", json=test_payload)
        self.assertEqual(test_res.status_code, 200)
        self.assertTrue(test_res.json()["matched"])
        self.assertEqual(test_res.json()["output_name"], "Sky Sports Main Event")

        # Apply endpoint
        apply_res = self.client.post("/api/rules/apply")
        self.assertEqual(apply_res.status_code, 200)
        self.assertEqual(apply_res.json()["status"], "ok")

    def test_05_case_transformations(self):
        """Test regex-matched case transformations (uppercase, lowercase, capitalize, titlecase)."""
        src = Source(name="CaseTestSrc", type="m3u", url="http://example.com/c.m3u", enabled=True)
        self.db.add(src)
        self.db.commit()

        ch1 = Channel(
            source_id=src.id,
            source_stream_id="c1",
            raw_name="sky cinema action",
            raw_group="uk movies and drama",
            display_name="sky cinema action",
            display_group="uk movies and drama",
            stream_url="http://stream/1",
        )
        self.db.add(ch1)
        self.db.commit()

        # 1. Uppercase matched channel name
        rule_up = Rule(
            order=1,
            rule_type="uppercase_channel",
            content_type="live",
            match_field="name",
            match_pattern=r"^sky\s+cinema",
            enabled=True,
        )
        # 2. Titlecase group
        rule_title = Rule(
            order=2,
            rule_type="titlecase_group",
            content_type="live",
            match_field="group",
            match_pattern=r".*",
            enabled=True,
        )
        self.db.add_all([rule_up, rule_title])
        self.db.commit()

        apply_all_rules_to_library(self.db)
        self.db.refresh(ch1)

        self.assertEqual(ch1.display_name, "SKY CINEMA action")
        self.assertEqual(ch1.display_group, "Uk Movies And Drama")
        # Invariant check: raw fields untouched
        self.assertEqual(ch1.raw_name, "sky cinema action")
        self.assertEqual(ch1.raw_group, "uk movies and drama")

        # Test preview with lowercase
        preview = test_rule_preview(
            rule_type="lowercase_channel",
            content_type="live",
            match_field="name",
            match_pattern=r".*",
            replacement=None,
            new_group=None,
            sample_name="BBC ONE HD",
            sample_group="General",
        )
        self.assertTrue(preview["matched"])
        self.assertEqual(preview["output_name"], "bbc one hd")

        # Test preview with capitalize
        preview_cap = test_rule_preview(
            rule_type="capitalize_group",
            content_type="live",
            match_field="group",
            match_pattern=r".*",
            replacement=None,
            new_group=None,
            sample_name="Test",
            sample_group="DOCUMENTARY",
        )
        self.assertTrue(preview_cap["matched"])
        self.assertEqual(preview_cap["output_group"], "Documentary")


if __name__ == "__main__":
    unittest.main()
