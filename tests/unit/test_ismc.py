"""International Speed Math Challenge: Vedic layout, 1 point per correct answer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.vedic import (
    detect_exam_kind,
    extract_docx_paragraphs,
    ismc_time_limit_sec,
    parse_ismc_mcq,
    score_ismc_item,
    strip_trailing_asterisks,
)

_ISMC = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "ismc"
    / "ISMC_Level2_Set1.docx"
)
_ISMC_STEC = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "ismc"
    / "ISMC_STEC_Level2_Set1.docx"
)
_PAPER_B = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "vedic"
    / "Paper B - Primary  (11 years and under) - set1.docx"
)

_MINI = """LEVEL 1
10–12 years
SET 2
Time allowed: 12 minutes.  2 multiple-choice questions.  No calculator.
1.  1 + 1 =
A  1
B  2
C  3
D  4
E  5
[ans=B]
2.  2 + 2 =
A  2
B  3
C  4
D  5
E  6
[ans=C]
Answer Key
1B  2C
"""

_MINI_STAR = """LEVEL 1
10–12 years
SET 2
Time allowed: 12 minutes.  2 multiple-choice questions.  No calculator.
1.  What is the LCM of 12 and 18? *
A  18
B  24
C  36
D  48
E  72
[ans=C]
2.  Next square number: 25, 36, 49, ___
A  54
B  64
C  81
D  100
E  121
[ans=B]
Answer Key
1C  2B
"""


@pytest.mark.unit
def test_strip_trailing_asterisks_from_stems():
    assert strip_trailing_asterisks("What is 15% of 60? *") == "What is 15% of 60?"
    assert strip_trailing_asterisks("Next square number: 25, 36, 49, ___") == (
        "Next square number: 25, 36, 49, ___"
    )
    assert strip_trailing_asterisks("Simplify: 48 ÷ 6 × 3 + 5 – 8 **  ") == (
        "Simplify: 48 ÷ 6 × 3 + 5 – 8"
    )


@pytest.mark.unit
def test_parse_ismc_strips_trailing_asterisk_from_prompt():
    items = parse_ismc_mcq(_MINI_STAR.splitlines())
    assert len(items) == 2
    assert items[0]["prompt"] == "What is the LCM of 12 and 18?"
    assert not items[0]["prompt"].endswith("*")
    assert items[1]["prompt"] == "Next square number: 25, 36, 49, ___"


@pytest.mark.unit
def test_score_ismc_is_one_point_without_vedic_penalty():
    ok = score_ismc_item(expected="B", given="B")
    assert ok["correct"] and ok["marks"] == 1 and ok["penalty"] == 0
    wrong = score_ismc_item(expected="A", given="C", points=1)
    assert not wrong["correct"] and wrong["marks"] == 0 and wrong["penalty"] == 0
    blank = score_ismc_item(expected="A", given="")
    assert blank["skipped"] and blank["marks"] == 0


@pytest.mark.unit
def test_parse_ismc_level2_set1_docx():
    assert _ISMC.exists(), _ISMC
    paragraphs = extract_docx_paragraphs(_ISMC.read_bytes())
    assert detect_exam_kind(paragraphs) == "ismc"
    assert ismc_time_limit_sec(paragraphs) == 12 * 60
    items = parse_ismc_mcq(paragraphs)
    assert len(items) == 30
    assert items[0]["item_no"] == 1
    assert items[0]["answer"] == "B"
    assert items[0]["prompt"].startswith("367")
    assert {c["key"] for c in items[0]["choices"]} == set("ABCDE")
    assert items[25]["item_no"] == 26
    assert items[25]["answer"] == "A"
    assert items[-1]["item_no"] == 30
    assert items[-1]["answer"] == "A"
    assert all(it["points"] == 1 and it["penalty"] == 0 for it in items)
    assert sum(it["points"] for it in items) == 30


@pytest.mark.unit
def test_parse_ismc_stec_docx_strips_trailing_asterisks():
    assert _ISMC_STEC.exists(), _ISMC_STEC
    paragraphs = extract_docx_paragraphs(_ISMC_STEC.read_bytes())
    items = parse_ismc_mcq(paragraphs)
    assert len(items) == 30
    assert items[0]["prompt"] == "What is the LCM of 12 and 18?"
    assert all(not str(it["prompt"]).rstrip().endswith("*") for it in items)


@pytest.mark.unit
def test_vedic_paper_is_not_classified_as_ismc():
    paragraphs = extract_docx_paragraphs(_PAPER_B.read_bytes())
    assert detect_exam_kind(paragraphs) == "vedic"


@pytest.mark.unit
def test_import_ismc_and_complete_session(dynamodb_table):
    from app.services import subject_service, study_service, user_service
    from app.validation import LevelCreate, SubjectCreate

    user_service.ensure_user_profile("u-ismc", nickname="Speedy", grade="Grade 6")
    subject_service.create_subject(
        SubjectCreate(
            subject_id="ismc",
            category="Mathematics",
            topic="International Speed Math Challenge",
            grade_level="Grade 6",
        )
    )
    subject_service.create_level(
        "ismc",
        LevelCreate(level_id="l2-set1", name="Level 2 Set 1", order=201),
    )
    summary = subject_service.import_questions(
        "ismc",
        "l2-set1",
        docx_bytes=_ISMC.read_bytes(),
        replace=True,
    )
    assert summary["imported"] == 30
    assert summary["exam_kind"] == "ismc"
    level = subject_service.get_level("ismc", "l2-set1")
    assert level.get("exam_kind") == "ismc"
    assert int(level.get("time_limit_sec") or 0) == 720
    assert float(level.get("pass_accuracy") or 0) == 0.8

    sess = study_service.start_session("u-ismc", "ismc", "l2-set1")
    assert sess["exam_kind"] == "ismc"
    assert int(sess["time_limit_sec"]) == 720
    qs = sess["questions"]
    assert len(qs) == 30
    assert qs[25]["points"] == 1
    assert qs[25]["penalty"] == 0

    perfect = [
        {"question_id": q["question_id"], "answer": q["answer"]} for q in qs
    ]
    res = study_service.complete_session(
        "u-ismc", sess["session_id"], total_elapsed_ms=600_000, answers=perfect
    )
    assert res["exam_kind"] == "ismc"
    assert res["score"] == 30
    assert res["max_score"] == 30
    assert res["correct"] == 30
    assert res["blank"] == 0
    assert res["wrong"] == 0
    assert res["passed"] is True
    assert res["speed_badge"] is None

    sess2 = study_service.start_session("u-ismc", "ismc", "l2-set1")
    qs2 = sess2["questions"]
    mixed = []
    for i, q in enumerate(qs2):
        n = i + 1
        if n == 26:
            mixed.append({"question_id": q["question_id"], "answer": "B"})
        elif n == 30:
            mixed.append({"question_id": q["question_id"], "answer": ""})
        else:
            mixed.append({"question_id": q["question_id"], "answer": q["answer"]})
    res2 = study_service.complete_session(
        "u-ismc", sess2["session_id"], total_elapsed_ms=500_000, answers=mixed
    )
    assert res2["score"] == 28
    assert res2["correct"] == 28
    assert res2["wrong"] == 1
    assert res2["blank"] == 1
    q26 = next(d for d in res2["details"] if d["item_no"] == 26)
    assert q26["marks"] == 0
    assert q26["penalty"] == 0
    assert q26["correct"] is False


@pytest.mark.unit
def test_ismc_sets_can_be_started_in_any_order(dynamodb_table):
    from app.services import subject_service, study_service
    from app.validation import LevelCreate, SubjectCreate

    sid = "ismc-any-order"
    subject_service.create_subject(
        SubjectCreate(
            subject_id=sid,
            category="Mathematics",
            topic="International Speed Math Challenge",
            grade_level="Grade 6",
        )
    )
    for n, name in ((1, "Level 2 Set 1"), (2, "Level 2 Set 2")):
        subject_service.create_level(
            sid,
            LevelCreate(level_id=f"l2-set{n}", name=name, order=200 + n),
        )
        summary = subject_service.import_questions(sid, f"l2-set{n}", text=_MINI, replace=True)
        assert summary["exam_kind"] == "ismc"
        assert summary["imported"] == 2

    for lid in ("l2-set2", "l2-set1"):
        sess = study_service.start_session("u-ismc-order", sid, lid)
        assert sess["level_id"] == lid
        assert sess["exam_kind"] == "ismc"
        assert int(sess["time_limit_sec"]) == 720
