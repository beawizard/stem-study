"""Home recommended-lesson carousel."""

from __future__ import annotations

import json
import random

import pytest

from app import db, keys
from app.handler import handler
from app.services import access_service, recommendation_service, subject_service, user_service
from app.validation import SubjectCreate
from tests.helpers import make_event


def _topic(subject_id: str, category: str, topic: str, **extra):
    subject_service.create_subject(
        SubjectCreate(
            subject_id=subject_id,
            category=category,
            topic=topic,
            grade_level="Grade 3",
        )
    )
    if extra:
        db.update_item(
            keys.subject_pk(subject_id),
            keys.subject_meta_sk(),
            extra,
        )


def _ids_for(body: dict, category: str) -> list[str]:
    return [
        item["subject_id"]
        for item in body["items"]
        if item["category"] == category
    ]


@pytest.mark.unit
def test_auto_picks_two_per_stem_category_at_random_without_visits(dynamodb_table):
    for category, prefix in (
        ("Science", "science"),
        ("Technology", "technology"),
        ("Engineering", "engineering"),
        ("Mathematics", "mathematics"),
    ):
        ready = {}
        if category == "Technology":
            ready = {"presentation_ready": True, "content_kind": "presentation"}
        elif category != "Mathematics":
            ready = {"science_ready": True, "content_kind": "lesson"}
        _topic(f"{prefix}-a", category, f"{category} A", **ready)
        _topic(f"{prefix}-b", category, f"{category} B", **ready)
        _topic(f"{prefix}-c", category, f"{category} C", **ready)
    _topic("science-draft", "Science", "Draft", content_kind="lesson")

    body = recommendation_service.list_home_recommendations(rng=random.Random(1))
    assert body["source"] == "random"
    assert len(body["items"]) == 8
    assert "science-draft" not in {item["subject_id"] for item in body["items"]}
    for category in ("Science", "Technology", "Engineering", "Mathematics"):
        picked = _ids_for(body, category)
        assert len(picked) == 2
        assert len(set(picked)) == 2


@pytest.mark.unit
def test_auto_uses_top_two_visited_when_counts_exist(dynamodb_table):
    _topic("science-low", "Science", "Low", science_ready=True, content_kind="lesson", visit_count=1)
    _topic("science-high", "Science", "High", science_ready=True, content_kind="lesson", visit_count=5)
    _topic("science-mid", "Science", "Mid", science_ready=True, content_kind="lesson", visit_count=3)
    _topic(
        "technology-show",
        "Technology",
        "Show",
        presentation_ready=True,
        content_kind="presentation",
        visit_count=2,
        pages=[{"index": 1, "image_url": "https://cdn.example/later.jpg"}, {"index": 0, "image_url": "https://cdn.example/first.jpg"}],
    )
    _topic(
        "technology-other",
        "Technology",
        "Other",
        presentation_ready=True,
        content_kind="presentation",
        visit_count=9,
        image_url="https://cdn.example/cover.jpg",
    )
    _topic("engineering-only", "Engineering", "Bridge", science_ready=True, content_kind="lesson", visit_count=4, image_url="https://cdn.example/bridge.jpg")
    _topic("mathematics-add", "Mathematics", "Addition", visit_count=2)

    body = recommendation_service.list_home_recommendations(rng=random.Random(0))
    assert body["source"] == "visited"
    assert _ids_for(body, "Science") == ["science-high", "science-mid"]
    assert _ids_for(body, "Technology") == ["technology-other", "technology-show"]
    assert _ids_for(body, "Engineering") == ["engineering-only"]
    assert _ids_for(body, "Mathematics") == ["mathematics-add"]
    tech = next(item for item in body["items"] if item["subject_id"] == "technology-show")
    assert tech["image_url"] == "https://cdn.example/first.jpg"
    assert tech["interactive_html_url"] == ""
    cover = next(item for item in body["items"] if item["subject_id"] == "technology-other")
    assert cover["image_url"] == "https://cdn.example/cover.jpg"

