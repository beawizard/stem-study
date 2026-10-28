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
    short_slash = normalize_reel_url("https://fb.watch/abcDEF/")
    share = normalize_reel_url("https://www.facebook.com/share/r/AbC123/")
    # Short links are not Reel ids. Matching them to a /reel/<id> URL needs
    # Facebook's redirect, which this matcher does not follow.
    assert short == short_slash == "url:fb.watch/abcdef"
    assert share == "url:facebook.com/share/r/abc123"
    assert not short.startswith("fb:")
    assert not share.startswith("fb:")
    with pytest.raises(ValueError, match="control"):
        clean_playlist_name("Plan\nts")
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
def test_plan_skips_deleted_and_blank_urls_and_keeps_first_listing():
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
    assert plan["updates"] == []
    assert plan["unchanged"] == ["science-spiders"]
    conflicts = [row for row in plan["duplicates"] if row["reason"] == "playlist_conflict"]
    assert conflicts
    assert conflicts[0]["kept_playlist"] == "Ecosystems"
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

    long_name = handler(
        make_event(
            "PUT",
            f"/subjects/{body['subject_id']}",
            headers=admin_headers,
            body=json.dumps({"playlist_name": "p" * 81}),
        )
    )
    assert long_name["statusCode"] == 422

    newline = handler(
        make_event(
            "PUT",
            f"/subjects/{body['subject_id']}",
            headers=admin_headers,
            body=json.dumps({"playlist_name": "Eco\nsystems"}),
        )
    )
    assert newline["statusCode"] == 422


@pytest.mark.unit
def test_put_without_playlist_keys_and_null_keep_membership(dynamodb_table, admin_headers):
    created = handler(
        make_event(
            "POST",
            "/subjects",
            headers=admin_headers,
            body=json.dumps(
                {
                    "category": "Science",
                    "topic": "Spiders",
                    "subject_id": "science-spiders",
                    "playlist_name": "Ecosystems",
                    "playlist_id": "1838370447615931",
                    "playlist_position": 3,
                    "playlist_sort": 1,
                }
            ),
        )
    )
    assert created["statusCode"] == 201, created["body"]
    subject_id = json.loads(created["body"])["subject_id"]

    renamed = handler(
        make_event(
            "PUT",
            f"/subjects/{subject_id}",
            headers=admin_headers,
            body=json.dumps({"topic": "Orb weavers"}),
        )
    )
    assert renamed["statusCode"] == 200, renamed["body"]
    kept = json.loads(renamed["body"])
    assert kept["topic"] == "Orb weavers"
    assert kept["playlist_name"] == "Ecosystems"
    assert kept["playlist_id"] == "1838370447615931"
    assert kept["playlist_position"] == 3
    assert kept["playlist_sort"] == 1

    nulled = handler(
        make_event(
            "PUT",
            f"/subjects/{subject_id}",
            headers=admin_headers,
            body=json.dumps(
                {
                    "playlist_name": None,
                    "playlist_id": None,
                    "playlist_position": None,
                    "playlist_sort": None,
                }
            ),
        )
    )
    assert nulled["statusCode"] == 200, nulled["body"]
    still = json.loads(nulled["body"])
    assert still["playlist_name"] == "Ecosystems"
    assert still["playlist_id"] == "1838370447615931"
    assert still["playlist_position"] == 3
    assert still["playlist_sort"] == 1


@pytest.mark.unit
def test_non_admin_cannot_write_subjects_or_science_topics(
    dynamodb_table, admin_headers, user_headers
):
    created = handler(
        make_event(
            "POST",
            "/subjects",
            headers=admin_headers,
            body=json.dumps({"category": "Science", "topic": "Rocks"}),
        )
    )
    assert created["statusCode"] == 201, created["body"]
    subject_id = json.loads(created["body"])["subject_id"]

    denied_put = handler(
        make_event(
            "PUT",
            f"/subjects/{subject_id}",
            headers=user_headers,
            body=json.dumps({"topic": "Nope"}),
        )
    )
    assert denied_put["statusCode"] == 403

    denied_subject = handler(
        make_event(
            "POST",
            "/subjects",
            headers=user_headers,
            body=json.dumps({"category": "Science", "topic": "Nope"}),
        )
    )
    assert denied_subject["statusCode"] == 403

    denied_science = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=user_headers,
            body=json.dumps({"category": "Science", "topic": "Nope"}),
        )
    )
    assert denied_science["statusCode"] == 403


