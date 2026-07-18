"""Unit tests for validation helpers."""

import pytest

from app.validation import (
    AnswerSubmit,
    LevelCreate,
    PaymentSubmit,
    SubjectCreate,
    TaskCreate,
    TaskUpdate,
    parse_body,
    parse_csv_questions,
)


def test_task_create_ok():
    t = TaskCreate(title="Review fractions", description="Ch 3")
    assert t.title == "Review fractions"
    assert t.subject_id is None


def test_task_create_strips_and_rejects_extra():
    t = TaskCreate(title="  Hello  ")
    assert t.title == "Hello"
    with pytest.raises(Exception):
        TaskCreate(title="x", unknown_field=1)


def test_subject_id_format():
    SubjectCreate(subject_id="math", name="Math")
    with pytest.raises(Exception):
        SubjectCreate(subject_id="Math!", name="Bad")
    with pytest.raises(Exception):
        SubjectCreate(subject_id="1math", name="Bad")


def test_level_create_bounds():
    LevelCreate(level_id="l1", name="One", order=1, pass_accuracy=0.8)
    with pytest.raises(Exception):
        LevelCreate(level_id="l1", name="One", order=0)
    with pytest.raises(Exception):
        LevelCreate(level_id="l1", name="One", order=1, pass_accuracy=1.5)


def test_answer_submit():
    a = AnswerSubmit(question_id="abc", answer="3", elapsed_ms=1200)
    assert a.elapsed_ms == 1200
    with pytest.raises(Exception):
        AnswerSubmit(question_id="abc", answer="3", elapsed_ms=-1)


def test_payment_submit():
    p = PaymentSubmit(gcash_reference="GC123456", amount_php=99.0)
    assert p.amount_php == 99.0
    with pytest.raises(Exception):
        PaymentSubmit(gcash_reference="bad ref!", amount_php=10)


def test_parse_body_invalid_json():
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_body(TaskCreate, "{not json")


def test_parse_body_validation_error():
    with pytest.raises(ValueError):
        parse_body(TaskCreate, "{}")


def test_parse_body_ok():
    obj = parse_body(TaskCreate, '{"title":"Do homework"}')
    assert obj.title == "Do homework"


def test_parse_csv_arithmetic_format():
    rows = parse_csv_questions("1,+,2,=,3\n4,+,5,=,9\n")
    assert len(rows) == 2
    assert rows[0]["prompt"] == "1+2"
    assert rows[0]["answer"] == "3"


def test_parse_csv_qa_format():
    rows = parse_csv_questions("1+2,3\nquestion,answer\n9-4,5\n")
    assert len(rows) == 2
    assert rows[0]["prompt"] == "1+2"
    assert rows[1]["answer"] == "5"


def test_parse_csv_empty():
    with pytest.raises(ValueError, match="empty"):
        parse_csv_questions("  \n")


def test_parse_csv_bad_row():
    with pytest.raises(ValueError, match="Row"):
        parse_csv_questions("onlyone\n")


def test_task_update_partial():
    u = TaskUpdate(completed=True)
    assert u.model_dump(exclude_unset=True) == {"completed": True}
