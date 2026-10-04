"""Science topic create / list / get."""

from __future__ import annotations

import json

import boto3
import pytest

from app.handler import handler
from app.validation import ScienceTopicCreate
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


def _start_body(**overrides):
    body = {
        "topic": "Plants",
        "grade_level": "Grade 3",
        "summary": "Plants make food with sunlight.\nThey need water and air.",
        "explainer_video_url": "https://www.youtube.com/watch?v=abc123",
        "image_ext": "png",
        "image_content_type": "image/png",
        "replace": False,
    }
    body.update(overrides)
    return json.dumps(body)


@pytest.mark.unit
def test_science_topic_create_validation():
    s = ScienceTopicCreate(
        topic="Plants",
        summary="Green leaves.",
        explainer_video_url="https://example.com/video",
        image_ext="png",
    )
    assert s.topic == "Plants"
    assert s.explainer_video_url.startswith("https://")
    with pytest.raises(Exception):
        ScienceTopicCreate(topic="Plants", explainer_video_url="youtube.com/watch")
    with pytest.raises(Exception):
        ScienceTopicCreate(topic="")


@pytest.mark.unit
def test_science_topic_roundtrip(dynamodb_table, admin_headers, user_headers, monkeypatch):
    _s3_env(monkeypatch)

    resp = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=admin_headers,
            body=_start_body(),
        )
    )
    assert resp["statusCode"] == 201, resp["body"]
    started = json.loads(resp["body"])
    assert started["subject_id"] == "science-plants"
    assert started["image_put_url"]
    assert started["science_ready"] is False
    assert started["summary"].startswith("Plants make food")

    learner_post = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=user_headers,
            body=_start_body(topic="Animals"),
        )
    )
    assert learner_post["statusCode"] in (401, 403)

    conflict = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=admin_headers,
            body=_start_body(),
        )
    )
    assert conflict["statusCode"] == 400

    done = handler(
        make_event(
            "POST",
            "/science/topics/science-plants/complete",
            headers=admin_headers,
        )
    )
    assert done["statusCode"] == 200, done["body"]
    ready = json.loads(done["body"])
    assert ready["science_ready"] is True
    assert ready["explainer_video_url"] == "https://www.youtube.com/watch?v=abc123"
    assert "science/science-plants/cover.png" in ready["image_url"]

    listed = handler(make_event("GET", "/science/topics", headers=user_headers))
    assert listed["statusCode"] == 200
    topics = json.loads(listed["body"])["topics"]
    assert any(t["subject_id"] == "science-plants" and t["science_ready"] for t in topics)

    detail = handler(
        make_event("GET", "/science/topics/science-plants", headers=user_headers)
    )
    assert detail["statusCode"] == 200
    body = json.loads(detail["body"])
    assert body["topic"] == "Plants"
    assert body["grade_level"] == "Grade 3"
    assert "sunlight" in body["summary"]
    assert body["image_url"]
    assert body["explainer_video_url"].startswith("https://")


@pytest.mark.unit
def test_science_topic_requires_image(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    resp = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=admin_headers,
            body=_start_body(image_ext="", image_content_type=""),
        )
    )
    assert resp["statusCode"] == 400
    assert "image" in json.loads(resp["body"]).get("error", json.loads(resp["body"]).get("message", "")).lower() or "image" in resp["body"].lower()


@pytest.mark.unit
def test_science_topic_replace_without_new_image(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    first = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=admin_headers,
            body=_start_body(),
        )
    )
    assert first["statusCode"] == 201, first["body"]
    handler(
        make_event(
            "POST",
            "/science/topics/science-plants/complete",
            headers=admin_headers,
        )
    )
    again = handler(
        make_event(
            "POST",
            "/science/topics",
            headers=admin_headers,
            body=_start_body(
                summary="Updated summary about plants and energy.",
                image_ext="",
                image_content_type="",
                replace=True,
            ),
        )
    )
    assert again["statusCode"] == 201, again["body"]
    body = json.loads(again["body"])
    assert body["summary"].startswith("Updated summary")
    assert not body.get("image_put_url")
    assert "cover.png" in (body.get("image_url") or "")
