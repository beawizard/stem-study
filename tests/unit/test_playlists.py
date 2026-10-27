"""Reel playlist matching, backfill, and topic fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db, keys
from app.handler import handler
from app.playlists import (
    apply_reel_playlist_backfill,
    normalize_reel_url,
    plan_reel_playlist_backfill,
)
from app.services.subject_service import create_subject, list_subjects
from app.validation import SubjectCreate
from tests.helpers import make_event

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "scripts" / "data" / "facebook_reels_playlists.json"

SPIDERS = "https://www.facebook.com/reel/29256819773901645"


def _catalog_one():
    return [
        {
            "name": "Ecosystems",
            "category": "Science",
            "playlist_id": "1838370447615931",
            "manage_url": "https://www.facebook.com/professional_dashboard/content/playlists/edit/?playlist_id=1838370447615931",
            "reels": [
                {
                    "title": "Why do spiders spin webs?",
                    "url": SPIDERS + "?s=share",
                },
                {
                    "title": "Not on the site yet",
                    "url": "https://www.facebook.com/reel/1111111111",
                },
            ],
        }
    ]


def _put_facebook(subject_id: str, url: str, category: str = "Science") -> None:
    db.update_item(
        keys.subject_pk(subject_id),
        keys.subject_meta_sk(),
        {"facebook_video_url": url, "category": category},
    )


@pytest.mark.unit
def test_normalize_reel_url_ignores_host_slash_and_query():
    bare = "29256819773901645"
    variants = [
        f"https://www.facebook.com/reel/{bare}",
        f"https://www.facebook.com/reel/{bare}/",
        f"https://m.facebook.com/reel/{bare}?s=1&ref=share",
        f"http://facebook.com/reel/{bare}/?foo=1",
        f"facebook.com/reel/{bare}",
    ]
    keys_found = {normalize_reel_url(url) for url in variants}
    assert keys_found == {f"fb:{bare}"}
    assert normalize_reel_url("https://www.facebook.com/watch/?v=123&ref=1") == "fb:123"
    assert normalize_reel_url("https://facebook.com/watch/?v=123") == "fb:123"
    assert normalize_reel_url("") == ""
    assert normalize_reel_url(SPIDERS) != normalize_reel_url(
        "https://www.facebook.com/reel/111"
    )


@pytest.mark.unit
def test_plan_reports_unmatched_and_skips_manage_url():
    subjects = [
        {
            "subject_id": "science-spiders",
            "category": "Science",
            "facebook_video_url": "https://facebook.com/reel/29256819773901645/",
        }
    ]
    plan = plan_reel_playlist_backfill(_catalog_one(), subjects)
    assert len(plan["updates"]) == 1
    row = plan["updates"][0]
    assert row["subject_id"] == "science-spiders"
    assert row["playlist_name"] == "Ecosystems"
    assert row["playlist_id"] == "1838370447615931"
    assert row["playlist_position"] == 1
    assert row["playlist_sort"] == 1
    assert "manage_url" not in row
    assert plan["unmatched"][0]["title"] == "Not on the site yet"
    assert plan["unmatched"][0]["reason"] == "no_topic"
    assert plan["unmatched"][0]["url"].endswith("/1111111111")


@pytest.mark.unit
def test_plan_does_not_assign_across_categories():
    subjects = [
        {
            "subject_id": "technology-spiders",
            "category": "Technology",
            "facebook_video_url": SPIDERS,
        }
    ]
    plan = plan_reel_playlist_backfill(_catalog_one(), subjects)
    assert plan["updates"] == []
    assert plan["unmatched"][0]["reason"] == "category_mismatch"
    assert plan["unmatched"][0]["subject_ids"] == ["technology-spiders"]


@pytest.mark.unit
def test_normalize_and_merge_edge_cases():
    from app.playlists import clean_playlist_id, clean_playlist_name, playlist_update_from_payload

    assert clean_playlist_id(None) is None
    assert clean_playlist_id("  ") == ""
    assert clean_playlist_name(None) is None
    assert clean_playlist_name("  Plants  ") == "Plants"
    port = normalize_reel_url("https://facebook.com:443/reel/29256819773901645")
    userinfo = normalize_reel_url("https://user:pass@www.facebook.com/reel/29256819773901645")
    assert port == userinfo == "fb:29256819773901645"
    short = normalize_reel_url("https://fb.watch/abcDEF")
    assert short == "url:fb.watch/abcdef"
    assert playlist_update_from_payload({"playlist_name": "Keep"}, {}) is None
    created = playlist_update_from_payload(None, {"playlist_name": "Plants"})
    assert created["playlist_id"] == ""
    assert created["playlist_position"] == 0
    merged = playlist_update_from_payload(
        {"playlist_id": "1145586957826409", "playlist_name": "Old", "playlist_position": 4, "playlist_sort": 3},
        {"playlist_name": "Plants"},
    )
    assert merged["playlist_id"] == "1145586957826409"
    assert merged["playlist_name"] == "Plants"
    assert merged["playlist_position"] == 4
    assert merged["playlist_sort"] == 3


@pytest.mark.unit
def test_plan_skips_deleted_and_blank_urls_and_reassigns_same_topic():
    subjects = [
        {"subject_id": "gone", "deleted_at": "2020-01-01T00:00:00Z", "category": "Science", "facebook_video_url": SPIDERS},
        {"subject_id": "blank", "category": "Science", "facebook_video_url": "  "},
        {
            "subject_id": "science-spiders",
            "category": "Science",
            "facebook_video_url": SPIDERS,
            "playlist_id": "1838370447615931",
            "playlist_name": "Ecosystems",
            "playlist_position": 1,
            "playlist_sort": 1,
        },
    ]
    catalog = [
        {
            "name": "Ecosystems",
            "category": "Science",
            "playlist_id": "1838370447615931",
            "reels": [
                {"title": "First", "url": SPIDERS},
                {"title": "Same reel again", "url": SPIDERS},
                {"title": "Empty", "url": ""},
            ],
        }
    ]
    plan = plan_reel_playlist_backfill(catalog, subjects)
    assert [row["subject_id"] for row in plan["updates"]] == ["science-spiders"]
    assert plan["updates"][0]["playlist_position"] == 2
    assert plan["unchanged"] == []
    assert any(row["title"] == "Empty" and row["reason"] == "no_topic" for row in plan["unmatched"])


@pytest.mark.unit
def test_plan_is_idempotent_when_fields_already_match():
    subjects = [
        {
            "subject_id": "science-spiders",
            "category": "Science",
            "facebook_video_url": SPIDERS,
            "playlist_id": "1838370447615931",
            "playlist_name": "Ecosystems",
            "playlist_position": 1,
            "playlist_sort": 1,
        }
    ]
    plan = plan_reel_playlist_backfill(_catalog_one(), subjects)
    assert plan["updates"] == []
    assert plan["unchanged"] == ["science-spiders"]


@pytest.mark.unit
def test_shipped_playlist_catalog_shape():
    raw = CATALOG_PATH.read_text(encoding="utf-8")
    assert "manage_url" not in raw
    catalog = json.loads(raw)
    assert [item["name"] for item in catalog] == [
        "Technology",
        "Engineering",
        "Ecosystems",
        "Everyday Science",
        "Plants",
        "Earth & Space",
        "Electricity & Magnetism",
        "Forces & Motion",
    ]
    assert [item["category"] for item in catalog] == [
        "Technology",
        "Engineering",
        "Science",
        "Science",
        "Science",
        "Science",
        "Science",
        "Science",
    ]
    assert sum(len(item["reels"]) for item in catalog) == 70
    plan = plan_reel_playlist_backfill(catalog, [])
    assert len(plan["unmatched"]) == 70
    assert {row["reason"] for row in plan["unmatched"]} == {"no_topic"}


@pytest.mark.unit
def test_backfill_apply_is_idempotent(dynamodb_table):
    create_subject(
        SubjectCreate(
            category="Science",
            topic="Spiders",
            subject_id="science-spiders",
        )
    )
    create_subject(
        SubjectCreate(
            category="Science",
            topic="Also spiders",
            subject_id="science-also-spiders",
        )
    )
    _put_facebook("science-spiders", "https://m.facebook.com/reel/29256819773901645/")
    _put_facebook("science-also-spiders", SPIDERS + "/")

    first = apply_reel_playlist_backfill(_catalog_one(), dry_run=True)
    assert first["dry_run"] is True
    assert first["updated"] == 0
    assert len(first["updates"]) == 2
    stored = {
        row["subject_id"]: row
        for row in list_subjects()
        if row["subject_id"].startswith("science-")
    }
    assert stored["science-spiders"]["playlist_name"] == ""

    written = apply_reel_playlist_backfill(_catalog_one())
    assert written["updated"] == 2
    assert written["duplicates"]
    again = apply_reel_playlist_backfill(_catalog_one())
    assert again["updated"] == 0
    assert again["updates"] == []
    assert sorted(again["unchanged"]) == [
        "science-also-spiders",
        "science-spiders",
    ]
    listed = {
        row["subject_id"]: row
        for row in list_subjects()
    }
    spiders = listed["science-spiders"]
    assert spiders["playlist_name"] == "Ecosystems"
    assert spiders["playlist_position"] == 1
    assert spiders["playlist_id"] == "1838370447615931"
    assert "manage_url" not in spiders


@pytest.mark.unit
def test_subject_api_sets_playlist(dynamodb_table, admin_headers, user_headers):
    created = handler(
        make_event(
            "POST",
            "/subjects",
            headers=admin_headers,
            body=json.dumps(
                {
                    "category": "Engineering",
                    "topic": "Bridges",
                    "playlist_name": "Engineering",
                    "playlist_id": "1821061039022813",
                    "playlist_position": 2,
                    "playlist_sort": 1,
                }
            ),
        )
    )
    assert created["statusCode"] == 201, created["body"]
    body = json.loads(created["body"])
    assert body["playlist_name"] == "Engineering"
    assert body["playlist_position"] == 2

    updated = handler(
        make_event(
            "PUT",
            f"/subjects/{body['subject_id']}",
            headers=admin_headers,
            body=json.dumps({"playlist_name": "", "playlist_id": ""}),
        )
    )
    assert updated["statusCode"] == 200, updated["body"]
    cleared = json.loads(updated["body"])
    assert cleared["playlist_name"] == ""
    assert cleared["playlist_id"] == ""
    assert cleared["playlist_position"] == 0

    listed = handler(make_event("GET", "/subjects", headers=user_headers))
    rows = json.loads(listed["body"])["subjects"]
    match = next(row for row in rows if row["subject_id"] == body["subject_id"])
    assert match["playlist_sort"] == 0

    invalid = handler(
        make_event(
            "PUT",
            f"/subjects/{body['subject_id']}",
            headers=admin_headers,
            body=json.dumps({"playlist_id": "not-a-playlist"}),
        )
    )
    assert invalid["statusCode"] == 422
