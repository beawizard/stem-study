"""Vedic PNVMO/IVMO paper parse + official scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.vedic import (
    MAX_SCORE,
    extract_docx_paragraphs,
    official_points_penalty,
    parse_vedic_mcq,
    score_vedic_item,
)

PAPER_B = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "vedic"
    / "Paper B - Primary  (11 years and under) - set1.docx"
)


@pytest.mark.unit
def test_official_points_penalty_bands():
    assert official_points_penalty(1) == (2, 0)
    assert official_points_penalty(25) == (2, 0)
    assert official_points_penalty(26) == (3, 1)
    assert official_points_penalty(35) == (3, 1)
    assert official_points_penalty(36) == (4, 2)
    assert official_points_penalty(40) == (4, 2)


@pytest.mark.unit
def test_score_blank_wrong_correct():
    ok = score_vedic_item(item_no=1, expected="B", given="B")
    assert ok["correct"] and ok["marks"] == 2
    skip = score_vedic_item(item_no=26, expected="B", given="")
    assert skip["skipped"] and skip["marks"] == 0
    wrong_mid = score_vedic_item(item_no=26, expected="B", given="A")
    assert not wrong_mid["correct"] and wrong_mid["marks"] == -1
    wrong_hard = score_vedic_item(item_no=36, expected="D", given="A")
    assert wrong_hard["marks"] == -2


@pytest.mark.unit
def test_parse_paper_b_docx():
    data = PAPER_B.read_bytes()
    paras = extract_docx_paragraphs(data)
    items = parse_vedic_mcq(paras)
    assert len(items) == 40
    assert items[0]["item_no"] == 1
    assert items[0]["answer"] == "B"
    assert items[0]["points"] == 2
    assert items[0]["penalty"] == 0
    assert {c["key"] for c in items[0]["choices"]} == set("ABCDE")
    assert items[25]["item_no"] == 26
    assert items[25]["points"] == 3
    assert items[25]["penalty"] == 1
    assert items[35]["item_no"] == 36
    assert items[35]["points"] == 4
    assert items[35]["penalty"] == 2
    assert items[39]["answer"] == "D"
    total = sum(it["points"] for it in items)
    assert total == MAX_SCORE


@pytest.mark.unit
def test_import_vedic_and_complete_session(dynamodb_table):
    from app.services import subject_service, study_service, user_service
    from app.validation import LevelCreate, SubjectCreate

    user_service.ensure_user_profile("u-vedic", nickname="Veda", grade="Grade 5")
    subject_service.create_subject(
        SubjectCreate(
            subject_id="vedic-primary",
            category="Mathematics",
            topic="Vedic Math Paper B",
            grade_level="Grade 5",
        )
    )
    subject_service.create_level(
        "vedic-primary",
        LevelCreate(level_id="set1", name="Paper B set 1", order=1),
    )
    summary = subject_service.import_questions(
        "vedic-primary",
        "set1",
        docx_bytes=PAPER_B.read_bytes(),
        replace=True,
    )
    assert summary["imported"] == 40
    assert summary["exam_kind"] == "vedic"
    level = subject_service.get_level("vedic-primary", "set1")
    assert level.get("exam_kind") == "vedic"
    assert int(level.get("time_limit_sec") or 0) == 3600

    sess = study_service.start_session("u-vedic", "vedic-primary", "set1")
    assert sess["exam_kind"] == "vedic"
    qs = sess["questions"]
    assert len(qs) == 40
    assert qs[0]["qtype"] == "mcq"
    assert qs[0]["choices"]
    assert qs[0]["answer"] == "B"

    answers = [
        {"question_id": q["question_id"], "answer": q["answer"]} for q in qs
    ]
    res = study_service.complete_session(
        "u-vedic", sess["session_id"], total_elapsed_ms=1_800_000, answers=answers
    )
    assert res["exam_kind"] == "vedic"
    assert res["score"] == 100
    assert res["correct"] == 40
    assert res["blank"] == 0
    assert res["passed"] is True

    # Retake: skip hard items, miss one easy
    sess2 = study_service.start_session("u-vedic", "vedic-primary", "set1")
    qs2 = sess2["questions"]
    answers2 = []
    for i, q in enumerate(qs2):
        n = i + 1
        if n == 1:
            answers2.append({"question_id": q["question_id"], "answer": "A"})  # wrong, 0
        elif n >= 26:
            answers2.append({"question_id": q["question_id"], "answer": ""})
        else:
            answers2.append({"question_id": q["question_id"], "answer": q["answer"]})
    res2 = study_service.complete_session(
        "u-vedic", sess2["session_id"], total_elapsed_ms=600_000, answers=answers2
    )
    # 24 easy correct * 2 = 48 (Q2-25), Q1 wrong 0 → 48; rest blank
    assert res2["score"] == 48
    assert res2["blank"] == 15
    assert res2["wrong"] == 1
    assert res2["correct"] == 24
