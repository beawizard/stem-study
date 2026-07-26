"""Unit tests for subject/level services."""

import pytest

from app.services import subject_service
from app.services.subject_service import ConflictError, SubjectNotFound
from app.validation import LevelCreate, SubjectCreate


@pytest.mark.unit
def test_create_list_subject(dynamodb_table):
    s = subject_service.create_subject(
        SubjectCreate(
            subject_id="math",
            category="Mathematics",
            topic="Arithmetic",
            sort_order=1,
        )
    )
    assert s["subject_id"] == "math"
    assert s["category"] == "Mathematics"
    assert s["topic"] == "Arithmetic"
    assert s["label"] == "Mathematics - Arithmetic"
    with pytest.raises(ConflictError):
        subject_service.create_subject(
            SubjectCreate(subject_id="math", category="Mathematics", topic="Dup")
        )
    listed = subject_service.list_subjects()
    assert len(listed) == 1


@pytest.mark.unit
def test_create_subject_auto_id_and_level_tags(dynamodb_table):
    s = subject_service.create_subject(
        SubjectCreate(category="Science", topic="Biology Basics")
    )
    assert s["subject_id"] == "science-biology-basics"
    assert s["label"] == "Science - Biology Basics"
    lv = subject_service.create_level(
        s["subject_id"],
        LevelCreate(level_id="l1", name="Cells", order=1, min_questions=1),
    )
    assert lv["subject_id"] == s["subject_id"]
    assert lv["category"] == "Science"
    assert lv["topic"] == "Biology Basics"
    summary = subject_service.import_questions_csv(
        s["subject_id"], "l1", "1,+,1,=,2\n"
    )
    assert summary["imported"] == 1
    qs = subject_service.list_questions(s["subject_id"], "l1", include_answers=True)
    assert qs[0]["subject_id"] == s["subject_id"]
    assert qs[0]["category"] == "Science"
    assert qs[0]["topic"] == "Biology Basics"


@pytest.mark.unit
def test_content_notices_when_questions_cleared_after_progress(dynamodb_table):
    from app.services import study_service

    user_id = "learner-content-notice"
    subject_service.create_subject(
        SubjectCreate(
            subject_id="math",
            category="Mathematics",
            topic="Arithmetic",
            sort_order=1,
        )
    )
    subject_service.create_level(
        "math",
        LevelCreate(level_id="l1", name="Level 1", order=1, min_questions=1),
    )
    subject_service.import_questions_csv("math", "l1", "1,+,1,=,2\n2,+,2,=,4\n")
    session = study_service.start_session(user_id, "math", "l1")
    study_service.complete_session(
        user_id,
        session["session_id"],
        total_elapsed_ms=2000,
        answers=[
            {"question_id": q["question_id"], "answer": q["answer"]}
            for q in session["questions"]
        ],
    )
    assert study_service.list_content_notices(user_id) == []

    subject_service.clear_questions("math", "l1")
    notices = study_service.list_content_notices(user_id)
    assert len(notices) == 1
    assert notices[0]["change_type"] == "cleared"
    assert notices[0]["level_id"] == "l1"
    assert notices[0]["question_count"] == 0


@pytest.mark.unit
def test_content_notices_when_questions_updated_after_progress(dynamodb_table):
    from app.services import study_service

    user_id = "learner-content-update"
    subject_service.create_subject(
        SubjectCreate(
            subject_id="math",
            category="Mathematics",
            topic="Arithmetic",
            sort_order=1,
        )
    )
    subject_service.create_level(
        "math",
        LevelCreate(level_id="l1", name="Level 1", order=1, min_questions=1),
    )
    subject_service.import_questions_csv("math", "l1", "1,+,1,=,2\n")
    session = study_service.start_session(user_id, "math", "l1")
    study_service.complete_session(
        user_id,
        session["session_id"],
        total_elapsed_ms=1000,
        answers=[
            {"question_id": q["question_id"], "answer": q["answer"]}
            for q in session["questions"]
        ],
    )
    # New questions = content update
    subject_service.import_questions_csv(
        "math", "l1", "9,+,1,=,10\n8,+,2,=,10\n", replace=True
    )
    notices = study_service.list_content_notices(user_id)
    assert len(notices) == 1
    assert notices[0]["change_type"] == "updated"
    assert notices[0]["question_count"] == 2


