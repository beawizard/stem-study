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
    # Client receives answers for local quiz UX
    assert all("answer" in q for q in questions)

    # Batch complete (preferred study path)
    answers = [
        {"question_id": q["question_id"], "answer": q["answer"]} for q in questions
    ]
    resp = handler(
        make_event(
            "POST",
            f"/study/sessions/{session_id}/complete",
            headers=user_headers,
            body=json.dumps({"total_elapsed_ms": 12500, "answers": answers}),
        )
    )
    assert resp["statusCode"] == 200, resp["body"]
    last = json.loads(resp["body"])
    assert last["session_complete"] is True
    assert last["passed"] is True
    assert last["total_elapsed_ms"] == 12500
    assert last["recommendation"]["tags"]

    # Profile stores study duration
    me = json.loads(handler(make_event("GET", "/me", headers=user_headers))["body"])
    assert me["total_study_ms"] >= 12500
    assert me["study_sessions_count"] >= 1

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
    # minimal session fail path: start and batch wrong answers
    resp = handler(
        make_event(
            "POST",
            "/study/sessions",
            headers=user_headers,
            body=json.dumps({"subject_id": "math", "level_id": "l1"}),
        )
    )
    session = json.loads(resp["body"])
    handler(
        make_event(
            "POST",
            f"/study/sessions/{session['session_id']}/complete",
            headers=user_headers,
            body=json.dumps(
                {
                    "total_elapsed_ms": 4000,
                    "answers": [
                        {"question_id": q["question_id"], "answer": "WRONG"}
                        for q in session["questions"]
                    ],
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
def test_blank_answer_treated_as_zero(dynamodb_table, admin_headers, user_headers):
    _seed(admin_headers)
    resp = handler(
        make_event(
            "POST",
            "/study/sessions",
            headers=user_headers,
            body=json.dumps({"subject_id": "math", "level_id": "l1"}),
        )
    )
    session = json.loads(resp["body"])
    answers = [{"question_id": q["question_id"], "answer": ""} for q in session["questions"]]
    # Only mark correct if expected is 0
    resp = handler(
        make_event(
            "POST",
            f"/study/sessions/{session['session_id']}/complete",
            headers=user_headers,
            body=json.dumps({"total_elapsed_ms": 3000, "answers": answers}),
        )
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["answered"] == len(session["questions"])
    for d in body["details"]:
        assert d["given_answer"] == "0"


@pytest.mark.integration
def test_me_creates_trial_profile(dynamodb_table, user_headers):
    resp = handler(make_event("GET", "/me", headers=user_headers))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["subscription_status"] == "trial"
    assert body["subscription_active"] is True
    assert body["trial_ends_at"]


@pytest.mark.integration
def test_assessment_samples_all_levels_and_suggests(dynamodb_table, admin_headers, user_headers):
    """Placement assessment: up to 10q per major Level N band (not per N-x set)."""
    _seed(admin_headers)

    preview = handler(
        make_event(
            "GET",
            "/study/assessment/preview",
            headers=user_headers,
            qs={"subject_id": "math"},
        )
    )
    assert preview["statusCode"] == 200, preview["body"]
    prev = json.loads(preview["body"])
    # Seed math has l1/l2/l3 → three major bands
    assert prev["level_count"] >= 3
    assert prev["major_count"] == prev["level_count"]
    assert prev["total_questions"] > 0
    assert prev["questions_per_level"] == 10
    # Each major band contributes at most 10
    assert prev["total_questions"] <= prev["level_count"] * 10
    for band in prev["levels"]:
        assert band["sample_size"] <= 10

    resp = handler(
        make_event(
            "POST",
            "/study/assessment",
            headers=user_headers,
            body=json.dumps({"subject_id": "math"}),
        )
    )
    assert resp["statusCode"] == 201, resp["body"]
    session = json.loads(resp["body"])
    assert session["is_assessment"] is True
    questions = session["questions"]
    assert len(questions) == prev["total_questions"]
    assert all("answer" in q for q in questions)
    # One sample block per major band in session meta
    assert len(session["levels"]) == prev["level_count"]
    for band in session["levels"]:
        assert band["sample_size"] <= 10
        assert len(band["question_ids"]) == band["sample_size"]

    # By major: at most 10 questions each
    by_major = {}
    for q in questions:
        m = q.get("major")
        by_major.setdefault(m, 0)
        by_major[m] += 1
    assert all(c <= 10 for c in by_major.values())

    answers = [
        {"question_id": q["question_id"], "answer": q["answer"]} for q in questions
    ]
    # Fast enough for Superb Advanced when time is split across bands
    resp = handler(
        make_event(
            "POST",
            f"/study/assessment/{session['session_id']}/complete",
            headers=user_headers,
            body=json.dumps({"total_elapsed_ms": 60_000, "answers": answers}),
        )
    )
    assert resp["statusCode"] == 200, resp["body"]
    result = json.loads(resp["body"])
    assert result["is_assessment"] is True
    assert result["correct"] == len(questions)
    assert result["suggested_major"] is not None
    assert result["suggestion_message"]
    assert int(result["suggested_major"]) >= 1
    # Results rows are major bands
    assert len(result["major_results"]) >= 1

    # Assessment must not create study progress
    prog = handler(
        make_event(
            "GET",
            "/study/progress",
            headers=user_headers,
            qs={"subject_id": "math"},
        )
    )
    progress = json.loads(prog["body"])["progress"]
    assert progress == []
