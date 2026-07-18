"""Unit tests for subject/level services."""

import pytest

from app.services import subject_service
from app.services.subject_service import ConflictError, SubjectNotFound
from app.validation import LevelCreate, SubjectCreate


@pytest.mark.unit
def test_create_list_subject(dynamodb_table):
    s = subject_service.create_subject(
        SubjectCreate(subject_id="math", name="Mathematics", sort_order=1)
    )
    assert s["subject_id"] == "math"
    with pytest.raises(ConflictError):
        subject_service.create_subject(
            SubjectCreate(subject_id="math", name="Dup")
        )
    listed = subject_service.list_subjects()
    assert len(listed) == 1


@pytest.mark.unit
def test_level_and_csv(dynamodb_table):
    subject_service.create_subject(
        SubjectCreate(subject_id="math", name="Math")
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