@pytest.mark.unit
def test_update_subject_cascades_topic_to_levels_and_questions(dynamodb_table):
    from app.validation import SubjectUpdate

    s = subject_service.create_subject(
        SubjectCreate(
            subject_id="math-arith",
            category="Mathematics",
            topic="Arithmetic",
            description="old",
            sort_order=1,
        )
    )
    subject_service.create_level(
        s["subject_id"],
        LevelCreate(level_id="l1", name="Set 1", order=1, min_questions=1),
    )
    subject_service.import_questions_csv(s["subject_id"], "l1", "1,+,1,=,2\n")

    updated = subject_service.update_subject(
        s["subject_id"],
        SubjectUpdate(topic="Algebra", description="new desc"),
    )
    assert updated["topic"] == "Algebra"
    assert updated["label"] == "Mathematics - Algebra"
    assert updated["description"] == "new desc"

    levels = subject_service.list_levels(s["subject_id"])
    assert levels[0]["topic"] == "Algebra"
    assert levels[0]["subject_label"] == "Mathematics - Algebra"

    # Questions resolve topic from subject META (no per-question cascade)
    qs = subject_service.list_questions(s["subject_id"], "l1", include_answers=False)
    assert qs[0]["topic"] == "Algebra"
    assert qs[0]["category"] == "Mathematics"


@pytest.mark.unit
def test_level_and_csv(dynamodb_table):
    subject_service.create_subject(
        SubjectCreate(subject_id="math", category="Mathematics", topic="Math")
    )
    lv = subject_service.create_level(
        "math",
        LevelCreate(level_id="l1", name="L1", order=1, min_questions=2),
    )
    assert lv["level_id"] == "l1"
    summary = subject_service.import_questions_csv(
        "math", "l1", "1,+,1,=,2\n2,+,2,=,4\n"
    )
    assert summary["imported"] == 2
    qs = subject_service.list_questions("math", "l1", include_answers=True)
    assert len(qs) == 2
    assert qs[0]["answer"]


@pytest.mark.unit
def test_questions_preserve_csv_row_order(dynamodb_table):
    """Study must start at the first CSV/Excel row, not a random/last entry."""
    subject_service.create_subject(
        SubjectCreate(subject_id="math", category="Mathematics", topic="Math")
    )
    subject_service.create_level(
        "math",
        LevelCreate(level_id="l1", name="L1", order=1, min_questions=1),
    )
    # Distinct prompts so order is obvious
    csv_body = "\n".join(
        [
            "1,+,0,=,1",
            "2,+,0,=,2",
            "3,+,0,=,3",
            "4,+,0,=,4",
            "5,+,0,=,5",
        ]
    )
    subject_service.import_questions_csv("math", "l1", csv_body, replace=True)
    qs = subject_service.list_questions("math", "l1", include_answers=True)
    prompts = [q["prompt"] for q in qs]
    assert prompts == ["1+0", "2+0", "3+0", "4+0", "5+0"]
    assert [q["sort_order"] for q in qs] == [0, 1, 2, 3, 4]

    # Append keeps sequence after existing rows
    subject_service.import_questions_csv("math", "l1", "6,+,0,=,6\n", replace=False)
    qs2 = subject_service.list_questions("math", "l1", include_answers=True)
    assert [q["prompt"] for q in qs2] == ["1+0", "2+0", "3+0", "4+0", "5+0", "6+0"]


@pytest.mark.unit
def test_missing_subject(dynamodb_table):
    with pytest.raises(SubjectNotFound):
        subject_service.get_subject("nope")


@pytest.mark.unit
def test_seed_math(dynamodb_table):
    r1 = subject_service.seed_math_defaults()
    assert r1["subject_created"] is True
    r2 = subject_service.seed_math_defaults()
    assert r2["subject_created"] is False
    levels = subject_service.list_levels("math")
    assert len(levels) == 3
