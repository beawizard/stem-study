"""Long topic ids, and replace uploads that must not clobber saved fields."""

from __future__ import annotations

import json

import boto3
import pytest

from app.handler import handler
from app.services.subject_service import slugify_subject_id
from app.validation import MAX_SUBJECT_ID_LEN, SUBJECT_ID_PATTERN
from tests.helpers import make_event

REGION = "ap-southeast-1"
BUCKET = "stem-test-frontend"


def _s3_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_BUCKET", BUCKET)
    monkeypatch.setenv("FRONTEND_PUBLIC_BASE", "http://example.com")
    s3 = boto3.client("s3", region_name=REGION)
    try:
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    except Exception:
        pass


def _post(path, headers, body):
    return handler(
        make_event("POST", path, headers=headers, body=json.dumps(body))
    )


def _error(resp) -> str:
    return json.loads(resp["body"]).get("error") or ""


@pytest.mark.unit
def test_slugify_respects_shared_limit_and_does_not_end_with_hyphen():
    assert MAX_SUBJECT_ID_LEN == 63
    assert SUBJECT_ID_PATTERN.match("a" * MAX_SUBJECT_ID_LEN)
    assert not SUBJECT_ID_PATTERN.match("a" * (MAX_SUBJECT_ID_LEN + 1))

    long_name = (
        "Photosynthesis and cellular respiration in tropical rainforest "
        "ecosystems extra words about energy transfer"
    )
    assert len(long_name) >= 95
    slug = slugify_subject_id("Science", long_name)
    assert len(slug) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(slug)
    assert not slug.endswith("-") and not slug.endswith("_")

    # The cut lands on a hyphen: mathematics- + ("ab-" * n)[50] == "-".
    hyphenated = slugify_subject_id("Mathematics", "ab-" * 40)
    assert len(hyphenated) <= MAX_SUBJECT_ID_LEN
    assert not hyphenated.endswith("-")
    assert SUBJECT_ID_PATTERN.match(hyphenated)

    prefix = "science-"
    exact = slugify_subject_id("Science", "a" * (MAX_SUBJECT_ID_LEN - len(prefix)))
    assert len(exact) == MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(exact)

    over = slugify_subject_id("Science", "a" * (MAX_SUBJECT_ID_LEN - len(prefix) + 5))
    assert len(over) == MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(over)
    assert not over.endswith("-")

    empty = slugify_subject_id("***", "???")
    assert empty == "subject"
    assert SUBJECT_ID_PATTERN.match(empty)


@pytest.mark.unit
def test_long_topic_name_saves_on_science_and_math(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    topic = (
        "Photosynthesis and cellular respiration in tropical rainforest "
        "ecosystems extra words about energy"
    )
    assert 90 <= len(topic) <= 100

    science = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": topic,
            "grade_level": "Grade 4",
            "summary": "Leaves capture sunlight.",
        },
    )
    assert science["statusCode"] == 201, science["body"]
    science_id = json.loads(science["body"])["subject_id"]
    assert len(science_id) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(science_id)
    assert not science_id.endswith("-")

    math = _post(
        "/subjects",
        admin_headers,
        {"category": "Mathematics", "topic": topic, "description": "Long math topic"},
    )
    assert math["statusCode"] == 201, math["body"]
    math_id = json.loads(math["body"])["subject_id"]
    assert len(math_id) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(math_id)
    assert not math_id.endswith("-")
    assert math_id != science_id