@pytest.mark.unit
def test_card_includes_interactive_html_when_there_is_no_cover(dynamodb_table, monkeypatch):
    monkeypatch.setenv("FRONTEND_PUBLIC_BASE", "https://cdn.example")
    _topic(
        "science-quakes",
        "Science",
        "Earthquakes",
        science_ready=True,
        content_kind="lesson",
        html_url="https://cdn.example/science/quakes/interactive.html",
    )
    _topic(
        "science-key",
        "Science",
        "Keyed",
        science_ready=True,
        content_kind="lesson",
        html_key="science/science-key/interactive.html",
        visit_count=2,
    )
    body = recommendation_service.list_home_recommendations(rng=random.Random(0))
    quakes = next(item for item in body["items"] if item["subject_id"] == "science-quakes")
    keyed = next(item for item in body["items"] if item["subject_id"] == "science-key")
    assert quakes["image_url"] == ""
    assert quakes["interactive_html_url"] == "https://cdn.example/science/quakes/interactive.html"
    assert keyed["interactive_html_url"] == "https://cdn.example/science/science-key/interactive.html"


@pytest.mark.unit
def test_curated_list_overrides_visits_and_can_be_cleared(dynamodb_table, admin_headers, user_headers):
    _topic("science-a", "Science", "Plants", science_ready=True, content_kind="lesson", visit_count=9, image_url="https://cdn.example/plants.jpg")
    _topic("science-b", "Science", "Rocks", science_ready=True, content_kind="lesson", visit_count=1)
    _topic("mathematics-add", "Mathematics", "Addition", visit_count=3)

    denied = handler(
        make_event(
            "PUT",
            "/recommendations",
            headers=user_headers,
            body=json.dumps({"subject_ids": ["science-b"]}),
        )
    )
    assert denied["statusCode"] == 403

    saved = handler(
        make_event(
            "PUT",
            "/recommendations",
            headers=admin_headers,
            body=json.dumps({"subject_ids": ["science-b", "mathematics-add", "missing-topic"]}),
        )
    )
    assert saved["statusCode"] == 400

    saved = handler(
        make_event(
            "PUT",
            "/recommendations",
            headers=admin_headers,
            body=json.dumps({"subject_ids": ["science-b", "mathematics-add", "science-b"]}),
        )
    )
    assert saved["statusCode"] == 200, saved["body"]
    body = json.loads(saved["body"])
    assert body["source"] == "curated"
    assert [item["subject_id"] for item in body["items"]] == ["science-b", "mathematics-add"]
    assert body["items"][0]["image_url"] == ""
    assert body["items"][0]["topic"] == "Rocks"

    listed = handler(make_event("GET", "/recommendations", headers=user_headers))
    assert listed["statusCode"] == 200
    assert json.loads(listed["body"])["source"] == "curated"

    cleared = handler(
        make_event(
            "PUT",
            "/recommendations",
            headers=admin_headers,
            body=json.dumps({"subject_ids": []}),
        )
    )
    assert cleared["statusCode"] == 200
    again = json.loads(cleared["body"])
    assert again["source"] == "visited"
    assert _ids_for(again, "Science") == ["science-a", "science-b"]


@pytest.mark.unit
def test_first_access_increments_visit_count_once(dynamodb_table):
    _topic("science-plants", "Science", "Plants", science_ready=True, content_kind="lesson")
    user_service.ensure_user_profile("u-ada", email="ada@example.com", nickname="Ada")
    access_service.record_topic_access("u-ada", "science-plants", elapsed_ms=0)
    access_service.record_topic_access("u-ada", "science-plants", elapsed_ms=50_000)
    subject = subject_service.get_subject("science-plants")
    assert int(subject.get("visit_count") or 0) == 1

    user_service.ensure_user_profile("u-bea", email="bea@example.com", nickname="Bea")
    access_service.record_topic_access("u-bea", "science-plants", elapsed_ms=0)
    subject = subject_service.get_subject("science-plants")
    assert int(subject.get("visit_count") or 0) == 2
