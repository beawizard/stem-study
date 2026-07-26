"""Level N-x unlock rules: free within major band; gate next major."""

import pytest

from app.services import study_service, subject_service
from app.services.study_service import ProgressLocked, parse_level_group, level_major_number
from app.validation import LevelCreate, SubjectCreate


def test_parse_level_group_formats():
    assert parse_level_group("Level-1-0") == (1, 0)
    assert parse_level_group("Level-1-20") == (1, 20)
    assert parse_level_group({"level_id": "Level1-3", "name": "x"}) == (1, 3)
    assert parse_level_group({"level_id": "l2", "name": "Level 2 – Sub"}) == (2, 0)
    assert level_major_number("Level-2-5") == 2


@pytest.mark.unit
def test_within_level_n_variations_are_free(dynamodb_table):
    """Level 1-20 is allowed without completing Level 1-0 first."""
    user_id = "u-band"
    subject_service.create_subject(
        SubjectCreate(subject_id="math", category="Mathematics", topic="Arith", sort_order=1)
    )
    for lid, name, order in (
        ("Level-1-0", "Level-1-0", 1),
        ("Level-1-1", "Level-1-1", 2),
        ("Level-1-20", "Level-1-20", 3),
        ("Level-2-0", "Level-2-0", 4),
    ):
        subject_service.create_level(
            "math",
            LevelCreate(level_id=lid, name=name, order=order, min_questions=1),
        )
        subject_service.import_questions_csv("math", lid, "1,+,1,=,2\n")

    # Any Level 1-x without prior progress
    for lid in ("Level-1-0", "Level-1-1", "Level-1-20"):
        session = study_service.start_session(user_id, "math", lid)
        assert session["level_id"] == lid


@pytest.mark.unit
def test_level_m_requires_any_level_n_complete(dynamodb_table):
    user_id = "u-gate"
    subject_service.create_subject(
        SubjectCreate(subject_id="math", category="Mathematics", topic="Arith", sort_order=1)
    )
    for lid, order in (("Level-1-0", 1), ("Level-1-5", 2), ("Level-2-0", 3), ("Level-2-1", 4)):
        subject_service.create_level(
            "math",
            LevelCreate(level_id=lid, name=lid, order=order, min_questions=1),
        )
        subject_service.import_questions_csv("math", lid, "1,+,1,=,2\n")

    with pytest.raises(ProgressLocked):
        study_service.start_session(user_id, "math", "Level-2-0")
    with pytest.raises(ProgressLocked):
        study_service.start_session(user_id, "math", "Level-2-1")

    # Complete Level-1-5 only (not Level-1-0) → unlocks all Level 2-x
    s = study_service.start_session(user_id, "math", "Level-1-5")
    study_service.complete_session(
        user_id,
        s["session_id"],
        total_elapsed_ms=500,
        answers=[{"question_id": q["question_id"], "answer": q["answer"]} for q in s["questions"]],
    )

    for lid in ("Level-2-0", "Level-2-1"):
        session = study_service.start_session(user_id, "math", lid)
        assert session["level_id"] == lid
