"""Unit tests for recommendation rules and insights aggregation."""

from app.services import subject_service, study_service
from app.services.insights_service import build_recommendation, learner_insights
from app.validation import LevelCreate, SubjectCreate


def test_mastery_fast_accurate():
    r = build_recommendation(
        accuracy=0.95,
        avg_ms_per_question=2000,
        passed=True,
        subject_id="math",
        level_id="l1",
        user_id="u",
    )
    assert "mastery" in r["tags"]
    assert r["passed"] is True
    assert r["actions"]


def test_rushed_low_accuracy():
    r = build_recommendation(
        accuracy=0.4,
        avg_ms_per_question=1000,
        passed=False,
        subject_id="math",
        level_id="l1",
        user_id="u",
    )
    assert "needs_review" in r["tags"]
    assert "rushed" in r["tags"]


def test_almost_there():
    r = build_recommendation(
        accuracy=0.7,
        avg_ms_per_question=10000,
        passed=False,
        subject_id="math",
        level_id="l2",
        user_id="u",
    )
    assert "almost_there" in r["tags"]


def test_slow_but_accurate():
    r = build_recommendation(
        accuracy=0.92,
        avg_ms_per_question=45000,
        passed=True,
        subject_id="math",
        level_id="l1",
        user_id="u",
    )
    assert "careful_mastery" in r["tags"]


def test_insights_hides_progress_for_deleted_levels(dynamodb_table):
    """Soft-deleted levels (e.g. seed l1 / old Level1-0) must not appear in Insights."""
    user_id = "user-insights-orphan"
    subject_service.create_subject(
        SubjectCreate(subject_id="math", name="Math", description="", sort_order=1)
    )
    subject_service.create_level(
        "math",
        LevelCreate(
            level_id="Level-1-0",
            name="Level-1-0",
            order=1,
            pass_accuracy=0.8,
            min_questions=1,
        ),
    )
    subject_service.create_level(
        "math",
        LevelCreate(
            level_id="l1",
            name="Old seed level",
            order=2,
            pass_accuracy=0.8,
            min_questions=1,
        ),
    )
    subject_service.import_questions_csv("math", "Level-1-0", "1,+,1,=,2\n")
    subject_service.import_questions_csv("math", "l1", "2,+,2,=,4\n")

    # Create progress on both levels via sessions
    for lid in ("Level-1-0", "l1"):
        session = study_service.start_session(user_id, "math", lid)
        study_service.complete_session(
            user_id,
            session["session_id"],
            total_elapsed_ms=1000,
            answers=[
                {"question_id": q["question_id"], "answer": q["answer"]}
                for q in session["questions"]
            ],
        )

    # Soft-delete orphan level (keeps progress rows in DB)
    subject_service.soft_delete_level("math", "l1")

    data = learner_insights(user_id, "math")
    level_ids = {p["level_id"] for p in data["progress"]}
    assert "Level-1-0" in level_ids
    assert "l1" not in level_ids
    assert data["levels_completed"] == 1
