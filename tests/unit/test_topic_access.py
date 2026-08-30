"""Topic access/usage tracking for Admin."""

from __future__ import annotations

import json

import pytest

from app.handler import handler
from app.services import access_service, user_service
from app.services.subject_service import SubjectNotFound
from app.validation import SubjectCreate, TopicAccessPing
from app.services import subject_service
from tests.helpers import make_event


def _topic(subject_id="math-addition"):
    return subject_service.create_subject(
        SubjectCreate(
            subject_id=subject_id,
            category="Mathematics",
            topic="Addition",
            grade_level="Grade 2",
        )
    )


@pytest.mark.unit
def test_record_access_sets_first_last_and_accumulates(dynamodb_table):
    _topic()
    user_service.ensure_user_profile(
        "u-ada", email="ada@example.com", nickname="Ada", grade="Grade 4"
    )
    first = access_service.record_topic_access("u-ada", "math-addition", elapsed_ms=0)
    assert first["total_ms"] == 0
    assert first["first_access_at"]
    assert first["last_access_at"]
    assert first["nickname"] == "Ada"
    assert first["grade_level"] == "Grade 4"

    second = access_service.record_topic_access(
        "u-ada", "math-addition", elapsed_ms=90_000
    )
    assert second["total_ms"] == 90_000
    assert second["first_access_at"] == first["first_access_at"]
    assert second["last_access_at"] >= first["last_access_at"]

    third = access_service.record_topic_access(
        "u-ada", "math-addition", elapsed_ms=30_000
    )
    assert third["total_ms"] == 120_000
    listing = access_service.list_topic_usage("math-addition")
    assert listing["users"][0]["total_mins"] == 2.0
    assert listing["users"][0]["nickname"] == "Ada"


@pytest.mark.unit
def test_list_usage_ranks_by_minutes_desc(dynamodb_table):
    _topic()
    user_service.ensure_user_profile("u-hi", nickname="High", grade="Grade 6")
    user_service.ensure_user_profile("u-lo", nickname="Low", grade="Grade 3")
    access_service.record_topic_access("u-lo", "math-addition", elapsed_ms=60_000)
    access_service.record_topic_access("u-hi", "math-addition", elapsed_ms=180_000)
    rows = access_service.list_topic_usage("math-addition")["users"]
    assert [r["nickname"] for r in rows] == ["High", "Low"]
    assert rows[0]["rank"] == 1
    assert rows[1]["rank"] == 2
    assert rows[0]["total_mins"] == 3.0
    assert rows[1]["total_mins"] == 1.0


@pytest.mark.unit
def test_record_unknown_topic_raises(dynamodb_table):
    with pytest.raises(SubjectNotFound):
        access_service.record_topic_access("u-x", "no-such-topic", elapsed_ms=1000)


@pytest.mark.unit
def test_ping_caps_elapsed_ms():
    ping = TopicAccessPing(subject_id="math-addition", elapsed_ms=300_000)
    assert ping.elapsed_ms == 300_000
    with pytest.raises(Exception):
        TopicAccessPing(subject_id="math-addition", elapsed_ms=300_001)


@pytest.mark.unit
def test_admin_usage_api_forbidden_for_learner(dynamodb_table, user_headers, admin_headers):
    _topic()
    resp = handler(
        make_event("GET", "/admin/topics/math-addition/usage", headers=user_headers)
    )
    assert resp["statusCode"] == 403

    user_service.ensure_user_profile("user-111", nickname="Kid", grade="Grade 1")
    ping = handler(
        make_event(
            "POST",
            "/study/access",
            headers=user_headers,
            body=json.dumps({"subject_id": "math-addition", "elapsed_ms": 60000}),
        )
    )
    assert ping["statusCode"] == 200, ping["body"]

    listing = handler(
        make_event("GET", "/admin/topics/math-addition/usage", headers=admin_headers)
    )
    assert listing["statusCode"] == 200, listing["body"]
    body = json.loads(listing["body"])
    assert body["topic"] == "Addition"
    assert len(body["users"]) == 1
    assert body["users"][0]["nickname"] == "Kid"
    assert body["users"][0]["grade_level"] == "Grade 1"
    assert body["users"][0]["total_mins"] == 1.0
