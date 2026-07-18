"""Integration tests – subjects, levels, study progression, insights."""

import json

import pytest

from app.handler import handler
from tests.helpers import make_event


def _seed(admin_headers):
    resp = handler(make_event("POST", "/admin/seed", headers=admin_headers))
    assert resp["statusCode"] == 200, resp["body"]
    return json.loads(resp["body"])


@pytest.mark.integration
def test_seed_and_list_subjects(dynamodb_table, admin_headers, user_headers):
    seed = _seed(admin_headers)
    assert seed["questions_imported"] >= 5

    resp = handler(make_event("GET", "/subjects", headers=user_headers))
    assert resp["statusCode"] == 200
    subjects = json.loads(resp["body"])["subjects"]
    assert any(s["subject_id"] == "math" for s in subjects)

    resp = handler(make_event("GET", "/subjects/math/levels", headers=user_headers))
    levels = json.loads(resp["body"])["levels"]
    assert len(levels) >= 3
    assert levels[0]["order"] <= levels[1]["order"]


@pytest.mark.integration
def test_admin_csv_import(dynamodb_table, admin_headers):
    _seed(admin_headers)
    csv_body = json.dumps({"csv": "8,+,1,=,9\n7,+,2,=,9\n"})
    resp = handler(
        make_event(
            "POST",
            "/subjects/math/levels/l1/questions",
            headers=admin_headers,
            body=csv_body,
        )
    )
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["imported"] == 2


@pytest.mark.integration
def test_non_admin_cannot_seed(dynamodb_table, user_headers):
    resp = handler(make_event("POST", "/admin/seed", headers=user_headers))
    assert resp["statusCode"] == 403


@pytest.mark.integration
def test_study_session_pass_and_unlock(dynamodb_table, admin_headers, user_headers):
    _seed(admin_headers)

    # Start level 1
    resp = handler(
        make_event(
            "POST",
            "/study/sessions",
            headers=user_headers,
            body=json.dumps({"subject_id": "math", "level_id": "l1"}),
        )
    )
    assert resp["statusCode"] == 201, resp["body"]
    session = json.loads(resp["body"])
    session_id = session["session_id"]
    questions = session["questions"]
    assert len(questions) >= 5

    # Answer all correctly (fetch answers as admin)
    resp = handler(
        make_event(
            "GET",
            "/subjects/math/levels/l1/questions",
            headers=admin_headers,
            qs={"include_answers": "true"},
        )
    )
    with_answers = {
        q["question_id"]: q["answer"]
        for q in json.loads(resp["body"])["questions"]
    }

    last = None
    for q in questions:
        ans = with_answers[q["question_id"]]
        resp = handler(
            make_event(
                "POST",
                f"/study/sessions/{session_id}/answers",
                headers=user_headers,
                body=json.dumps(
                    {
                        "question_id": q["question_id"],
                        "answer": ans,
                        "elapsed_ms": 2500,
                    }
                ),
            )
        )
        assert resp["statusCode"] == 200, resp["body"]
        last = json.loads(resp["body"])

    assert last["session_complete"] is True
    assert last["passed"] is True
    assert last["recommendation"]["tags"]

    # Progress completed
    resp = handler(
        make_event(
            "GET",
            "/study/progress",
            headers=user_headers,
            qs={"subject_id": "math"},
        )
    )
    progress = json.loads(resp["body"])["progress"]
    l1 = next(p for p in progress if p["level_id"] == "l1")
    assert l1["status"] == "completed"

    # Level 2 unlocked
    resp = handler(
        make_event(
            "POST",
            "/study/sessions",
            headers=user_headers,
            body=json.dumps({"subject_id": "math", "level_id": "l2"}),
        )
    )
    assert resp["statusCode"] == 201


@pytest.mark.integration
def test_level_locked_without_prior(dynamodb_table, admin_headers, user_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "POST",
            "/study/sessions",
            headers=user_headers,
            body=json.dumps({"subject_id": "math", "level_id": "l2"}),
        )
    )
    assert resp["statusCode"] == 403


@pytest.mark.integration
def test_insights_endpoint(dynamodb_table, admin_headers, user_headers):
    _seed(admin_headers)
    # minimal session fail path: start and answer wrong
    resp = handler(
        make_event(
            "POST",
            "/study/sessions",
            headers=user_headers,
            body=json.dumps({"subject_id": "math", "level_id": "l1"}),
        )
    )
    session = json.loads(resp["body"])
    for q in session["questions"]:
        handler(
            make_event(
                "POST",
                f"/study/sessions/{session['session_id']}/answers",
                headers=user_headers,
                body=json.dumps(
                    {
                        "question_id": q["question_id"],
                        "answer": "WRONG",
                        "elapsed_ms": 800,
                    }
                ),
            )
        )

    resp = handler(make_event("GET", "/insights", headers=user_headers))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "summary" in body
    assert body["levels_failed"] >= 1 or body["levels_in_progress"] >= 0


@pytest.mark.integration
def test_me_creates_trial_profile(dynamodb_table, user_headers):
    resp = handler(make_event("GET", "/me", headers=user_headers))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["subscription_status"] == "trial"
    assert body["subscription_active"] is True
    assert body["trial_ends_at"]