@pytest.mark.unit
def test_duplicate_reel_stays_on_the_first_playlist(dynamodb_table):
    create_subject(
        SubjectCreate(
            category="Science",
            topic="Spiders",
            subject_id="science-spiders",
        )
    )
    _put_facebook("science-spiders", SPIDERS)
    catalog = [
        {
            "name": "Ecosystems",
            "category": "Science",
            "playlist_id": "1838370447615931",
            "reels": [{"title": "Spiders", "url": SPIDERS}],
        },
        {
            "name": "Everyday Science",
            "category": "Science",
            "playlist_id": "935983412709483",
            "reels": [{"title": "Spiders again", "url": SPIDERS}],
        },
    ]
    first = apply_reel_playlist_backfill(catalog)
    assert first["updated"] == 1
    assert first["updates"][0]["playlist_name"] == "Ecosystems"
    assert first["updates"][0]["before"]["playlist_name"] == ""
    assert first["updates"][0]["after"]["playlist_name"] == "Ecosystems"
    assert any(row["reason"] == "playlist_conflict" for row in first["duplicates"])

    second = apply_reel_playlist_backfill(catalog)
    assert second["updated"] == 0
    assert second["updates"] == []
    assert second["unchanged"] == ["science-spiders"]
    stored = db.get_item(keys.subject_pk("science-spiders"), keys.subject_meta_sk())
    assert stored["playlist_name"] == "Ecosystems"
    assert stored["playlist_id"] == "1838370447615931"


@pytest.mark.unit
def test_backfill_keeps_manual_playlist_unless_forced(dynamodb_table):
    create_subject(
        SubjectCreate(
            category="Science",
            topic="Spiders",
            subject_id="science-spiders",
        )
    )
    _put_facebook("science-spiders", SPIDERS)
    db.update_item(
        keys.subject_pk("science-spiders"),
        keys.subject_meta_sk(),
        {
            "playlist_id": "99999",
            "playlist_name": "Class notes",
            "playlist_position": 4,
            "playlist_sort": 2,
        },
    )
    kept = apply_reel_playlist_backfill(_catalog_one())
    assert kept["updated"] == 0
    assert kept["updates"] == []
    assert kept["skipped"][0]["subject_id"] == "science-spiders"
    assert kept["skipped"][0]["before"]["playlist_name"] == "Class notes"
    assert kept["skipped"][0]["after"]["playlist_name"] == "Ecosystems"
    stored = db.get_item(keys.subject_pk("science-spiders"), keys.subject_meta_sk())
    assert stored["playlist_name"] == "Class notes"

    forced = apply_reel_playlist_backfill(_catalog_one(), force=True)
    assert forced["updated"] == 1
    assert forced["updates"][0]["before"]["playlist_name"] == "Class notes"
    assert forced["updates"][0]["after"]["playlist_name"] == "Ecosystems"
    stored = db.get_item(keys.subject_pk("science-spiders"), keys.subject_meta_sk())
    assert stored["playlist_name"] == "Ecosystems"
    assert stored["playlist_position"] == 1


@pytest.mark.unit
def test_apply_does_not_recreate_a_deleted_topic(dynamodb_table, monkeypatch):
    monkeypatch.setattr(
        "app.services.subject_service.list_subjects",
        lambda: [
            {
                "subject_id": "ghost-topic",
                "category": "Science",
                "facebook_video_url": SPIDERS,
            }
        ],
    )
    result = apply_reel_playlist_backfill(_catalog_one())
    assert result["updated"] == 0
    assert result["failures"]
    assert result["failures"][0]["subject_id"] == "ghost-topic"
    assert db.get_item(keys.subject_pk("ghost-topic"), keys.subject_meta_sk()) is None


@pytest.mark.unit
def test_mathematics_ignores_playlists(dynamodb_table, admin_headers, user_headers):
    rejected = handler(
        make_event(
            "POST",
            "/subjects",
            headers=admin_headers,
            body=json.dumps(
                {
                    "category": "Mathematics",
                    "topic": "Addition",
                    "playlist_name": "Ecosystems",
                    "playlist_id": "1838370447615931",
                }
            ),
        )
    )
    assert rejected["statusCode"] == 422

    created = handler(
        make_event(
            "POST",
            "/subjects",
            headers=admin_headers,
            body=json.dumps(
                {
                    "category": "Mathematics",
                    "topic": "Addition",
                    "subject_id": "math-addition",
                }
            ),
        )
    )
    assert created["statusCode"] == 201, created["body"]
    _put_facebook("math-addition", SPIDERS, category="Mathematics")

    edited = handler(
        make_event(
            "PUT",
            "/subjects/math-addition",
            headers=admin_headers,
            body=json.dumps({"description": "Sums"}),
        )
    )
    assert edited["statusCode"] == 200, edited["body"]
    body = json.loads(edited["body"])
    assert body["description"] == "Sums"
    assert body["playlist_name"] == ""
    assert body["playlist_id"] == ""

    playlist_put = handler(
        make_event(
            "PUT",
            "/subjects/math-addition",
            headers=admin_headers,
            body=json.dumps(
                {
                    "playlist_name": "Ecosystems",
                    "playlist_id": "1838370447615931",
                    "playlist_position": 1,
                    "playlist_sort": 1,
                }
            ),
        )
    )
    assert playlist_put["statusCode"] in (400, 422)

    science_math = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=admin_headers,
            body=json.dumps(
                {
                    "category": "Mathematics",
                    "topic": "Fractions",
                    "playlist_name": "Ecosystems",
                }
            ),
        )
    )
    assert science_math["statusCode"] == 422

    result = apply_reel_playlist_backfill(_catalog_one())
    assert result["updates"] == []
    stored = db.get_item(keys.subject_pk("math-addition"), keys.subject_meta_sk())
    assert stored["playlist_name"] == ""
    assert stored["playlist_id"] == ""
    listed = handler(make_event("GET", "/subjects", headers=user_headers))
    rows = json.loads(listed["body"])["subjects"]
    match = next(row for row in rows if row["subject_id"] == "math-addition")
    assert match["playlist_name"] == ""


