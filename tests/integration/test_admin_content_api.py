"""Admin content management: update/delete questions and levels."""

import json

import pytest

from app.handler import handler
from tests.helpers import make_event


def _seed(admin_headers):
    resp = handler(make_event("POST", "/admin/seed", headers=admin_headers))
    assert resp["statusCode"] == 200, resp["body"]


@pytest.mark.integration
def test_update_and_delete_question(dynamodb_table, admin_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "GET",
            "/subjects/math/levels/l1/questions",
            headers=admin_headers,
            qs={"include_answers": "true"},
        )
    )
    questions = json.loads(resp["body"])["questions"]
    assert questions
    qid = questions[0]["question_id"]

    resp = handler(
        make_event(
            "PUT",
            f"/subjects/math/levels/l1/questions/{qid}",
            headers=admin_headers,
            body=json.dumps({"prompt": "9+1", "answer": "10"}),
        )
    )
    assert resp["statusCode"] == 200, resp["body"]
    body = json.loads(resp["body"])
    assert body["prompt"] == "9+1"
    assert body["answer"] == "10"

    resp = handler(
        make_event(
            "DELETE",
            f"/subjects/math/levels/l1/questions/{qid}",
            headers=admin_headers,
        )
    )
    assert resp["statusCode"] == 204

    resp = handler(
        make_event(
            "GET",
            "/subjects/math/levels/l1/questions",
            headers=admin_headers,
            qs={"include_answers": "true"},
        )
    )
    ids = {q["question_id"] for q in json.loads(resp["body"])["questions"]}
    assert qid not in ids


@pytest.mark.integration
def test_clear_questions_and_replace_csv(dynamodb_table, admin_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "DELETE",
            "/subjects/math/levels/l1/questions",
            headers=admin_headers,
        )
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["question_count"] == 0

    resp = handler(
        make_event(
            "POST",
            "/subjects/math/levels/l1/questions",
            headers=admin_headers,
            qs={"replace": "true"},
            body=json.dumps({"csv": "1,+,1,=,2\n2,+,2,=,4\n"}),
        )
    )
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["imported"] == 2
    assert body["question_count"] == 2
    assert body["replaced"] is True


@pytest.mark.integration
def test_update_subject_topic_and_description(dynamodb_table, admin_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "PUT",
            "/subjects/math",
            headers=admin_headers,
            body=json.dumps(
                {
                    "topic": "Number Sense",
                    "description": "Updated description",
                }
            ),
        )
    )
    assert resp["statusCode"] == 200, resp["body"]
    body = json.loads(resp["body"])
    assert body["topic"] == "Number Sense"
    assert body["label"] == "Mathematics - Number Sense"
    assert body["description"] == "Updated description"

    # Impact: levels list tags cascade
    resp = handler(
        make_event("GET", "/subjects/math/levels", headers=admin_headers)
    )
    assert resp["statusCode"] == 200
    levels = json.loads(resp["body"])["levels"]
    assert levels
    assert levels[0]["topic"] == "Number Sense"
    assert levels[0]["subject_label"] == "Mathematics - Number Sense"


@pytest.mark.integration
def test_update_and_delete_level(dynamodb_table, admin_headers, user_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "PUT",
            "/subjects/math/levels/l2",
            headers=admin_headers,
            body=json.dumps({"name": "Subtraction (revised)", "pass_accuracy": 0.9}),
        )
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["name"] == "Subtraction (revised)"
    assert body["pass_accuracy"] == 0.9

    resp = handler(
        make_event(
            "DELETE",
            "/subjects/math/levels/l2",
            headers=admin_headers,
        )
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["deleted"] is True

    resp = handler(make_event("GET", "/subjects/math/levels", headers=user_headers))
    level_ids = {lv["level_id"] for lv in json.loads(resp["body"])["levels"]}
    assert "l2" not in level_ids


@pytest.mark.integration
def test_non_admin_cannot_delete_level(dynamodb_table, admin_headers, user_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "DELETE",
            "/subjects/math/levels/l1",
            headers=user_headers,
        )
    )
    assert resp["statusCode"] == 403