@pytest.mark.unit
def test_subject_id_exactly_at_limit_saves_and_over_limit_is_clear(
    dynamodb_table, admin_headers
):
    exact_id = "m" + "a" * (MAX_SUBJECT_ID_LEN - 1)
    assert len(exact_id) == MAX_SUBJECT_ID_LEN
    ok = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Mathematics",
            "topic": "Exact limit",
            "subject_id": exact_id,
        },
    )
    assert ok["statusCode"] == 201, ok["body"]
    assert json.loads(ok["body"])["subject_id"] == exact_id

    too_long = exact_id + "z"
    assert len(too_long) == MAX_SUBJECT_ID_LEN + 1
    bad = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Mathematics",
            "topic": "Too long",
            "subject_id": too_long,
        },
    )
    assert bad["statusCode"] in (400, 422)
    assert _error(bad) == f"Topic id must be at most {MAX_SUBJECT_ID_LEN} characters"

    invalid = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Mathematics",
            "topic": "Bad id",
            "subject_id": "Math!",
        },
    )
    assert invalid["statusCode"] in (400, 422)
    message = _error(invalid)
    assert "Topic id" in message
    assert str(MAX_SUBJECT_ID_LEN) in message


@pytest.mark.unit
def test_replace_with_defaults_keeps_grade_and_summary(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 6",
            "summary": "Magma rises and erupts.",
            "explainer_video_url": "https://www.youtube.com/watch?v=keepme",
        },
    )
    assert created["statusCode"] == 201, created["body"]

    # Omitted summary and null grade are the form defaults once the admin
    # has not edited those fields. main overwrote both.
    replaced = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": None,
            "replace": True,
        },
    )
    assert replaced["statusCode"] == 201, replaced["body"]
    body = json.loads(replaced["body"])
    assert body["grade_level"] == "Grade 6"
    assert body["summary"] == "Magma rises and erupts."
    assert body["explainer_video_url"] == "https://www.youtube.com/watch?v=keepme"


@pytest.mark.unit
def test_replace_with_explicit_values_updates_grade_and_summary(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 6",
            "summary": "Magma rises and erupts.",
            "explainer_video_url": "https://www.youtube.com/watch?v=keepme",
        },
    )
    assert created["statusCode"] == 201, created["body"]

    replaced = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 8",
            "summary": "A revised summary about magma and ash.",
            "explainer_video_url": "https://www.youtube.com/watch?v=newvideo",
            "replace": True,
        },
    )
    assert replaced["statusCode"] == 201, replaced["body"]
    body = json.loads(replaced["body"])
    assert body["grade_level"] == "Grade 8"
    assert body["summary"] == "A revised summary about magma and ash."
    assert body["explainer_video_url"] == "https://www.youtube.com/watch?v=newvideo"

    # Grade 3 is the upload form's untouched default. Sent on purpose, it still saves.
    chosen_default = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 3",
            "summary": "A revised summary about magma and ash.",
            "replace": True,
        },
    )
    assert chosen_default["statusCode"] == 201, chosen_default["body"]
    chosen = json.loads(chosen_default["body"])
    assert chosen["grade_level"] == "Grade 3"
    assert chosen["summary"] == "A revised summary about magma and ash."


@pytest.mark.unit
def test_technology_replace_keeps_grade_unless_explicit(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    pages = [
        {
            "index": 0,
            "title": "Intro",
            "text": "Hello robots",
            "image_ext": "jpg",
            "image_content_type": "image/jpeg",
        }
    ]
    created = _post(
        "/technology/topics",
        admin_headers,
        {
            "topic": "Robots",
            "grade_level": "Grade 6",
            "description": "How robots move.",
            "pages": pages,
        },
    )
    assert created["statusCode"] == 201, created["body"]
    assert json.loads(created["body"])["grade_level"] == "Grade 6"

    kept = _post(
        "/technology/topics",
        admin_headers,
        {"topic": "Robots", "replace": True, "pages": pages},
    )
    assert kept["statusCode"] == 201, kept["body"]
    assert json.loads(kept["body"])["grade_level"] == "Grade 6"

    changed = _post(
        "/technology/topics",
        admin_headers,
        {
            "topic": "Robots",
            "grade_level": "Grade 1",
            "replace": True,
            "pages": pages,
        },
    )
    assert changed["statusCode"] == 201, changed["body"]
    assert json.loads(changed["body"])["grade_level"] == "Grade 1"