@pytest.mark.unit
def test_moving_to_mathematics_clears_playlist(dynamodb_table, admin_headers):
    created = handler(
        make_event(
            "POST",
            "/subjects",
            headers=admin_headers,
            body=json.dumps(
                {
                    "category": "Science",
                    "topic": "Spiders",
                    "subject_id": "science-spiders",
                    "playlist_name": "Ecosystems",
                    "playlist_id": "1838370447615931",
                    "playlist_position": 2,
                    "playlist_sort": 1,
                }
            ),
        )
    )
    assert created["statusCode"] == 201, created["body"]

    moved = handler(
        make_event(
            "PUT",
            "/subjects/science-spiders",
            headers=admin_headers,
            body=json.dumps({"category": "Mathematics"}),
        )
    )
    assert moved["statusCode"] == 200, moved["body"]
    as_math = json.loads(moved["body"])
    assert as_math["category"] == "Mathematics"
    assert as_math["playlist_name"] == ""
    assert as_math["playlist_id"] == ""
    assert as_math["playlist_position"] == 0
    assert as_math["playlist_sort"] == 0

    restored = handler(
        make_event(
            "PUT",
            "/subjects/science-spiders",
            headers=admin_headers,
            body=json.dumps({"category": "Science"}),
        )
    )
    assert restored["statusCode"] == 200, restored["body"]
    as_science = json.loads(restored["body"])
    assert as_science["category"] == "Science"
    assert as_science["playlist_name"] == ""
    assert as_science["playlist_id"] == ""
    assert as_science["playlist_position"] == 0
    assert as_science["playlist_sort"] == 0


def _backfill_script():
    import importlib.util

    path = ROOT / "scripts" / "backfill_reel_playlists.py"
    spec = importlib.util.spec_from_file_location("backfill_reel_playlists", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary_counts(capsys, script, result) -> dict[str, str]:
    script.print_report(result)
    line = next(
        row for row in capsys.readouterr().out.splitlines() if row.startswith("dry_run=")
    )
    return dict(part.split("=", 1) for part in line.split())


@pytest.mark.unit
def test_report_pending_is_still_unwritten(dynamodb_table, capsys, monkeypatch):
    create_subject(
        SubjectCreate(
            category="Science",
            topic="Spiders",
            subject_id="science-spiders",
        )
    )
    _put_facebook("science-spiders", SPIDERS)
    script = _backfill_script()
    rocks = "https://www.facebook.com/reel/2222222222"
    catalog = [
        {
            "name": "Ecosystems",
            "category": "Science",
            "playlist_id": "1838370447615931",
            "reels": [
                {"title": "Spiders", "url": SPIDERS},
                {"title": "Missing topic", "url": rocks},
            ],
        }
    ]

    dry = apply_reel_playlist_backfill(catalog, dry_run=True)
    dry_counts = _summary_counts(capsys, script, dry)
    assert dry_counts["dry_run"] == "True"
    assert dry_counts["updated"] == "0"
    assert dry_counts["pending"] == "1"
    assert script.pending_count(dry) == 1

    from app.services.subject_service import list_subjects as real_list_subjects

    def with_ghost():
        rows = real_list_subjects()
        rows.append(
            {
                "subject_id": "ghost-topic",
                "category": "Science",
                "facebook_video_url": rocks,
            }
        )
        return rows

    monkeypatch.setattr("app.services.subject_service.list_subjects", with_ghost)
    failed = apply_reel_playlist_backfill(catalog)
    failed_counts = _summary_counts(capsys, script, failed)
    assert failed["updated"] == 1
    assert len(failed["failures"]) == 1
    assert failed_counts["dry_run"] == "False"
    assert failed_counts["updated"] == "1"
    assert failed_counts["pending"] == "1"
    assert failed_counts["failures"] == "1"
    stored = db.get_item(keys.subject_pk("science-spiders"), keys.subject_meta_sk())
    assert stored["playlist_name"] == "Ecosystems"
    assert db.get_item(keys.subject_pk("ghost-topic"), keys.subject_meta_sk()) is None

    monkeypatch.undo()
    db.update_item(
        keys.subject_pk("science-spiders"),
        keys.subject_meta_sk(),
        {
            "playlist_id": "",
            "playlist_name": "",
            "playlist_position": 0,
            "playlist_sort": 0,
        },
    )
    written = apply_reel_playlist_backfill(catalog)
    written_counts = _summary_counts(capsys, script, written)
    assert written["failures"] == []
    assert written_counts["dry_run"] == "False"
    assert written_counts["updated"] == "1"
    assert written_counts["pending"] == "0"
