"""Unit tests for lightweight Study landing payload."""

import pytest

from app.services import study_service, subject_service
from app.validation import LevelCreate, SubjectCreate


@pytest.mark.unit
def test_study_landing_includes_levels_progress_and_radar_rows(dynamodb_table):
    subject_service.create_subject(
        SubjectCreate(
            subject_id="math",
            category="Mathematics",
            topic="Arithmetic (Addition) - Level 1",
            sort_order=1,
        )
    )
    subject_service.create_subject(
        SubjectCreate(
            subject_id="mathematics-arithmetic-addition-level-2",
            category="Mathematics",
            topic="Arithmetic (Addition) - Level 2",
            sort_order=2,
        )
    )
    subject_service.create_level(
        "math",
        LevelCreate(level_id="l1-0", name="Level 1-0", order=1, min_questions=1),
    )
    subject_service.create_level(
        "mathematics-arithmetic-addition-level-2",
        LevelCreate(level_id="l2-0", name="Level 2-0", order=1, min_questions=1),
    )
    subject_service.import_questions_csv("math", "l1-0", "1,+,1,=,2\n")
    subject_service.import_questions_csv(
        "mathematics-arithmetic-addition-level-2", "l2-0", "2,+,2,=,4\n"
    )

    user_id = "learner-landing"
    session = study_service.start_session(user_id, "math", "l1-0")
    study_service.complete_session(
        user_id,
        session["session_id"],
        total_elapsed_ms=5000,
        answers=[{"question_id": q["question_id"], "answer": q["answer"]} for q in session["questions"]],
    )

    landing = study_service.study_landing(user_id, "math")
    assert landing["subject_id"] == "math"
    assert landing["base_topic"] == "Arithmetic (Addition)"
    assert "math" in landing["subject_ids"]
    assert "mathematics-arithmetic-addition-level-2" in landing["subject_ids"]
    assert len(landing["levels"]) == 1
    assert landing["levels"][0]["level_id"] == "l1-0"
    assert any(p["level_id"] == "l1-0" and p["status"] == "completed" for p in landing["progress"])
    # Radar rows cover both majors in the base topic
    rows = landing["progress_rows"]
    assert any(r["subject_id"] == "math" and r["level_id"] == "l1-0" for r in rows)
    assert any(
        r["subject_id"] == "mathematics-arithmetic-addition-level-2" and r["status"] == "new"
        for r in rows
    )


@pytest.mark.unit
def test_study_bootstrap_batches_subjects_and_landing(dynamodb_table):
    subject_service.create_subject(
        SubjectCreate(
            subject_id="math",
            category="Mathematics",
            topic="Arithmetic (Addition) - Level 1",
            sort_order=1,
        )
    )
    subject_service.create_level(
        "math",
        LevelCreate(level_id="l1-0", name="Level 1-0", order=1, min_questions=1),
    )
    subject_service.import_questions_csv("math", "l1-0", "1,+,1,=,2\n")

    boot = study_service.study_bootstrap("learner-boot", "math")
    assert len(boot["subjects"]) >= 1
    assert any(s["subject_id"] == "math" for s in boot["subjects"])
    assert boot["landing"] is not None
    assert boot["landing"]["subject_id"] == "math"
    assert len(boot["landing"]["levels"]) == 1

    # Default subject when omitted
    boot2 = study_service.study_bootstrap("learner-boot", None)
    assert boot2["landing"] is not None
    assert boot2["landing"]["subject_id"] == "math"
